---
code_file: frontend/src/lib/migrationLabels.ts
last_verified: 2026-07-30
stub: false
---

# lib/migrationLabels.ts — shared framework display labels + order

## Why it exists

`FRAMEWORK_LABELS` / `frameworkLabel()` / `FRAMEWORK_ORDER` were copy-pasted in
[[ImportAgentModal]] and [[MigrationGuide]] — a new importable framework had to
be added in several places. Extracted here so they share one source. (Note the
unrelated `FRAMEWORK_LABELS` in `settings/ProviderSummaryCard` is a different
domain — provider frameworks — and intentionally separate.)
