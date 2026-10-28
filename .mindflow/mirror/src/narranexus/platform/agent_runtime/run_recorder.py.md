---
code_file: src/narranexus/platform/agent_runtime/run_recorder.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（GH #127 / B-05）— FAILED 行也保留已流出的 final_output

- STATE_FAILED 分支本身一直正确写 `error_message`；漏洞在调用方从不带 `had_fatal_error=True` 的 run
  调 `finalize(STATE_FAILED, ...)`（见 [[client]]/[[background_run]] 同日条目）。原来那条
  "deliberate asymmetry" 注释描述的正是被修掉的旧行为，已改写。
- `final_output` 兜底的门从 `state == STATE_COMPLETED` 改为 `state != STATE_CANCELLED`：调用方现在会把
  「已流出正文后才撞上 fatal」的 run（例如 claude_code 中途 401，那条分支仍无条件 fatal）落成
  FAILED，旧门会让这种行只剩 error_message、丢掉用户其实已看到的回复。cancelled 行照旧不写。
  「never overwrite a non-empty value」的读-改-写原样保留（step_4 写过的值不被覆盖）。

测试：`tests/agent_runtime/test_run_recorder.py::test_finalize_failed_keeps_output_streamed_before_the_failure`
（failed 保留、cancelled 不写）。

## 2026-09-10（PR #394 review 第四轮 I-1）— recorder 替探测认领者报出 run id

`RunRecorder(..., agent_id=None, probe_token=None)`：run 携带半开探测认领时，`_bind_run_id`
在「running init」那次写之后调 [[circuit_breaker]] `bind_probe_run`，把本 run 的 event_id
记为认领者（token CAS，永不抛）。普通 run 两个参数都是 None，什么都不写。这是认领身份唯一的
落点——WS/openai（[[background_run]]）与触发路径（[[client]] `_new_recorder`）都经过它。
`sweep_stale_runs` 调 `release_orphaned_probe` 的判定随之变成「被绑定的那条 run 是否还活着」。

## 2026-09-10（PR #394 第二轮 review I-4）— 活性规则只剩一条 import 路径

本模块不再 re-export 活性规则：只为自身使用导入 `HEARTBEAT_INTERVAL_S` / `STATE_RUNNING` /
`run_is_live`，`__all__` 去掉了 `HEARTBEAT_INTERVAL_S` / `RUN_STALE_AFTER_S` / `STATE_RUNNING` /
`parse_db_utc` / `run_is_live`。所有调用方（backend main/auth/websocket/runs、cancel_watcher、
patrol、errand、测试）直接从 [[run_liveness]] 导入。状态机常量 `STATE_COMPLETED` /
`STATE_CANCELLED` / `STATE_FAILED` / `TERMINAL_STATES` 仍住在本模块。门禁：
`test_run_recorder.py::test_the_liveness_rule_has_one_import_path`（扫 src/backend/tests/scripts
里从 run_recorder / background_run 导入活性名字的语句，含多行括号导入）。

## 2026-09-10（PR #394 review I1/I4）— 活性规则搬到叶子模块；清扫改调 `release_orphaned_probe`

`HEARTBEAT_INTERVAL_S` / `RUN_STALE_AFTER_S` / `STATE_RUNNING` / `parse_db_utc` /
`run_is_live` 移到 [[run_liveness]]（`utils` 叶子）。（最初本模块原样 re-export；第二轮 review I-4 已删掉
re-export，见上一节。）熔断器改从叶子模块导入，于是本模块对 [[circuit_breaker]] 的导入成了普通的模块级
下行导入——原来两边各一个函数内 lazy import 互相掩护一个 loop↔runtime 环，已拆掉。

`sweep_stale_runs` 每翻一行（failed 或 cancelled）对该 `agent_id` 调
`release_orphaned_probe`：丢失的 run 带着 token 死了，只能靠时间判身份——行是 PROBING 且
没有「认领之后才开始」的存活 run 时才归还（同 agent 更早就在跑的长 run 不再能把行焊在
PROBING）。锁：`test_sweep_releases_the_lost_runs_half_open_probe`、
`test_sweep_releases_a_lost_probe_despite_an_older_live_run`、
`test_breaker_and_sweep_share_one_liveness_rule_without_a_cycle`。

## 2026-08-30 — thinking segment 带上档位，且 segment 也必须 tier 纯净

