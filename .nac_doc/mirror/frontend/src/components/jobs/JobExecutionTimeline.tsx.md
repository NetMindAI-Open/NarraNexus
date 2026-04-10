---
code_file: frontend/src/components/jobs/JobExecutionTimeline.tsx
last_verified: 2026-04-10
---

# JobExecutionTimeline.tsx — Gantt-style execution timeline for jobs

Shows each job as a horizontal bar positioned proportionally between the
earliest start time and latest end time across all displayed jobs.

## Why it exists

Dependency graphs show structure; the timeline shows time. For diagnosing
slow pipelines or overlapping jobs, absolute position and width is more
useful than topology.

## Upstream / downstream

- **Upstream:** `JobNode[]` from `JobsPanel`, which maps `started_at` →
  `started_at` and `completed_at` → `completed_at`
- **Downstream:** click calls `onJobClick(jobId)` → `JobsPanel` shows
  `JobDetailPanel`

## Design decisions

**No library:** Pure CSS absolute positioning with percentage widths computed
from the min/max time range. Keeps the implementation self-contained with no
extra dependencies.

**Running jobs use `Date.now()` as the effective end:** If a job is still
running and has no `completed_at`, the bar extends to the right edge of the
current visible time window, showing progress in real time on refresh.

**Minimum bar width of 2%:** Prevents very short jobs from being invisible.
Similarly, bars with no time data at all (pending status) render a centered
"Waiting..." placeholder instead of a zero-width bar.

## Gotchas

- The time range is computed once per `jobs` change — the timeline does not
  auto-extend in real time. The user must manually refresh or wait for
  `usePreloadStore`'s next poll cycle.
- `animate-pulse` on running bars uses Tailwind's animation class. When
  stripping animated classes for the status label badge (to avoid the label
  itself pulsing), the code does a string `.replace('animate-pulse', '')` on
  the class string — a fragile pattern if the class name changes.
