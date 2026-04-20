---
code_file: src/xyz_agent_context/module/job_module/job_trigger.py
last_verified: 2026-04-20
---

## 2026-04-20 — runtime consumption via `collect_run` (Bug 2)

Inner loop now delegates to `agent_runtime.run_collector.collect_run`.
When `collection.is_error` is true the returned job result carries
`success=False`, `error_type`, and `error_message` — replacing the old
misleading "Task executed but produced no text output" fallback for
runs that actually errored (e.g. owner removed their provider, system
quota exhausted). Downstream `_finalize_job_execution` persists the
real failure reason on the job row.

# job_trigger.py — Job 后台轮询执行服务

## 为什么存在

`JobTrigger` 是 Agent 系统的"时钟"——它独立运行，持续扫描到期的 Job 并触发执行。没有它，所有 Job 只能在用户主动发消息时被动执行；有了它，Agent 才能在深夜执行定时任务、在约定时间自动跟进。

这是系统里唯一需要独立部署的 Module 组件，通过 `make dev-poller` 启动。

## 上下游关系

- **被谁用**：`run.sh` / `Makefile` 通过 `python -m xyz_agent_context.module.job_module.job_trigger` 直接启动；Tauri desktop 通过 sidecar 启动
- **依赖谁**：`AgentRuntime`（懒加载，避免循环引用）执行 Job；`JobRepository.try_acquire_job()`（原子锁）防重复执行；`_job_context_builder.build_execution_prompt()`；`_job_scheduling.calculate_next_run_time()`；`UserRepository`（获取用户时区用于 cron 计算）

## 收事件方式

**Worker Pool 模式**：1 个 Poller 协程 + N 个 Worker 协程（默认 5）。Poller 每 60 秒扫一次 DB 找到期 Job，通过 `asyncio.Queue` 送给 Worker。`_running_jobs: Set[str]` 防止同一 Job 被多次入队。

**原子锁防重复**：`try_acquire_job()` 用数据库原子 UPDATE 把状态从 `PENDING/ACTIVE → RUNNING`，只有成功的 Worker 才能执行。这解决了多实例部署（未来）或 Worker Pool 内竞争的重复执行问题。

## 执行身份切换

`_execute_job()` 里用 `job.related_entity_id or job.user_id` 作为执行时的 `user_id` 传给 `AgentRuntime`。这让针对特定用户的 Job（如销售跟进任务）在执行时加载**目标用户**的 Narrative 和社交图谱，而不是 Job 创建者的上下文。

## 设计决策

**`_finalize_job_execution` 的 ONGOING 处理**：ONGOING Job 完成一次执行后，优先由 `hook_after_event_execution`（入口 1，LLM 分析）决定下次执行时间和状态；`job_trigger` 只更新 `iteration_count`，并在入口 1 失败（状态仍为 RUNNING）时作为 fallback 机械更新。两入口的协调通过数据库状态判断，没有显式锁。

**启动恢复**：服务启动时调用 `repo.recover_all_running_jobs()` 把所有 `RUNNING` 状态的 Job 恢复为可调度状态，避免上次进程被杀后 Job 永久卡在 `RUNNING`。

## Gotcha / 边界情况

- **Schema 自动迁移**：`start()` 里调用 `auto_migrate()` 确保所有表存在。这是 JobTrigger 作为独立进程启动时不依赖主进程初始化的必要措施。
- **用户时区影响 cron 执行时间**：cron 表达式按用户的本地时区解释，需要通过 `UserRepository.get_user_timezone()` 获取用户设置的时区（IANA 格式）。时区获取失败时 fallback 到 UTC，这可能导致 cron 任务在错误的时间执行。

## 新人易踩的坑

- 在 SQLite 环境下运行多个 JobTrigger 进程（不应该，但可能误操作）会因 SQLite 单写锁导致 `try_acquire_job()` 的 UPDATE 语句死锁。
- `AgentRuntime` 是懒加载（`from xyz_agent_context.agent_runtime import AgentRuntime`），这是避免循环导入的必要措施——不要改成模块顶部导入。
