---
code_dir: frontend/src/components/jobs/
last_verified: 2026-04-10
---

# jobs/ — Job management panel (list, dependency graph, Gantt timeline)

Jobs are the task-scheduling primitive of NexusAgent. A Job can be a one-shot
request, a cron-scheduled run, or a node in a dependency chain that the
ModulePoller executes after its predecessors complete.

## Why three view modes exist

A flat list is fine for quick triage. But understanding *why* a job is blocked
requires seeing the dependency graph, and comparing *when* jobs ran requires
the Gantt timeline. All three views share the same `JobNode[]` slice converted
from `usePreloadStore`, so switching costs zero extra network requests.

## Component tree

```
JobsPanel                        ← root; owns viewMode, filter, cancel state
  ├── StatusDistributionBar      ← stacked proportional bar across all statuses
  ├── KPICard (×4)               ← active / success / failed / rate
  ├── [list view]
  │     └── JobExpandedDetail    ← inline accordion on row click
  ├── [graph view]
  │     ├── JobDependencyGraph   ← React Flow canvas, topological layout
  │     └── JobDetailPanel       ← slides in below graph on node click
  └── [timeline view]
        ├── JobExecutionTimeline ← Gantt-style bars keyed on start/end times
        └── JobDetailPanel       ← same panel, below timeline on bar click
```

`JobDetailPanel` is shared across graph and timeline views.

## Upstream / downstream

- Data source: `usePreloadStore` — jobs polled in the background.
- Mutation: `api.cancelJob()` called directly from `JobsPanel`.
- Consumed by: right-panel layout that tabs between Jobs / Runtime / Skills /
  System pages.

## Gotchas

- The `depends_on` field falls back to parsing `job.payload` JSON for backward
  compat (old backend stored dependencies in the payload blob).
- In "all" filter mode, failed jobs are collapsible at the bottom so live
  jobs aren't pushed off-screen by historical failures.
- `JobTemplateSelector` is exported from this directory but is currently not
  wired to the main panel — it was built for a future "create job from
  template" flow and is not rendered anywhere in the main layout yet.
