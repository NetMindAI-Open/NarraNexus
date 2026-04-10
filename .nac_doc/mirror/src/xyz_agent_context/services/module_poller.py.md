---
code_file: src/xyz_agent_context/services/module_poller.py
last_verified: 2026-04-10
stub: false
---

# module_poller.py — Instance 完成回调检测服务

## 为什么存在

Job 是异步执行的——一个 Job 可能跑几分钟到几小时，完成后需要通知依赖它的其他 Job 解除 blocked 状态。AgentRuntime 不能轮询等待，用户的请求不能阻塞在那里。`ModulePoller` 作为独立进程，每隔 5 秒扫描数据库，检测哪些 Instance 从 `in_progress` 变成了 `completed` 或 `failed`，然后调用 `InstanceHandler.handle_completion()` 处理依赖链。

## 上下游关系

**被谁触发**：`make dev-poller` 或直接运行 `uv run python -m xyz_agent_context.services.module_poller`。它是独立进程，不被任何 Python 代码 import 启动。

**调用谁**：
- `repository/InstanceRepository` 查询状态变化的 Instance
- `repository/InstanceNarrativeLinkRepository` 查找 Instance 对应的 Narrative
- `narrative.InstanceHandler.handle_completion()` 处理依赖激活逻辑（InstanceHandler 直接从 `narrative` 包顶层导入，而非通过 NarrativeService，是有意绕过 Service 层的快捷路径）
- 启动时调用 `utils/schema_registry.auto_migrate()` 确保所有表结构是最新的

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
