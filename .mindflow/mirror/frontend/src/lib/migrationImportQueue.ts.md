---
code_file: frontend/src/lib/migrationImportQueue.ts
last_verified: 2026-09-03
stub: false
---

# lib/migrationImportQueue.ts — sequential batch runner for agent import

## Why it exists

The migration backend is single-source per call: `/api/migrate/scan` extracts
one source, `/api/migrate/apply` writes one agent (see [[migrate.py]]). The
one-page [[ImportAgentModal]] lets the user check N rows, so "import N agents"
is N sequential round-trips driven from the frontend — **no backend change was
needed for multi-select**. The loop lives here, free of React, so its two
user-visible contracts are testable
(`lib/__tests__/migrationImportQueue.test.ts`).

## Design decisions

- **A failing row never stops the queue.** A permission error on project #3 must
  not cost the user #4-#9. The per-item `catch` is one of the legitimate
  exception swallows: the error is not lost, it is surfaced on that row in the
  done summary with a Retry button.
- **`shouldStop` is consulted only BETWEEN rows** (Owner decision 2026-08-27:
  "stop after this one"). Aborting an in-flight `/apply` would leave a
  half-populated agent — created, awareness written, memory partially imported —
  which is worse than waiting out one more row. Rows never started are reported
  `skipped`, and the modal offers to resume exactly those.
- **Sequential, not parallel.** Each apply summarizes sessions into Narratives
  via the LLM; firing 26 of those concurrently would hammer the user's provider
  and make progress unreadable.
- **`scanned` short-circuits the scan step** — a row the user expanded was
  already scanned, so the queue reuses that payload instead of paying for the
  filesystem read twice.
- **`applyImportEdits` is a pure transform**: renamed agent + only the checked
  sessions. `undefined` sessions means "keep all" — an untouched row must import
  everything, and the easiest bug here would be importing nothing.
- **`summarizeBatch` counts successes only**, so the done tiles can never claim
  narratives from a row that failed.

## Gotcha

- Progress is emitted via callback, not returned incrementally; the modal keeps
  a `Record<key, progress>` and renders it in the row order from
  [[migrationDetections]] — so `results[0]` (what [[useAgentImported]] selects)
  is the same agent the "Open …" button names.

## 2026-09-03 — stop no longer means wait

Each item carries an `importId` and `deps.apply` receives it, because "stop" has
three jobs, not one: skip the rows that haven't started, leave the in-flight
write alone (aborting it half-populates an agent) — and **hurry that write**.
[[useAgentImport]] posts the running row's id to `/api/migrate/hurry`, which
makes the server finish it with deterministic session summaries instead of one
model call per session. `summarizeBatch` totals `summariesDegraded` so the done
report can state the trade the user made.
