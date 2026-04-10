---
code_file: frontend/src/components/jobs/JobDetailPanel.tsx
last_verified: 2026-04-10
---

# JobDetailPanel.tsx — Read-only detail pane for a selected job node

Displays the key fields of a `JobNode` (ID, task key, description, depends-on
badges, start/end times, output) below the graph or timeline when a node/bar
is clicked.

## Why it exists

The graph and timeline both need a way to inspect a selected node without
opening a modal. A slide-in pane below the canvas keeps context visible.

## Upstream / downstream

- **Upstream:** `job: JobNode | null` from `JobsPanel`'s `selectedJob`
- **Downstream:** `onClose` callback clears `selectedJobId` in `JobsPanel`
- **Used by:** `JobsPanel` (graph view, timeline view)

## Gotchas

This component works on `JobNode`, not the raw API `Job` type. It therefore
only shows fields that `jobToJobNode` copies over. For the full field set
(payload, trigger config, process log, error), the user must switch to list
view and use `JobExpandedDetail`.
