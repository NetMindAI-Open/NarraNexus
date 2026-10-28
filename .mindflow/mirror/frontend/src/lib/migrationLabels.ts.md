---
code_file: frontend/src/lib/migrationLabels.ts
last_verified: 2026-08-27
stub: false
---

# lib/migrationLabels.ts — shared framework display labels, icons + order

## Why it exists

`FRAMEWORK_LABELS` / `frameworkLabel()` / `FRAMEWORK_ORDER` were copy-pasted in
[[ImportAgentModal]] and [[MigrationGuide]] — a new importable framework had to
be added in several places. Extracted here so they share one source. (Note the
unrelated `FRAMEWORK_LABELS` in `settings/ProviderSummaryCard` is a different
domain — provider frameworks — and intentionally separate.)

`FRAMEWORK_ICONS` / `frameworkIcon()` live here for the same reason — one place
per framework. 2026-08-27 they map to **real brand marks** from
[[FrameworkBrandIcons]] rather than lucide glyphs (Owner decision; design_system
§5 gained the matching exception for third-party product identity). `custom` is a
user-typed folder rather than a product, so it keeps a lucide glyph, and an
unknown framework falls back to `Bot`. Adding a framework stays a one-line change
here instead of an icon-less row downstream.

The mapping lives in this `.ts` file while the components live in a `.tsx` one
because react-refresh forbids mixing component and non-component exports — the
same split as [[modelBrandIcons]].

`FRAMEWORK_ORDER` puts the two coding agents first — `claude_code`, `codex`, then
`openclaw`, `hermes`, `custom` (Owner 2026-08-27): on a developer machine those
two carry almost every detected source, so they belong at the top of the list.