followups #1 折入本单。此前这条回放路（recorder → [[broadcaster]] →
[[wsManager]] → [[useRunObservation]]）全程不带档位，后果有两个：单聊
**运行中刷新**时重放的前半截退回 receded，团队面板的 live 视图**从来**没有
过档位。

两处改动：

- **`_append_to_segment(text, is_monologue)`，换档由异步调用方先 flush。**
  segment 和 WS 批次同一条规则、同一个理由：一行只带一个档位，跨档拼接会
  把两档文本标成一档——在持久化侧重造一遍 [[_thinking_batcher]] 刚消灭的
  那次撕裂。（flush 放在 `record_event` 里而不是 helper 里，因为 helper 是
  同步的而 `_flush_segment` 是 async；不要为此塞 fire-and-forget。）
- **行 kind 承载档位**：叙述写 `thinking_segment_monologue`，推理仍写
  `thinking_segment`。**用新 kind 而不是把 payload 改成 JSON**，是为了让本
  改动之前写入的存量行原样可读——无回填、无迁移（铁律 #2/#6）。存量行的档位
  确实未知，回放成 receded，那也正是它们一直以来的样子。

## 2026-08-26 — tool_call 的 current_stage 对齐 run-agent 相位

tool_call bump 里的 `current_stage` 从硬编码 `"step.3_agent_loop"` 改成
**从共享相位常量派生**：`_extract_progress_stage({"step":
PHASE_RUN_AGENT_STEP, "title": PHASE_RUN_AGENT_TITLE})`（常量来自叶子模块
[[runtime_message]]，不能从 [[step_3_agent_loop]] 导——它被本包 `__init__`
eager import 会成环）。原硬编码值与 progress 事件推导出的
`"step.3_Execute Agent Loop"` **两个字符串打架**，同一 loop 阶段在
`events.current_stage` 里来回跳。派生后天然与 progress 相位逐字一致，一个
相位一个值，且相位改名不会再悄悄复活打架。同时同步 in-memory
`self.current_stage`（与 progress 分支对齐）。测试
`test_tool_call_stamps_the_run_agent_stage`（对着常量断言）。

## 2026-08-21 — `first_live_run_id`：跨进程"这个用户忙不忙"的唯一口径

`run_is_live` 是**单条 run** 的判活口径；本次把同一口径抬到**用户**这一层。
动机是 2026-07-31 prod 事故：任何"据此销毁容器"的动作（[[executor_reaper.py]]
的空闲回收）都不能用进程内记账回答"这个用户还在不在跑" —— 编排是 backend +
workers 两个进程，各自只看得见自己那一半。`events` 表是它们唯一的交汇点
（事故教训 #5：DB 痕迹比日志 grep 可靠，"该在的行不在"本身就是证据）。

三个决定：
- **返回 run id 而不是 bool**：调用方要能说出"是谁挡住了我"（reaper 的审计
  行），否则别处会再写一遍 running+心跳的查询。
- **`exclude_run_id`**：提问者自己的 events 行在问的时候已经是 running，不
  排除的话判决恒为"忙"。
- **失败就抛，不吞**：这是唯一入口。包一层"出错返回 None"的便利函数等于给
  同一个问题定义了第二套 fail-safe 语义，而破坏性调用方恰恰是最不能继承别人
  猜测的那批。它们各自解决歧义，且**全部**解决成"忙"。

投影查询（只取 `event_id / last_event_at / started_at`）：events 行带着好几个
MEDIUMTEXT 列。`started_at` 必须留 —— 第一次心跳落地前 `run_is_live` 靠它兜底，
丢了会把刚起跑的 run 读成死的。
## 2026-08-10 — retain normalized action reason

Fatal capture retains `action_reason` beside error type/message so analytics
can classify quota/configuration/infrastructure/runtime without parsing copy.

## 2026-08-07 — root_run_id 的铸造:根 run 给自己盖章

`RunRecorder` 增加 `inherited_root_run_id`,在 `_bind_run_id` 里与 running
翻转**同一条 UPDATE** 写入 `root_run_id = inherited or run_id`。

- **放在 recorder 而不是 Event/create_event**:这是 run 控制事实(和 state /
  心跳 / cancel_requested_at 同族),不是叙事事实;而且 recorder 本来就在
  late-bind 时写这一行,零额外写。
- **与 running 翻转同一条 UPDATE**:任何 run 一旦可见,它所属的树就是完整
  的。分两次写会留下一个窗口,期间级联查询选不到这一行——停止静默漏掉一条
  分支,而且没有任何报错。
