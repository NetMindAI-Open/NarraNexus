---
code_file: src/xyz_agent_context/schema/job_schema.py
last_verified: 2026-04-10
stub: false
---

# job_schema.py

## Why it exists

Background tasks (Jobs) are a first-class concept in NexusAgent — they allow the agent to do work on the user's behalf on a schedule or with a delay, without blocking real-time conversation. This file defines the entire data contract for that system: how jobs are described (`JobModel`), how triggers are configured (`TriggerConfig`), and how the LLM reports back what happened after each execution (`JobExecutionResult`, `OngoingExecutionResult`).

## Upstream / Downstream

`JobRepository` persists and loads `JobModel`. `JobTrigger` (background service) reads due jobs from the repository and fires them through `AgentRuntime`. `JobModule.hook_after_event_execution()` receives the `PathExecutionResult`, asks the LLM to produce a `JobExecutionResult` (or `OngoingExecutionResult` for ONGOING type), then writes that back to the database via `JobRepository`. The frontend Job panel reads `JobModel` data through `api_schema.JobResponse`.

## Design decisions

**Three job types: `ONE_OFF`, `SCHEDULED`, `ONGOING`**: the first two cover standard task scheduling. `ONGOING` was added in January 2026 for polling/monitoring scenarios (e.g., "keep checking until the customer replies"). ONGOING jobs combine `interval_seconds` with a natural-language `end_condition` that the LLM evaluates after each execution.

**`payload` is natural language, not structured parameters**: the execution instruction is a free-form string assembled into a prompt by `JobTrigger`. This was chosen over structured function calls because different agents have different tool sets and the LLM can interpret intent better from natural language than from rigid parameter schemas.

**`clamp_interval_seconds` validator with a 90-day cap**: LLMs occasionally generate unreasonably large interval values (e.g., scheduling a task "in one year"). The validator silently clamps to 90 days (7,776,000 seconds). Similarly, `clamp_next_run_time` in `JobExecutionResult` caps the next run to 90 days in the future. These guards prevent runaway scheduling.

**`JobExecutionResult` is separate from `JobModel`**: it is a lightweight LLM output struct containing only the fields the LLM needs to fill in after execution. Reusing `JobModel` would expose system management fields (embedding, instance_id, etc.) to the LLM prompt unnecessarily.

**`related_entity_id`** makes the Job execution use a specific user's context. When set, `JobTrigger` loads that user's Narrative and social graph instead of the job creator's context. This enables scenarios like "Agent monitors customer X on behalf of the creator".

## Gotchas

**`JobModel.process` is a list of strings**: it is an append-only execution journal, not a status field. Each run adds 2-5 natural-language step descriptions. Over time this list grows unboundedly. There is no automatic truncation — if a SCHEDULED job runs daily for a year, `process` will have 365+ entries.

**`JobStatus.RUNNING`** is set by `JobTrigger` at execution start and should be cleared to `ACTIVE` or `COMPLETED` when execution finishes. If the process dies mid-execution, the job stays `RUNNING` forever. There is a `started_at` field intended for timeout detection, but as of this writing no automatic stuck-job recovery is implemented.

**`TriggerConfig.cron`** is a standard 5-field cron expression but there is no validation of the expression format. An invalid cron string (e.g., `"0 8 * * * *"` with 6 fields) will be stored successfully and then silently fail to parse at execution time.

## New-joiner traps

- `JobModel.limit` is a field with default `10` that appears to be a pagination hint for the repository. It is stored in the database alongside business data. This field was probably intended for API responses and should not have been on the persistence model.
- `OngoingExecutionResult.should_notify` defaults to `False` for ONGOING jobs. Only the final "completed" execution should notify the user. The LLM is responsible for setting `should_notify=True` only when `should_continue=False`.
- Comparing `job.status == JobStatus.ACTIVE` works because `JobStatus` is `str, Enum`. The string `"active"` and `JobStatus.ACTIVE` are equal.
