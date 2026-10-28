---
code_file: src/narranexus/platform/agent_runtime/executor_reaper.py
stub: false
last_verified: 2026-08-24
---

## 2026-08-24 — broker 可以拒绝停机，本文件必须听懂

`stop_fn` 的契约从 `Awaitable[None]` 变成 `Awaitable[bool]`：`False` = broker 拒绝，
因为**容器自己**说有在途工作（见 [[broker_client.py]]）。这一侧的判活读 events 表，
看不见 office-watch 这类不落 run 行的持有者，而**编排侧回收器的 TTL 是 20 分钟**，
比 broker 自己的 4h 紧一个数量级 —— 所以"容器忙"这件事最先是在这条路上被撞见的。

判据是 `if not await stop_fn(...)` 而不是 `is False`：极性要偏向安全的那一侧 ——
将来某条分支忘了 return，falsy 会让该用户**留着戳、下轮重试**；而把沉默当成功会丢
掉戳，那个从没被停掉的容器就再也不会被重新考虑。（`is False` 还等于把已退役的
`-> None` 契约固化下来，铁律 #2。）

处理方式与"判活变忙"那条分支一致：**把戳还回去**（否则一个从没被停掉的容器也就
再也不会被重新考虑），不计入 `reaped`，并在 `reaper_status()` 里单独记
`refused_busy`。

**故意不写 `cull_skipped_busy` 行**：那个指标的契约是"一行 = 救下的一个 run，且用它
真实的 run id 命名"，而这次拒绝**没有 run id 可命名**（持有者不是 recorded run）。
混进去会让"数行数 = 救了几个 run"这个读法失效 —— 与当初拒绝把 `unknown` 写进
run_id 列是同一条理由。`refused_busy` 非零意味着容器看见了 events 表看不见的持有者。

## 2026-08-21 — 判活多了第二个消费方：broker 的 stale 镜像替换判决

`no_live_recorded_run_for(user_id, active_run_id=...)` 落在本文件，因为它和空闲
回收问的是**同一个问题**（"现在有人在用这个容器吗"）、销毁的是**同一个容器**，只是
理由不同。分成两处问会让两边的判活口径在第一次改动时就开始漂移。

**判决在编排侧算、broker 只负责听**：broker 是全系统唯一有 docker 权限的组件，
它的威胁模型建立在"只有一个受调用方控制的输入（会被校验的 user_id）"上。为了一个
编排侧本来就握着的事实给它发 DB 凭证，是拿安全面换便利。

**必须排除提问者自己**：ensure 发生在 step 3，那时提问者的 events 行已经是
running，不排除的话判决恒为"忙"，镜像永远滚不动 —— 把掐 run 换成"旧 executor 静默
降级"（2026-07 mcp_servers 改名 → 空 MCP 集，不报错）。

**故意保守**：同一用户的另一个在途 run 未必真在用这个容器（可能还没走到 step 3，
或者是根本不碰 executor 的 direct-trigger run）。推迟的代价是多跑一轮旧代码、下次
ensure 自愈；替换的代价是杀掉一个在跑的 run（铁律 #14）。

**函数名说的是证据，不是结论**：叫 `no_live_recorded_run_for` 而不是
"…is_safe"。它能看见的只有 `events` 表里的 run；容器上别的东西它一个都看不见 ——
office_watch 的代理会话不是 run，`backend/routes/office_watch/proxy.py` 那几处
**必须**继续吃默认 `allow_stale_replace=False`。名字写成"safe"就是在邀请下一个人
把它传给那些调用方。

**而且判决的主体是容器，不是调用方**：`allow_stale_replace=True` 授权销毁的是容器，
要证的命题是"容器上没有任何人"。"我自己没在容器上占东西"排除不掉别的子系统的会话
—— 今天没有任何调用方能证明前者，所以**每个传判决的调用方都在接受一个残留风险**
（office-watch 会话被连带拆掉）。今天可以接受，是因为空闲回收器已经在 20 分钟
idle TTL 上拆这种容器；正解是让非 run 占用者也进同一个判活口径：`watch/ensure` 落一条 lease 行，
`live_run_elsewhere` 一起读。（细节另有本地笔记
`reference/self_notebook/todo/…`，**未入库**，不必依赖它。）

