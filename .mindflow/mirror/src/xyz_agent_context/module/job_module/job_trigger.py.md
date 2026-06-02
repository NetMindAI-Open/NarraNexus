---
code_file: src/xyz_agent_context/module/job_module/job_trigger.py
last_verified: 2026-05-22
---

## 2026-06-01 — edge recovery backstop + long-running diagnostic (batch ②b)

Poll step 1 no longer force-recovers RUNNING jobs older than 30min
(`recover_stuck_jobs`) — that would interrupt a legitimate long agent_loop AND
duplicate it (铁律 #14). Replaced with `_diagnose_long_running_jobs()` (via
`repo.find_long_running_jobs`, read-only): it logs a WARNING per long-runner for
alerting but never resets them. Orphan RUNNING rows from a killed process are
still reset at startup by `recover_all_running_jobs` (the only safe time).

Poll step 1.5 (`_resume_eligible_no_quota_jobs`) is now gated to a low-frequency
backstop (`_NO_QUOTA_BACKSTOP_INTERVAL_S`, 15min) instead of running every 60s
cycle. Primary PAUSED_NO_QUOTA recovery is edge-triggered from the
provider-mutation routes via `job_recovery.rearm_user_no_quota_jobs`.

NOTE: `find_long_running_jobs` filters in Python, not via a SQL datetime-string
comparison — SQLite stores datetimes with a 'T' separator but binds a datetime
param with a space, so `started_at < %s` compares wrong ('T' > ' '). This is a
latent SQLite-only bug `get_due_jobs` / `recover_stuck_jobs` share (masked by
native MySQL DATETIME in prod); Python filtering is correct on both backends.

## 2026-06-01 — transient-failure backoff via COOLING (batch ②)

Before: `_finalize_job_execution` ignored `success` on the non-quota path, so a
run that *failed* for a transient reason (network / 5xx / timeout) rescheduled
straight to ACTIVE and re-fired every interval; a one_off failure was even
marked COMPLETED (hiding the failure). Now a non-quota failure goes to `COOLING`
with `cooldown_until = now + _compute_cooldown_seconds(n)` (exp backoff: 60s ×2,
cap 1h) and `next_run_time = cooldown_until`; `consecutive_failure_count` is
incremented; at `_MAX_CONSECUTIVE_FAILURES` (8) it escalates to `FAILED`
(`paused_reason=repeated_failure`) instead of cooling forever. A success resets
the counter and clears cooldown before the normal complete/reschedule branch.

`_rearm_cooled_jobs()` (poll step 1.6, symmetric with the no-quota resume step)
flips `COOLING → ACTIVE` once `cooldown_until <= now` — **time-based** recovery,
so polling is the natural trigger (contrast PAUSED_NO_QUOTA, which is
state-change/edge recovered). A COOLING job's instance status is deliberately
left untouched (not terminal) so dependents stay blocked until it finally
succeeds or gives up.

