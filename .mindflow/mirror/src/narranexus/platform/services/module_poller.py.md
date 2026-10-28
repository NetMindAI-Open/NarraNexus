---
code_file: src/narranexus/platform/services/module_poller.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（PR #394 review C1）— Path A 只用 `peek_skip`，不认领半开探测

`_execute_callback_instance` 吞掉所有失败、不报结果，本入口永远结算不了探测；而一个没人
结算的认领会让死凭据每个 grant 周期重跑一次（C1）。所以 Path A 不再走
`should_skip`+`try_begin_probe` 两步，改为 [[circuit_breaker]] 的只读 `peek_skip`：
PAUSED（含半开窗口已开）/PROBING/未到期 COOLING 一律不建 runtime、不写行；探测留给能结算
的入口。fail-open 不变。锁：`test_module_poller_circuit_breaker_gate.py`（真熔断器，三种
held 状态均不跑且行不变；健康 agent 照跑）。

## 2026-09-10（review r3 C1/I2）— 发现查询补 narrative-less 半边；对账只扫无 link 的 BLOCKED

**C1**：`_find_completed_instances` 原来只有 `INNER JOIN instance_narrative_links ... link_type='active'`
一条查询，`/api/jobs/complex` 的 instance 没有任何 link 行，**永远不会被发现**——下文 B-16 段描述的
`handle_completion_no_narrative` 分支在生产上是走不到的，依赖链实际只靠 15 分钟对账激活。现在发现分两半，
各自 LIMIT 100：narrative 半边不变；新增 [[instance_repository]] `get_unlinked_completed_awaiting_callback(100)`
（完全没有 link 行的实例），产出 `CompletedInstanceInfo(narrative_id=None)`（字段改 `Optional[str]`），走
`handle_completion_no_narrative`。于是 A 完成后 B 在下一个 5 秒 poll 周期激活，对账退回真正的兜底。只有
HISTORY link 的实例两半都不进（与改动前一致）。上线时历史上已完成、未处理的无 link job 实例会按每轮
100 条被逐步消化：只会激活依赖全终态的 BLOCKED 依赖方（与对账同一判据），`completed_at` 不被改写。
**I2**：对账候选（`get_blocked_page`）排除任何有 narrative link 的实例，narrative 绑定的 BLOCKED 由
`handle_completion` 独占；分页改为「空页才结束」，游标照常推进。
锁：`tests/services/test_module_poller_no_narrative_dependency.py` 的
`test_completed_narrative_less_instance_is_discovered_and_unblocks_its_dependent`（走真实发现查询）、
`test_poll_cycle_activates_the_dependent_without_the_reconcile_backstop`、
`test_discovery_keeps_narrative_bound_rows_on_the_narrative_path`、
`test_narrative_free_completion_never_activates_a_narrative_bound_dependent`、
`test_reconcile_leaves_narrative_bound_blocked_instances_to_handle_completion`。

## 2026-09-10（review r2 I-A）— 对账改成按 `id` keyset 分页走完整个 BLOCKED 集

r1 版的「限量」是假的：`find(limit=200, created_at ASC)` 只限制了**发现哪些 agent**，handler 随后对每个
agent 再做一次无上限的 `get_by_agent(BLOCKED)`；而且固定的「最老 200 条」窗口会被永远激活不了的行
（上游仍 ONGOING、或依赖行已被删）长期占满，之后新产生的 BLOCKED 行再也不会被看到——backstop 静默失效。
现在每轮从 `id > 0` 起，用 [[instance_repository]] 的 `get_blocked_page(after_id, _BLOCKED_RECONCILE_PAGE=200)`
按自增 `id` 升序分页，每页按 `agent_id` 分组后**把行本身**传给 `reconcile_blocked_instances(rows)`
（handler 不再二次查询），下一页游标 = 本页最后一行的 `id`，页不满即结束。结果：**每条查询、每次
handler 调用都以 200 行为界；一轮总量不设上限（刻意的——任何 BLOCKED 行都不会被饿死）**，15 分钟一轮。
游标只活在单次调用里，不跨轮保存任何进程内状态，重启/多副本无需交接。选 `id` 而非 `created_at`：
唯一、随插入单调、主键有索引、两种方言都是整数——无重叠、无因本页行被激活移出 BLOCKED 导致的
OFFSET 式跳行，也不依赖 SQLite 下 DATETIME 文本的写法。
锁：`test_window_full_of_never_activatable_rows_still_activates_a_new_row`（页大小打到 3，塞 8 条永久卡死
的老行 + 1 条可激活的新行，新行必须被激活）、`test_reconcile_pages_are_bounded_and_the_handler_does_not_requery`。

