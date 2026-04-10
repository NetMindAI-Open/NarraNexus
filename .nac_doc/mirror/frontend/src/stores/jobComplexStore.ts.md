---
code_file: frontend/src/stores/jobComplexStore.ts
last_verified: 2026-04-10
stub: false
---

# jobComplexStore.ts — DAG job group state and exponential-backoff polling

## Why it exists

The backend supports "job complexes" — groups of interdependent jobs arranged as a DAG (directed acyclic graph). This store manages which group is currently being viewed, maps raw API `Job` objects into `JobNode` format that the dependency graph renderer understands, and polls for status changes with exponential backoff.

It also houses the hardcoded `JOB_TEMPLATES` list (Company Analysis, PR Impact Analysis, Multi-Platform Publishing) that seeds the job creation dialog. Templates are static data that currently lives here for colocation with the store; they could be moved to a constants file if the list grows.

## Upstream / Downstream

Calls `api.getJobs(agentId, userId)` (`GET /api/jobs`). The `jobToJobNode` adapter function parses `job.payload` JSON to extract `depends_on` and `task_key` fields that the backend embeds inside the payload string.

Consumed by `JobsPanel.tsx` (triggers `loadJobs`, reads `jobs`, manages `startPolling`/`stopPolling` on mount/unmount), `JobDependencyGraph.tsx` (reads `jobs` to render the node graph), `JobDetailPanel.tsx` (reads `selectedJobId`, calls `selectJob`), and `JobTemplateSelector.tsx` (imports `JOB_TEMPLATES`).

## Design decisions

**Exponential backoff built into the store.** Rather than using a fixed interval, each poll cycle compares the current job status snapshot with the previous one. If nothing changed, `pollAttempt` increments and the next delay is `min(3000 * 1.5^pollAttempt, 30000)`. A state change resets `pollAttempt` to 0 (back to 3-second polling). This avoids unnecessary load when jobs are stuck waiting and recovers responsiveness the moment something changes.

**Auto-stop when all terminal.** If every job in the group has reached `completed`, `failed`, or `cancelled`, polling stops automatically. No manual teardown needed from the component.

**`clearJobs` is a full reset.** It cancels any pending timer and zeros all state. Called when the user switches agents or leaves the jobs panel.

**`jobToJobNode` parses payload defensively.** `job.payload` is a raw JSON string. The function wraps the parse in a try/catch and falls back to `job.job_id` as `task_key` and empty `depends_on` if parsing fails. This avoids hard crashes when jobs have non-JSON payloads.

**`JOB_TEMPLATES` is exported from this file.** Colocation makes it easy to find. If templates ever become user-configurable (fetched from the server), this export disappears and callers are updated.

## Gotchas

**`startPolling` returns immediately if already polling.** Calling it a second time (e.g., from `useEffect` firing twice in StrictMode) is a no-op because of the `if (isPolling) return` guard. The timer is NOT restarted with the new `agentId`/`userId`. If the user switches agents without calling `clearJobs` first, polling continues for the old agent.

**`pollingInterval` state field is largely decorative.** The actual delay is computed inline in the `poll` closure from `BASE_INTERVAL` and `pollAttempt`. `setPollingInterval` writes to `pollingInterval` but that value is not read by the `poll` closure. This is a leftover from an earlier design — the exposed field does not affect behavior.

**`depends_on` uses `task_key` strings, not job IDs.** The dependency graph renderer expects `depends_on` to reference `task_key` values (like `'init'`, `'financial'`). If a job's payload does not include `task_key`, the node falls back to `job_id` as its key, which will not match the `task_key` strings in other nodes' `depends_on` arrays, breaking the graph layout.
