---
code_file: frontend/src/lib/migrationDetections.ts
last_verified: 2026-08-27
stub: false
---

# lib/migrationDetections.ts — selection + ordering rules for the import list

## Why it exists

The one-page [[ImportAgentModal]] turns a raw `/api/migrate/detect` response
into a checkbox list. *Which* rows arrive pre-checked and in *what order* are
product decisions, not layout — with 26 Claude Code projects on a machine they
decide what the user actually imports. Extracted here so they are unit-testable
without rendering a modal (`lib/__tests__/migrationDetections.test.ts`).

## Design decisions

- **`detectionKey` = `framework::path`** — `/detect` never repeats that pair, so
  it doubles as React key and selection id. The manual-path row synthesizes a
  detection so it keys the same way as a detected one.
- **`defaultSelection` = confidence `high` AND ≥1 session, minus the
  shared-config fallback** (Owner decision 2026-08-27). Every checked row costs
  one LLM summarization pass on apply, so the long tail stays unchecked.
  Exception: a lone detection is pre-checked — there is nothing to choose
  between, and making the user click once more to accept the only option is
  friction for its own sake.
- **`groupDetections` sorts richest-source-first inside a group** (sessions desc,
  then title). Detector order is filesystem order, which is meaningless to a
  user staring at 26 identically-labeled Claude Code rows.
- **An unknown framework still gets a group** — a backend that learns a new tool
  before the frontend does must not make those rows disappear silently.
- **`detectionTitle`**: folder name for Claude Code project rows (the only thing
  that distinguishes them), the framework label otherwise. `flattenGroups` gives
  the modal the exact row order for the import queue, so progress rows appear
  where the user saw them.

## Gotcha

- `sessionCount` parses the detector's `sessions:N` string signal — it is an
  estimate. The real count only exists after `/scan`, so the modal prefers the
  scan when a row has been expanded.