## 2026-09-10（review r1 I10）— `_reconcile_blocked_instances`：15 分钟一次的 BLOCKED 对账

`_poll_and_enqueue` 开头加低频 backstop（`_BLOCKED_RECONCILE_INTERVAL_S = 900`，与
[[job_trigger]] 的 no-quota backstop 同节奏；`_last_blocked_reconcile` 记上次时间）：取
BLOCKED 实例（取法已被 r2 I-A 改为按 `id` keyset 分页走全集，见上节），
按 `agent_id` 分组，逐组交给 [[instance_handler]] 的 `reconcile_blocked_instances`——判据与激活钩子
都是事件路径那一份，这里不另写「依赖是否满足」。为什么放在本服务而不是 job_trigger：本文件是
依赖链推进的唯一驱动者（`_process_completed_instance` 是 `handle_completion*` 的唯一调用点），
对账属于同一职责；job_trigger 只管到期执行。激活后 `JobModule.on_instance_activated` 把 job 的
`next_run_time` 设成 now，JobTrigger 下个周期照常捞走。任何异常只记日志，绝不 wedge 循环。
锁：`test_poll_cycle_reconciles_a_blocked_instance_whose_dependency_finished_unseen`、
`test_reconcile_backstop_is_rate_limited`、`test_reconcile_groups_by_agent_and_counts`。

## 2026-09-09 — B-16：`_process_completed_instance` 按 narrative_id 有无分叉

`InstanceHandler.handle_completion`（这里唯一处理依赖激活的调用点，见
docstring「Execution strategy: Path B」）只能在 `instance_narrative_links`
里按 `narrative_id` 找依赖方——`/api/jobs/complex` 建的 job 从没绑过
narrative（route 从不传 `narrative_id`），根本没进过那张表，`handle_completion`
对它们永远等价于「查不到任何依赖方」。于是这类依赖链「永不触发」
（GitHub #114/#109）：上游 job 跑完，`_process_completed_instance` 照常执行，
但下游 job 的 BLOCKED 状态永远没人碰。

`info.narrative_id` 为假值时改叫 [[instance_handler]] 新增的
`handle_completion_no_narrative`——直接读 `module_instances.dependencies`，
不touch 任何 narrative 相关表。有 narrative_id 时行为完全不变（回归测试
`test_narrative_id_present_still_uses_the_narrative_scoped_path` 钉死这点）。

## 2026-07-22 — no longer its own OS process; runs under the worker supervisor

`ModulePoller.start()` is unchanged, but it is no longer launched as a
standalone `python -m ...module_poller` process by run.sh / dev-local.sh /
deploy-cloud.sh / Tauri. It is now one supervised task inside
[[run_worker_supervisor.py]] (shared event loop + DB pool, backoff-restart on
crash). The "独立进程" framing below is HISTORY — the poller shares the
supervisor's interpreter now. Its own `ServiceAuditor("module_poller")` and the
`__main__` debug entrypoint are retained.

## 2026-07-13 — Agent 实时层熔断器接入