- 观察者纪律不变:写自己的记录字段与"绝不影响被观察的 run"不冲突(取消的
  执行者是独立的 [[cancel_watcher]],不在 recorder 里)。

## 2026-08-07 — sweep 尊重取消旗标:停止中的 run 落 cancelled 不落 failed

`sweep_stale_runs` 原来无条件把心跳停跳的 running 行翻 `failed`。加入
[[cancel_watcher]] 后这是个**间歇性错误终态**:用户点了停止,run 在优雅
退出前心跳恰好断了(或 watcher 所在进程被掐),这一行就被记成"故障"。

需求验收明写「停止不被记为失败、不触发失败重试与失败告警」,而原来的
行为让这条验收取决于一场竞速 —— run 自己的 finalize 有没有赶在心跳变
stale 之前。竞速导致的绿是最坏的绿:**重跑一次就过了**。

现在按 `cancel_requested_at` 分流:非空 → `cancelled` 且**不写
error_message**(cancelled 行上盖错误会让下游告警把用户的主动动作当成
事故);其余照旧 `failed` + "run lost"。

判活逻辑(`run_is_live`)一个字没动 —— 变的只是"死了之后记成什么"。

# run_recorder.py — run 可观察性的持久化半身（唯一事实源）

## 为什么存在

2026-07-31 之前，「run 可观察」是聊天 WS 的私有功能：只有 BackgroundRun
驱动的 run（WS 聊天 / openai-compat）会逐条写 `event_stream`、维护
`events` 行状态机和心跳；所有 trigger run（lark / team bus / job / A2A）
是黑箱。Owner 拍板把可观察性升级为**平台属性**：任何 run 留下同一份
live trace，任何读侧（聊天重连、team roster、未来 dashboard）走同一条
路观察。本文件就是那份契约的唯一事实源。

## 内容

- **run 状态机**：`STATE_RUNNING/COMPLETED/CANCELLED/FAILED` +
  `HEARTBEAT_INTERVAL_S`（30s）+ `RUN_STALE_AFTER_S`（3 拍）。从
  background_run.py 移入 —— 状态机属于持久化层，不属于 WS 传输层。
- **`run_is_live`**：读侧唯一判活口径（agents 列表、观察端点、清扫
  共用）。只读，绝不据此停 run（铁律 #14）。
- **`sweep_stale_runs`**：把心跳停跳的 running 行翻 failed。**按心跳
  判，不按进程判** —— run 可能活在别的容器里，「本进程不认识」证明
  不了死。backend lifespan 启动时 + 周期性调用。
- **事件规范化**：`normalise_event` / `event_to_wire` /
  `try_extract_event_id`（从 background_run 移入并去下划线公开）。
- **`RunRecorder`**：消费一个 run 的规范化事件流，写 `event_stream`
  行（組合 B 粒度：thinking 整段一行、tool_call/tool_output/
  text_delta/progress 各一行）+ `events` 行生命周期字段 + 心跳 task。
  `record()` 首个 Step-0 progress 帧 late-bind run_id；`finalize()`
  幂等收尾。两个可选回调：`on_run_id`（传输层注册）、
  `on_thinking_buffer`（Broadcaster 的段内快照镜像）。

## 设计决策

- **观察者纪律**：每个 DB 写独立 try/except —— 记录失败绝不弄坏被
  记录的 run。progress 帧也持久化（观察者要能回放 0~3 步流水线）。
- **杀开关** `NARRANEXUS_RUN_RECORDING_DISABLED` 只管 trigger 挂载面
  （client.py）；BackgroundRun 的持久化早于此开关存在，聊天重连依赖
  它，不受影响。
- seq 每 run 单 writer（per-run 单 recorder 实例），单调无竞争 ——
  观察端点的 tail-follow 依赖单调性。
- `finalize(completed)` 只在 `events.final_output` 为空时回填
  （step_4 的写入是权威）。fatal-completed run（死 key 自然结束）
  **不**写 error_message —— completed 行上盖错误会显示假故障，
  错误已作为 error stream 行存在。

## 上下游

被 `background_run.py`（组合）与 `agent_runtime/client.py`（trigger
挂载）消费；读侧是 `backend/routes/websocket.py` 的观察端点与
`backend/routes/auth.py` 的 active_run 富集；`backend/main.py`
lifespan 调 `sweep_stale_runs`。

测试：tests/agent_runtime/test_run_recorder.py。

## 2026-08-20 — classify_event 转公有(#334 minor)

两个模块消费即非私有:去下划线并入 __all__;background_run 同步改。
