---
code_file: frontend/src/components/jobs/StatusDistributionBar.tsx
last_verified: 2026-04-10
---

# StatusDistributionBar.tsx — Stacked proportional bar showing job status mix

Simple display-only component. Renders a thin colored bar divided into
segments proportional to each status count, with a legend below.

Used by `JobsPanel` at the top of the panel to give an at-a-glance health
snapshot before the user scrolls into the job list. Returns `null` when there
are no jobs.
