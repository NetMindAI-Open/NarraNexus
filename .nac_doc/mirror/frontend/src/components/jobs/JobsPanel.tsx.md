---
code_file: frontend/src/components/jobs/JobsPanel.tsx
last_verified: 2026-04-10
---

# JobsPanel.tsx — Root orchestrator for the Jobs panel

The single place where view-mode switching, status filtering, inline expand,
and cancel are coordinated. It is intentionally large because those concerns
interact: cancelling a running job must also close the expand row and refresh
the list.

## Why it exists

Without this top-level orchestrator, each sub-component would need to share
mutable state (selected job ID, cancelling flag, filter) through props or a
separate store. Keeping it here avoids over-engineering a store for a panel
that is self-contained.

## Upstream / downstream

- **Upstream:** `usePreloadStore` (jobs data, refreshJobs), `useConfigStore`
  (agentId / userId), `api.cancelJob()`
- **Downstream:** `JobExpandedDetail`, `JobDependencyGraph`,
  `JobExecutionTimeline`, `JobDetailPanel`, `StatusDistributionBar`, `KPICard`
- **Consumed by:** right-panel tab layout

## Design decisions

**`jobToJobNode` conversion:** Transforms the API `Job` type into `JobNode`
needed by graph/timeline. Prefers `instance_id` over `job_id` as the node ID
because dependency references use instance IDs.

**Failed-job collapsing:** Separates failed jobs into a collapsible group at
bottom when filter is "all", so active/pending jobs stay visible by default.

**Cancel flow:** Calls native `confirm()` before `api.cancelJob()`. Deliberate
friction because cancels are irreversible. Cancel state is tracked per-job-id
so the loading spinner appears on the correct row.

## Gotchas

- The status filter `'active'` and `'running'` are both "in progress" but are
  different backend states — `active` means the Module instance is alive,
  `running` means the job script is executing. The KPI metric merges both.
- `refreshJobs` must receive `(agentId, userId)` — calling it without
  arguments silently does nothing (preloadStore signature).
