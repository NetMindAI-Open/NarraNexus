---
code_file: frontend/src/components/jobs/index.ts
last_verified: 2026-08-27
---

# index.ts — Public re-export barrel for the jobs directory

Consumers should import from `@/components/jobs` rather than individual file
paths.

## 2026-08-27 — barrel updated by the density rebuild

Added `JobRow` and `JobStatusMeter`. `StatusDistributionBar` was never in the
barrel and its file is now deleted (replaced by [[JobStatusMeter.tsx]]).

`jobStatusVisuals.ts` is deliberately **not** re-exported: it is the jobs
directory's internal status→visual table, not something other areas of the app
should reach for.