`caller` 和 `consequence` 都只用于日志，而且**签名上就是必填**（没有默认值，
reaper 那侧用 `functools.partial` 显式绑定）：默认值必然是某一个消费方的后果文案，
下一个漏传的消费方会静默继承它 —— 那正是这个参数被加出来要修的 bug。**新**消费方漏传就是第一次调用
`TypeError`；reaper 这条路不是 —— 它靠 `_REAPER_LIVENESS` 绑定，而这条路上的
TypeError 会被"一个用户的失败不中断整趟"那两处宽 catch 吞成一条 `reap pass error`
或（更糟）`failed to stop executor`（读起来像 broker 挂了），`reaper_status()` 照样
报健康。所以 **reaper 的绑定值由测试守**，不是由签名守
（`test_the_reaper_binds_its_own_log_subject`）。两个消费方在"判不
出来"时的后果不同（回收停摆 vs 镜像永不滚动），把其中一方的后果硬编码进共享函数，
就是让另一方的告警去指错方向。`NARRANEXUS_RUN_RECORDING_DISABLED` 打开时
stale-replace 恒判 `False`（所有用户被钉在旧镜像上），那条日志必须说的是镜像的事。

## 2026-08-21 — 回收器不再掐掉活在别的进程里的 run（prod 事故修复）

**事故**：2026-07-31 prod 群聊 @ agent 后回复失败，前端 `infra_transient`。
不是网络也不是资源 —— **回收器把正在干活的容器停了**。

**根因**：executor 容器按 **user** 共享，但 [[admission.py]] 是**进程级**单例，
而本 reaper 只在 backend 起（`backend/main.py`）。云端编排跑在 backend +
workers 两个进程里：backend 只看得见自己的网页单聊 run，看不见 workers 里的
群聊/消息总线、定时任务、渠道触发。所以本文件原文件头那句
"only idle executors (zero active loops) are ever reaped" 只在单进程内成立
—— "0 活跃 loop" 实际是"**在问的这个进程里**是 0"。触发组合＝用户先在网页
单聊聊过（backend 打了 idle 戳），TTL 到期那一刻正好有个 workers 驱动的 run
在跑。纯单聊或纯群聊用户都不会踩到，所以它藏了很久。时间线可精确对账：
09:21:15 + 1200s = 09:41:15，回收器 120s 一轮，09:42:02 那轮命中。

**改法**：让"这个用户忙不忙"成为**跨进程可判定的事实**，数据源是 `events`
表（每个进程都往里写 —— 事故教训 #5）。判活复用
[[run_recorder.py]] 的 `run_is_live`（30s 心跳、3 拍判死）。不需要新表、
不需要 Redis。协作者从三个变四个，多出来的那个回答 **IS** the user idle。

- `live_run_elsewhere()` — 本文件对外的判活入口，**永不抛**，任何失败路径
  都答"忙"，用 `UNKNOWN_RUN` 哨兵区分"真有 run 挡着"和"问不出来"。
- `_CullVeto` — 注入给 `claim_idle_users` 的否决器，按 **run id** 去重审计
  行。被否决的用户每轮都会被重新提名、重新否决，逐轮写行会让行数变成
  **run 时长的函数**（一个 10 小时的 agent ≈ 300 行，而铁律 #14 说这是正常
  的）。指标要数的是"救下了几个 run"，所以每个 (user, run) 只记一次。
- `reap_once` **停之前再查一次**：claim 时统一否决过，但停是串行的、每个
  `docker stop` 还要等 SIGTERM 宽限期，轮到第 N 个用户时它的判决可能已经
  几分钟前了，足够一个 bus 触发的 run 起来并跑到 step 3。
- 回退时 `restamp_idle`：claim 是破坏性的，光 `continue` 就会把误杀换成
  **容器永不回收**的泄漏。