`_execute_callback`（Path A，触发 AgentRuntime 的唯一路径，**当前休眠**——活动的是 Path B / JobTrigger，后者自带熔断）顶部加**防御性** `should_skip` 闸门：若将来切到 Path A，坏 agent 也不会在此被重触发。fail-open。


# module_poller.py — Instance 完成回调检测服务

## 为什么存在

Job 是异步执行的——一个 Job 可能跑几分钟到几小时，完成后需要通知依赖它的其他 Job 解除 blocked 状态。AgentRuntime 不能轮询等待，用户的请求不能阻塞在那里。`ModulePoller` 作为独立进程，每隔 5 秒扫描数据库，检测哪些 Instance 从 `in_progress` 变成了 `completed` 或 `failed`，然后调用 `InstanceHandler.handle_completion()` 处理依赖链。

## 上下游关系

**被谁触发**：`make dev-poller` 或直接运行 `uv run python -m narranexus.platform.services.module_poller`。它是独立进程，不被任何 Python 代码 import 启动。

**调用谁**：
- `repository/InstanceRepository` 查询状态变化的 Instance
- `repository/InstanceNarrativeLinkRepository` 查找 Instance 对应的 Narrative
- `narrative.InstanceHandler.handle_completion()` 处理依赖激活逻辑（InstanceHandler 直接从 `narrative` 包顶层导入，而非通过 NarrativeService，是有意绕过 Service 层的快捷路径）
- 启动时调用 `utils/db/schema_registry.auto_migrate()` 确保所有表结构是最新的

## 设计决策

Worker Pool 架构：1 个 Poller 协程 + N 个 Worker 协程（默认 3 个）。Poller 负责查询并把任务放入 `asyncio.Queue`，Worker 从队列取任务并发处理。好处是多个 Instance 完成时可以并发处理回调，不会排队等待。

通过 `_processing_instances: Set[str]` 防止同一个 Instance 被并发处理两次——Poller 每次轮询都会检查这个集合，已在处理中的 Instance 跳过不重复入队。

当前实现是 **Path B 策略**：ModulePoller 只负责激活依赖，`handle_completion()` 会设置 JobModule Instance 的 `next_run_time = NOW()`，然后由 JobTrigger 的独立轮询检测到这个时间并执行。代码里有 `_execute_callback()` 方法但标注为 disabled，这是 Path A 的预留实现（ModulePoller 直接调 AgentRuntime 执行回调），目前未启用。

`last_polled_status` 字段是状态变化检测的关键：Poller 查的条件是 `status IN (completed/failed) AND last_polled_status = in_progress AND callback_processed = FALSE`。处理完成后把 `callback_processed` 设为 TRUE 并更新 `last_polled_status`，避免重复处理。

## Gotcha / 边界情况

在处理出错时（`_process_completed_instance` 抛出异常），Poller 仍然会调用 `_mark_callback_processed`，防止"失败的 Instance 无限被重试"。这意味着如果 `InstanceHandler.handle_completion()` 内部崩溃，依赖链将**不会**被激活——这不是 silent failure，会有 `logger.error` 日志，但不会自动重试。

启动时的 `auto_migrate()` 是为了防止"Poller 进程比主进程更早启动，表还没建好"的竞态——这在 `make dev-poller` 和主进程并发启动时可能发生。

## 新人易踩的坑

ModulePoller 的日志里有 `logger.success()` 调用（loguru 特有方法），不是标准 logging 的方法，grep 时用 `success` 级别过滤。

`poll_interval` 默认 5 秒，`max_workers` 默认 3。在任务密集场景下 `max_workers` 应该调高，否则 Worker 成为瓶颈时队列会积压。用 `--workers 5` 参数启动可以调整。

独立进程意味着它不共享 AgentRuntime 的内存状态——如果主进程里有内存级别的缓存（如 VectorStore），ModulePoller 里的操作不会更新那个缓存。依赖链激活后下一次用户请求需要重新从数据库加载状态。