铁律 #14: this spaces SCHEDULER retries, never caps a running agent_loop — only
a run that finished AND failed accrues backoff. New `instance_jobs` columns
(`consecutive_failure_count`, `cooldown_until`, `paused_reason`, `paused_at`)
are additive (auto_migrate); JobStatus gains `COOLING` / `BLOCKED` /
`BLOCKED_FAILED` (code-only, VARCHAR column unchanged — 铁律 #6).

## 2026-06-01 — resume gate unified onto the single provider classifier (oscillation fix)

The 2026-05-22 resume gate `_user_can_run` reimplemented the provider decision
tree as "`QuotaService.check()` OR own-provider-complete". That **ignored
`prefer_system_override`** and so disagreed with the runtime: a user opted in to
the free tier (`prefer_system_override=1`, the default) whose quota was
exhausted but who *also* had a complete own provider was judged "can run" →
resumed `PAUSED_NO_QUOTA → ACTIVE` → picked up → runtime routed to the exhausted
free tier and raised `SystemDefaultUnavailable` (it will NOT silently fall back
to the user's own key) → re-paused. Every poll cycle. Prod 2026-05-31 logged
~1828 pause / ~1826 resume over 72h for 4 such jobs (elricwan, haili, two test
users — all `prefer_system_override=1` + exhausted + own provider).

Fix: `_user_can_run` now delegates to `provider_resolver.classify_provider_for_user`
(→ `ProviderResolver.classify` → `ProviderAvailability`) and returns
`is_runnable(verdict)`. The resume gate, the HTTP path (`resolve`) and — by
construction — the runtime now share ONE classifier, so they cannot drift again.
For the regression case the verdict is `FREE_TIER_EXHAUSTED` → `is_runnable` is
False → the job stays `PAUSED_NO_QUOTA` until the user tops up / configures a
provider / disables the toggle. On any classifier error the gate is
conservatively False (don't resume into an unknown state).

铁律 #15 still honoured: opted-out own-provider users pass via `USER_OK`; the
platform never overrides the user's choice — it only stops resuming a job into a
run the runtime is guaranteed to refuse.

(Design: `reference/self_notebook/specs/2026-06-01-job-scheduler-resilience-design.md`,
batch ①. Remaining batches — cooling/backoff, edge-triggered recovery, pause/resume
API + notifications + frontend — are not yet implemented.)

## 2026-05-22 — no-quota auto-pause + resume (#6 infinite-loop fix)

A run that failed because the owner's free-tier quota is exhausted (and no own
provider is configured) returns `success=False` (it does NOT raise), so it
bypassed `_handle_job_failure` and went through `_finalize_job_execution`, which
ignored `success` and **rescheduled** the recurring/ongoing job — so it re-fired
every interval into the same wall forever (amplified by many jobs).

Fix:
- `_finalize_job_execution` now early-returns BEFORE the reschedule branching
  when `_is_no_quota_failure(result)` (error_type ∈ `_NO_QUOTA_ERROR_TYPES`
  like `QuotaExceededError`, set by `step_3_agent_loop`'s
  `error_type = type(e).__name__`; plus a message-substring fallback). It sets
  status `PAUSED_NO_QUOTA` and does not reschedule. **Transient** failures
  (network/LLM hiccups) are deliberately excluded → they still reschedule.
- `_poll_and_enqueue` calls `_resume_eligible_no_quota_jobs()` each cycle:
  for every `PAUSED_NO_QUOTA` job, `_user_can_run(uid)` checks system quota
  (`QuotaService.default().check`) OR a complete own provider
  (`UserProviderService` + `_is_user_config_complete`); if so, recompute
  `next_run` from now and flip back to ACTIVE. Covers both resume triggers
  (quota topped up / own provider configured).

铁律 #14: this is purely job-scheduler-level — no agent_loop time/iteration
limit, no force-stop. #15: own-provider users' runs succeed, so they never enter
the pause branch and their jobs are never wrongly paused.

## 2026-04-27 — disable per-run file logging (fd-leak fix)

EC2 production observation: `narranexus-jobs` Python process saturated
file descriptors at 1021 / 1024 limit after ~3 days of uptime, and
**every subsequent job run failed with `OSError: [Errno 24] Too many
open files`** thrown from `logging_service.py:128`. Of the 1021 fd, 674
were `PIPE` — roughly 337 unreclaimed `multiprocessing.SimpleQueue`
instances created by loguru's `enqueue=True` worker queue.

Root cause: `LoggingService.setup()` calls `logger.add(..., enqueue=True)`,
which spawns a `multiprocessing.SimpleQueue` (2 pipe fd + 1 lock fd).
Cleanup is owned by the agent_runtime background hook task; if `setup`
itself raises (e.g. fd exhaustion), or the BG task is killed before its
finally clause runs, the queue's fds are never closed. JobTrigger runs
high-frequency cron jobs (e.g. S&P 500 every 10 min — 144 runs/day),
so the leak compounds fastest here. `narranexus-backend`, which also
uses default `LoggingService`, leaks at a much lower rate; `lark_trigger`
and `message_bus_trigger` already disable file logging entirely.

Fix: pass `LoggingService(enabled=False)` to `AgentRuntime` in
`_run_agent`, matching the convention already used by `lark_trigger.py`
(line 1242) and `message_bus_trigger.py` (line 409). With logging
disabled, `setup()` returns immediately without allocating a loguru
handler, so the leak path is closed.

Trade-offs:
- Per-agent log files at `~/.narranexus/logs/agents/<agent_id>_*.log`
  are no longer written for job-triggered runs. Same trade-off
  `lark_trigger` and `message_bus_trigger` already accepted.
- `docker logs narranexus-jobs` still surfaces full loguru output to
  stdout, so post-incident triage is unaffected. Container log retention
  is the operational source of truth for trigger-run history.
- The deeper fix (remove `enqueue=True` from `LoggingService` itself or
  redesign cleanup ownership) remains open for the architectural
  TODO list — this fix is the smallest change that aligns the three
  trigger processes and stops the bleed.

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