**为什么否决必须下沉进 `claim_idle_users` 而不是在这里过滤** —— 见
[[admission.py]] 同日条目，这是本次改动最关键的一处落点判断。

**kill switch 的连带影响**：`NARRANEXUS_RUN_RECORDING_DISABLED` 打开时，
trigger 路径的 run 根本不会翻 running，DB 会把它们全报成空闲。所以那面开关
一开，**整个回收器停摆**（一律答"忙"）并打警告。观测开关不能顺手变成"允许
销毁容器"的授权。

**护栏的起点是 `events` 行翻 `running`，不是 admission 入队**：另一个进程的
run 从 `acquire()` 到 recorder 的 `_bind_run_id` 之间（step 0 建行 + 首个带
event_id 的 progress，数百 ms~数秒）对 `first_live_run_id` 不可见。这个窗口里
被回收的后果是 step 3 的 `ensure()` **冷启一个新容器**（用户看到首 token 慢），
不是事故里那个 `infra_transient` —— 不构成本次事故的复发。彻底关掉要把
admission 账本本身 Redis 化（铁律 #20），牵动并发闸门，单独排期。

**claim 了但没动手的两条路都要还戳**：判活变忙、以及 `stop_fn` 抛异常
（`stop_executor` 是一次到 broker 的 HTTP 调用，部署期重启/5xx/超时都走这条）。
后者是更常见的那条。少还一次，该用户在 backend 的戳就永久消失，"主要在
workers 里跑"的用户等不到下一次 `release()` → 容器再也不会被回收。broker 自带
的 label-based reaper **不兜这条** —— 它清的是 orphan，一个已知 user 的容器不是
orphan；而且 `EXECUTOR_IDLE_TTL_SECONDS` 在 compose 里没配，broker 自己的空闲
回收器根本没开，编排侧这个 reaper 是**唯一**的空闲回收器。

**停摆态必须可见**（事故教训 #4 的 L2）：fail-safe 让所有人恒判"忙"时，回收器
永久停摆，而它和"本来就没人该回收"从外面看**一模一样**（都是 reap 0 行、
`cull_skipped_busy` 恒 0）。所以：
- `_CullVeto` 记每轮的 judged/vetoed/blind 三个计数（只有否决器看得见候选，
  `claim_idle_users` 只回幸存者）
- 整轮全 blind 时按 `_BLIND_WARN_EVERY` 周期重发警告 —— 只打一次会随
  `docker restart` 一起消失，而开关状态还留在 `.env` 里（事故教训 #5）
- 写一行 `cull_disabled` 审计，与上面那条警告**共用 `_BLIND_WARN_EVERY` 这一个
  节拍**（第一个全瞎轮立刻落一行）—— 每轮一行会让行数变成**停摆时长**的函数。
  注意与 `_CullVeto` 的按 run 去重是**两件事**：那里避的是 run 时长，这里避的是
  停摆时长。成因写进 `detail.recording_disabled`，行本身自证；**能落行的成因
  不止 kill switch**，只有 DB 完全拨不通那种确实落不进来 —— 展开见
  [[executor_audit.py]]
- `reaper_status()` 挂进 `/api/admin/runtime/status`（见
  [[runtime.py]]），**无条件上报**，且**两条路径的键集合完全相同**：
  - `is_busy=None` 那种"没装护栏"的配置照报。把它藏成 `running: false` 会让读的
    人担心"没人回收要泄漏"，而真实风险恰恰相反（回收器正在按事故前的逻辑掐 run）
  - "还没跑完第一轮"那条路径也给全套键（未知量为 `None`，计数为 `0`）。它覆盖
    **每个进程部署后的头一个 interval**，正是有人盯着看的那两分钟；消费方在那里
    KeyError 会像端点坏了。`veto_installed` 在这条路径上是 `None` 而**不是**
    `False` —— "不知道"和"没有护栏"要的是相反的反应

