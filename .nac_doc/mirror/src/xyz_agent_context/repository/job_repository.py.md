---
code_file: src/xyz_agent_context/repository/job_repository.py
last_verified: 2026-04-10
stub: false
---

# job_repository.py

## Why it exists

`JobRepository` manages the `instance_jobs` table — the persistence layer for the agent's background task system. Its most critical responsibility beyond standard CRUD is `get_due_jobs()`, which fetches jobs that are ready to fire and acquires row-level locks (`SELECT ... FOR UPDATE`) to prevent two JobTrigger processes from claiming the same job simultaneously.

## Upstream / Downstream

`JobTrigger` (background service) calls `get_due_jobs()` on a polling loop and fires each returned job through `AgentRuntime`. `JobModule.hook_after_event_execution()` calls `update_job()` after execution to record the LLM's `JobExecutionResult` (new status, process entries, next_run_time). The job management API routes in `backend/routes/` call `create_job()`, `get_jobs_by_agent()`, and `delete_job()` to serve the frontend job panel.

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
