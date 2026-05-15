---
code_file: frontend/src/components/artifacts/renderers/CsvRenderer.tsx
last_verified: 2026-05-14
stub: false
---

## 2026-05-14 — drop `version` prop, fetch via `useArtifactRawUrl`

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

Renderer no longer takes a `version` prop. Uses `useArtifactRawUrl` for the
token-protected public URL; `fetchArtifactText` no longer adds an Authorization
header (raw is on the JWT-bypassed `/api/public/artifacts/...` prefix and the
HMAC token in the path is the auth).

# CsvRenderer.tsx — Tabular renderer for text/csv artifacts

## Why it exists

Fetches agent-generated CSV and renders it as a scrollable HTML `<table>` so users can inspect tabular data inline without downloading the file. Agent-generated CSVs are typically small (a few hundred rows at most), so a simple all-in-memory approach is fine.

## Upstream / Downstream

- **Used by**: `ArtifactColumn` via `React.lazy`, dispatched when `artifact.kind === 'text/csv'`.
- **Calls**: `rawUrl()` from `@/types/artifact` for the fetch target.

## Design decisions

**Naive comma-split parser (`parseCsv`).** Does not handle RFC 4180 quoted fields (e.g., `"hello, world",next`). Agent-generated CSVs that need the full spec should use a proper parser like `papaparse`. The parser is isolated in a pure function at the top of the file, so swapping it out requires changing exactly one line without touching the component.

**First row treated as header unconditionally.** There is no heuristic to detect whether a header row exists. Agents that emit headerless CSVs should add a header row. This is a conscious trade-off: guessing is error-prone and the agent is the authority on its own output format.

**`overflow-auto` on the wrapper, not the table.** The table uses `border-collapse` which can conflict with `overflow` clipping on the table element itself. Wrapping in a `<div>` with `overflow-auto` avoids that CSS quirk.

## Gotchas

Very large CSVs (thousands of rows) will render slowly and occupy a lot of DOM nodes. No pagination or virtualisation is implemented. This is acceptable for agent-emitted tabular results; production data import pipelines need a different component.

**Empty CSV guard (I6, 2026-05-09)**: An explicit `rows.length === 0` check was added before the `const [header, ...body] = rows` destructuring. Without it, an agent that emits a zero-byte or whitespace-only CSV file would produce `rows = []` after `parseCsv()`, and `header.map(...)` would throw `TypeError: Cannot read properties of undefined`. Now an empty CSV renders a `"(Empty CSV)"` placeholder instead of crashing.
