---
code_file: src/xyz_agent_context/repository/job_repository.py
last_verified: 2026-06-01
stub: false
---

## 2026-06-01 — resilience fields + find_long_running_jobs (batch ②)

`_row_to_entity` / `_entity_to_row` now carry the four backoff/pause columns
(`consecutive_failure_count`, `cooldown_until`, `paused_reason`, `paused_at`).
New read-only `find_long_running_jobs(threshold_minutes)` returns RUNNING jobs
older than the threshold for DIAGNOSTICS only (铁律 #14 — never force-recover).
It filters in Python, NOT via SQL `started_at < %s`: SQLite stores datetimes
with a 'T' separator but binds a datetime param with a space, so the string
comparison is wrong ('T' > ' '). Native MySQL DATETIME is fine, but Python
filtering is correct on both. (The same latent SQLite bug affects `get_due_jobs`
and `recover_stuck_jobs`, which still use the SQL comparison — out of scope here,
prod is MySQL.)

## 2026-05-27 — defensive None→now() for created_at / updated_at

Real prod incident (Owner dmg, 2026-05-27 18:46): `job_trigger`'s
poll loop crashed every cycle with
  `2 validation errors for JobModel`
  `created_at: Input should be a valid datetime, input_value=None`
  `updated_at: Input should be a valid datetime, input_value=None`
because some pre-existing job rows in the local sqlite DB had
NULL `created_at`/`updated_at` (the columns previously had no NOT
NULL / DEFAULT constraint — see companion fix in [[schema_registry]]).
JobModel itself requires `datetime` (not `Optional[datetime]`), so
the None passed to the pydantic constructor was a hard rejection.

`_row_to_entity` now coerces NULL via `row.get("created_at") or
datetime.now()` at the row→entity boundary. The model stays strict
(no `Optional` ripple through downstream consumers); the DB
columns get NOT NULL + DEFAULT for new INSERTs (in schema_registry);
old NULL rows still load via this fallback. Belt-and-braces — once
all existing NULLs naturally get UPDATEd by future writes the
fallback becomes dead code.

## 2026-05-22 — get_jobs_by_status (no-quota resume, #6)

Added `get_jobs_by_status(status, limit)` so JobTrigger's periodic recheck can
fetch `PAUSED_NO_QUOTA` jobs to consider for resume. No row lock (unlike
`get_due_jobs`' `FOR UPDATE SKIP LOCKED`) — the recheck only flips status;
actual execution still re-acquires via `try_acquire_job` when the job later
fires. `get_due_jobs` filters `status IN (PENDING, ACTIVE)`, so paused jobs
never fire while paused.

# job_repository.py

## Why it exists

`JobRepository` manages the `instance_jobs` table — the persistence layer for the agent's background task system. Its most critical responsibility beyond standard CRUD is `get_due_jobs()`, which fetches jobs that are ready to fire and acquires row-level locks (`SELECT ... FOR UPDATE`) to prevent two JobTrigger processes from claiming the same job simultaneously.

## Upstream / Downstream

`JobTrigger` (background service) calls `get_due_jobs()` on a polling loop and fires each returned job through `AgentRuntime`. `JobModule.hook_after_event_execution()` calls `update_job()` after execution to record the LLM's `JobExecutionResult` (new status, process entries, next_run_time). The job management API routes in `backend/routes/` call `create_job()`, `get_jobs_by_agent()`, and `delete_job()` to serve the frontend job panel.

## v2 时区协议（2026-04-21）

`instance_jobs` 表现在有 α + β 两套 next/last run 字段：

- α：`next_run_time` / `last_run_time`（UTC aware，物理瞬间）——**仅 poller 的 `get_due_jobs()` 用**
- β：`next_run_at_local` / `next_run_tz` / `last_run_at_local` / `last_run_tz`（用户本地 naive ISO + IANA）——**所有面向 LLM / UI 的读取路径都用这套**

对 α+β 的更新**必须走这三个专用方法**，不能直接拼 SQL 更新：

- `update_next_run(job_id, NextRunTuple)`：原子写 α + β 下次运行
- `update_last_run(job_id, utc, local, tz)`：原子写 α + β 最后运行
- `clear_next_run(job_id)`：one_off 触发完、ongoing 达到终止条件时清空下次运行

违反原子性（只更新 α 不更新 β 或反之）会产生"显示时间和实际触发时间不一致"的幽灵 bug。

## Design decisions

**`id_field = "job_id"`**: unlike `AgentRepository` where id_field is the auto-increment, here `job_id` is both the business key and the effective lookup key. `BaseRepository.get_by_id("job_xxx")` works correctly.

**`get_due_jobs()` uses `SELECT ... FOR UPDATE` (row lock)**: this is the one place in the entire repository layer where a transaction-level lock is acquired. It is necessary because `JobTrigger` may run as multiple processes and they must not double-fire a job. The lock is held for the duration of the status update to `RUNNING`. If the process dies after the lock but before the update, the job stays locked until the transaction times out or the process is restarted.

**`trigger_config` stored as JSON**: `TriggerConfig` is a Pydantic model serialized to a JSON string. The repository deserializes it in `_row_to_entity()` as `TriggerConfig(**json.loads(...))`. This means new optional fields added to `TriggerConfig` (like `end_condition`, `max_iterations` for ONGOING jobs) are backward compatible — old rows simply have `None` for those fields.

**`semantic_search()` uses in-process numpy cosine similarity** — same pattern as `InstanceRepository.vector_search()`. All job embeddings are loaded, deserialized, and compared in Python. No database vector index.

## Gotchas

**`JobModel.limit` field**: this field (default `10`) is present on the `JobModel` schema but its serialization in `_entity_to_row()` needs to be checked — if `limit` is included in the row dict, it will be written to the database as a column. The `instance_jobs` table schema should have a `limit` column or the insert will fail. This looks like a schema design error — `limit` is a pagination hint that should not be on the domain model.

**`process` is a JSON list that grows with each run**: `update_job()` should append to `process`, not overwrite it. If the caller passes a `process` list that only contains the current run's entries (not the cumulative history), older entries will be lost. Always fetch the existing `process` list and append before calling `update_job()`.

## New-joiner traps

- `get_due_jobs()` returns jobs with status `PENDING` (never run) or `ACTIVE` (scheduled, due for next run) where `next_run_time <= now`. Jobs with status `RUNNING` or `COMPLETED` are never returned even if they are overdue — `RUNNING` means another process is executing, `COMPLETED` means done.
- `monitored_job_ids` is used by "monitor job" patterns where one ONGOING job watches the completion of other jobs. If you see a job with a non-empty `monitored_job_ids` list, it is a meta-job that should not execute normally — its trigger logic is driven by the monitored jobs' state changes.