**判活必须有自己的每候选预算（`_PER_CANDIDATE_S`）**：DB 最常见的降级形态是
**慢**而不是死。只在 admission 那层用整批预算的话，`asyncio.wait_for` 会在
`await` 处**取消**否决协程，`_judged += 1` 永远不执行 —— 于是"这一轮什么都判
不了"和"这一轮本来就没人该回收"导出完全相同的零，本 PR 三个观测面同时报健康。
预算下沉进 `_CullVeto.__call__` 后，超时走既有的 blind 记账，警报照常响；
admission 那层的整批预算退化为 backstop（保护的是 admission 延迟，两层职责不同，
都要留）。停之前那次复查也走同一个 `__call__`，所以**不再**单独包一层
`wait_for` —— 两层超时会 race，输的那个的 `TimeoutError` 会掉进停容器的
`except` 里，读起来像"broker 挂了"。同理 `_audit()` 的预算加在**函数内部**：
它是 cull 路径上的一次 DB **写**，而复查那条路经由否决器也会走到它，不设预算时
一个卡住的连接池会把整轮 park 住 —— 而跑不完的轮不会上报，于是卡死的 reaper
对外显示成"从没跑过"。

**两层预算的关系不能只靠"取个好数值"**：外层一旦把某次判活中途取消，那次判活
就从记账里消失 —— 而且按真实计时，被吃掉的**恒定是每一轮的最后一个候选**，不是
偶尔一个。选一个不整除批预算的值解决不了它（只是把边界挪到下一个候选）。所以
reaper 把 `_PER_CANDIDATE_S` **传给** `claim_idle_users`（`per_check_budget`），
由后者拒绝发起装不下的那次判活 —— "发起了就一定记得上账"变成**结构性成立**，与
两个常量取什么值无关。两条测试守：
`test_the_batch_budget_never_cancels_a_check_mid_flight`（守卫本身）和
`test_the_two_timeout_layers_together_still_record_a_blind_pass`（用**真实**
controller 端到端，其余测试的假 controller 没有外层预算，证明不了"内层先响"）。

**残余**：被扣留的候选从没被判过，所以 `judged` 是个**下界**而不是候选数，警告
文案里的数字也偏小。**告警不受影响**（判到的那几个全 blind ⇒ 整轮判定为瞎）。

**审计写有自己的预算 `_AUDIT_WRITE_S`**，不复用判活预算：一次写 + 拿连接的合理
量级和一次索引读本来就不同，焊在同一个名字上意味着以后每次调其中一个都会悄悄调
另一个。

因此 reaper 预留给一次判活的是 **`_PER_CANDIDATE_S + _AUDIT_WRITE_S`** —— 一次
`__call__` 的最坏开销包含它可能跟着做的那次审计写。只预留判活的话，取消点只是从
判活挪到了写上：记账仍在（`_judged` 在写之前就加了），但**那一行审计丢了**。

**丢了要能重来**：这两行是本次交付的全部可观测性（`cull_skipped_busy` 每行 = 一个
被救下的 run），所以 `_audit()` 返回是否落行，`_blocked_by` 备忘**只在落行后**记，
`cull_disabled` 的限频节拍也按**上次成功落行**算而不是按轮次。写失败 → 下一轮重试；
写成功 → 立刻回到 `_BLIND_WARN_EVERY` 的慢节拍，行数不会变成停摆时长的函数。
备忘记在写之前的话，一次池卡顿就让那一行**永久**消失：下一轮看到同一个
(user, run)，`!=` 判假，再也不试。

**计数分两段**：一轮里 `is_busy` 被问的次数是 `候选数 + 幸存者数`。合并导出会
让健康轮显示 `judged: 10 / reaped: 5`，读的人去查另外 5 个不存在的用户，"否决率"
这个最自然的派生指标也系统性偏低。所以 claim 阶段的账在 `claim_idle_users` 返回
后**立刻** drain，复查阶段单独记 `recheck_*`。**`wholly_blind` 只能用 claim 阶段
那对数字算** —— 把复查的 blind 混进去，会把"claim 正常、复查时 DB 抖了一下"误报
成整轮全瞎。

