---
code_file: src/narranexus/platform/agent_runtime/run_registry.py
last_verified: 2026-08-22
stub: false
---

# run_registry.py — 按 (agent, surface) 索引活 run 的路由大脑

live-steering 的路由判据:producer 拿到发给某 agent 的新消息,问"这 agent 在**这个 surface** 上有活 run 吗"。
有→steer 进那个 run(下个 step 边界追加进上下文),没有→照旧新起 turn。

**surface-scoped 是命门**:一个 agent 可能同时多个并发 run(多 team 房间 / web 单聊 / job),一条消息只能
进同 surface 的 run——teamA 消息绝不能拼进 teamB turn 的上下文(反串台)。key 是 `(agent_id, surface_key)`。

**为何进程内、内存、不落表**:run 的 steer 句柄(往它 loop 里推的活通道)只存在于**拥有该 run 的进程**;
v1 两个 producer 都与目标 run 同进程——team 消息与 team run 都在 bus-trigger 进程,单聊插话与 web run 都
在 backend 进程。所以"谁产就谁持有 run"。这是 seam(铁律 #20,同 `get_admission_controller`):将来若有
跨进程 producer,接口不变、真相源可迁 Redis/DB view。

## 数据结构与不变量

- 双索引:`_by_run[run_id]→RunHandle`、`_by_surface[(agent,surface)]→run_id`。
- `register`:一个 surface 最多一个活 run;同 (agent,surface) 重复 register **顶替**旧的(旧 run_id 映射被覆盖)。
- `live_run(agent,surface)`:经 surface 索引取回 handle;不同 surface / 不同 agent 天生不匹配(key 就是那对)。
- `release(run_id)`:pop `_by_run` 后**无条件**清 surface 映射(pop 早退掉 `handle is None`)。被顶替的旧
  run 在顶替时已从 `_by_run` 移除(见 register 顶替 pop),故其迟到 release 在 `handle is None` 早退、够不到
  surface——不再需要 `== run_id` 守卫(持有活 handle 时该守卫恒真,见 2026-08-22 条目)。
- `run_id` 不透明(同 steer_inbox):谁 register 谁铸句柄,不必是 late-bind 的 events.event_id;registry 不解释它。
- 同步方法、单事件循环:dict 读改写间无 await 故原子;`get_run_registry()` 进程单例,天然按 owner 分区。

## 放置说明

`run_registry.py` / `steer_channel.py` 平铺进 `agent_runtime/` 而非开 `steering/` 子包:`RunRegistry` 是
通用的"活 run 路由"原语(不止 steering 用),`agent_runtime/` 本就偏平(admission/background_run/client 等
平铺)。若将来 steering 相关文件增多再收成子包。

## 2026-08-21(补)— review 加固:作用域 API + 顶替不泄漏 + 生存性兜底

`registered()` contextmanager(finally release,调用方没法忘);`register` 顶替旧 run 时 pop 旧 `_by_run`
条目(否则泄漏一个 SteerChannel);`RunHandle.is_alive` 可选**同步**探针,`live_run` 命中后校验、死 run 就
清映射并当作无活 run——「忘 release/崩溃」退化成「多起一个新 turn」而非「该 surface 永久失聪」(事故教训
#4/#5)。register/release/扫死 run 各写一行 `logger.info`(**不是** debug:默认级别就是 INFO,debug 在
dev/prod 一行不落,那 release/sweep 就没有对应的"run 何时起"记录——事故教训 #5;且这是**每 run 一次**的生命周期
事件,不是每消息,量可接受;live_run 正常路径保持沉默,别提级)。生命周期落应用日志而非独立审计表——单进程
内存态,进程死则 run 全灭,无跨重启审计需求。is_alive 必须同步(live_run 靠读改写间无 await 的原子性)。

**下游硬性要求(非可选)**:`is_alive` 形式上默认 `None`(=assume alive)是 seam-first 取舍,但**生产路径每个
run 都必须传**——它是"崩溃/忘 release 的 run 不让 surface 永久失聪"的唯一自愈来源。bus 编排 PR / 单聊插话 /
IM producer 建 run 时都要传探针;不传就只剩 `registered()` 的 `finally` 一道防线(仅覆盖正常/异常退出,盖不住
硬崩溃)。读 `= None` 别当"可选",当"这一层没接上"。

## 2026-08-22(补)— review 收敛:release 去守卫 + is_alive 双向覆盖

方案(b):`release` 简化为**无条件**清 surface 映射(保留 `handle is None` 早退),删掉原
`if self._by_surface.get(key) == run_id` 守卫并同步 docstring。理由:顶替 pop 落地(2026-08-21)后,被顶替的
旧 run_id 已不在 `_by_run`,其迟到 release 在 `handle is None` 早退,够不到 surface;而只要 run_id 仍在
`_by_run`,surface 必指向它(register 同时写两索引,唯一改 `_by_surface[key]` 的是同 key 再 register——那会先
pop 掉本 run_id——或 release 本身)。故守卫恒真=死代码。回归 `test_releasing_a_superseded_run_does_not_evict_the_current_one`
仍绿(走的是 `handle is None` 早退路径)。

is_alive 此前只测了「探针说死→扫掉」,补齐反向:`test_live_run_keeps_a_run_its_probe_reports_alive`
(说活→handle 原样返回、surface 保留)+ `test_registering_after_a_sweep_establishes_a_fresh_mapping`
(扫掉后同 surface 能正常重建映射、无残留)。

`live_run` 再补一条自愈:`handle is None`(surface 指向一个已不在 `_by_run` 的 run_id——同 run_id 先后 register
到两个 surface、release 只清自己那个 key 时会留下这种悬空映射)时,顺手 `del` 掉那条悬空 `_by_surface`。行为本
就正确(返回 None),但把"自愈"补全,少一个后人要推理的死映射边界。`registered()` + 顶替 的组合也补了用例
(body 执行中被顶替,退出 `finally: release` 是 no-op、不踢顶替者)。