**没有候选的那一轮不动 `blind_passes`**：既不加也不清零。kill switch 开着的几
小时里夹一分钟没人到期，清零会把任何基于阈值的告警打穿。

**新鲜度是 L2 的前提**（事故教训 #4）：`running: true` 原本只意味着"这个进程里
曾经跑完过至少一轮"。之后无论卡死、逐轮抛异常、还是 task 已经死了，
`reaper_status()` 都会**永久冻结在最后一次好轮的数字上**，读起来和健康系统一模
一样。所以加 `age_seconds` / `stale`（3 个 interval，沿用本仓"3 拍判死"那把尺）
和 `task_error`（`_on_reaper_done` 落下来的，否则 task 之死只有一行会被轮转吃掉
的日志）。

不修的代价是具体的：泄漏累积到 broker 的 `MAX_EXECUTORS` 之后，ensure() 开始
对**新用户** 403，现象是"点了没反应/起不了 agent"，而 on-call 手里没有任何指标
指向 reaper。

**可观测**：每次否决写一行 `instance_executor_audit / cull_skipped_busy`
（见 [[executor_audit.py]]）。每行 = 一个老代码会当场掐死的在途 run。
## 2026-07-28 — post_reap 钩子随 per-run 会话票一起删除

`post_reap_fn` 这个机制当初只有一个用途：culling 掉闲置 executor 后，顺手
吊销该用户遗留的 free-tier 会话票。per-run 会话票整套已经不存在（改成每用户
一把长期钱包 key，落在 `user_providers` 里），钩子随之失去唯一调用方，按
铁律 #2/#8 一并删掉而不是留着空转。

## 为什么存在

per-user Executor 容器的 idle-cull 协调者。云端每个活跃用户有一个 executor
容器(~1.5G),长期不回收会把内存占满。reaper 周期性地把空闲超过 TTL 的用户
executor 停掉。

## 设计(优雅:单一职责 + 依赖注入)

三个关注点分离,reaper 是纯协调者,不持有任何一方的内部:
- `AgentAdmissionController`([[admission.py]]) — 并发 + 空闲记账(WHO is idle)。
- `ExecutorReaper`(本文件) — **WHEN** to cull(周期 + TTL)。
- `broker_client.stop_executor` — **HOW**(docker 传输,DELETE /executors/{user})。

reaper 通过构造注入 `controller` + `stop_fn`,可用 fake 完整单测,无需真 broker/
真 sleep(`reap_once()` 是可测的单趟)。

## 坑 / 决策

- **铁律 #14**:只回收空闲(0 活跃 loop)的 executor,绝不碰运行中的 loop。
  `claim_idle_users` 在锁内原子地"认领并移除",避免重复回收。
- **竞态**:认领后、停止前若有新 run 到达并复用了那个容器 → 极小窗口内 run 可能
  连到被停容器;`broker.ensure` 幂等会冷启动一个新的,最坏只是一次冷启动(唤醒
  UX 覆盖)。20 分钟 TTL 下碰撞概率极低。
- **stop 失败**:见 2026-08-21 条目 —— 记录、**还戳**、跳过,不中断整趟。
  (此处原写"broker 自带的 label-based reaper 兜底清孤儿",两句都不成立:行为已经
  不是"记录并跳过",而 broker 那个 reaper 清的是 orphan,一个已知 user 的容器不是
  orphan。)
- **fire-and-forget**:`maybe_start_executor_reaper` 起的后台 task 挂了 done-callback
  上报异常(事故教训 #2:裸 create_task 是地雷)。
- **门控**:`maybe_start_executor_reaper` 仅在配置了 `BROKER_URL`(云端)时启动;
  本地/桌面无 per-user executor,返回 None。在 `backend/main.py` lifespan 启动/取消。
- TTL/间隔:`EXECUTOR_IDLE_TTL_SEC`(默认 1200=20min)、`EXECUTOR_REAP_INTERVAL_SEC`
  (默认 120)。
