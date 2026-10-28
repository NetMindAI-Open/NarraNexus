---
code_file: frontend/src/hooks/useAgentImport.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 (评审修订) — 每次尝试换新的 importId

原 `??=` 让同一行永远复用一个 id。stop 标记的是 id；上次被 stop（或异常泄漏标记）的行，
Retry 会带着同一个 id 全程降级。现在 `buildItem` 每次生成新 id，且在行进入 `importing`
之前，`requestStop` 拿到的永远是正在跑的那一次。

# hooks/useAgentImport.ts — state behind "import agents from other tools"

## Why it exists

The import picker has two homes: the sidebar's [[ImportAgentModal]] and step 2
of the first-run flow ([[StepImport]]). Only the chrome differs, so the chrome is
what stayed in the components and everything else lives here once — two copies
of this logic is precisely how the old welcome dialog and the import modal
drifted apart.

Owns: detect (or the caller's `initialDetections`), selection, per-row lazy
scan + edits, the manual-folder row, and the sequential queue with its per-row
progress. Composition of the list and the queue mechanics themselves live in
[[migrationDetections]] / [[migrationImportQueue]].

## Design decisions

- **No `onApplied` / `onClose`.** What happens when a batch finishes is the
  caller's business: the modal closes itself, the flow advances a step. A hook
  that navigated would make one of those wrong.
- **`initialDetections` skips the hook's own detect** so the welcome flow does
  not scan the filesystem a second time for the same screen.
- **Group open/closed is tracked as the set of OPEN groups**, not closed ones,
  so nothing has to seed state when detections arrive or change (a `useEffect`
  seeding a "closed" set would fight every rescan). Empty default = every
  multi-row group starts collapsed.
- A manual folder scan is added as a **synthesized detection row** (pre-scanned,
  selected, expanded) rather than a separate mode — same list, same mental model.
- `progressRows` is ordered by the rendered row order, and `batch` is derived
  from it, so `results[0]` is the same agent the "Open …" button names.

## Gotcha

- LOCAL ONLY: detect/scan read the user's filesystem and 503 on cloud. Every
  caller is responsible for mounting in local mode only.

## 2026-09-03 — requestStop hurries the running row

`requestStop` mints-and-remembers a per-row `importId` (so a row that is already
mid-write can be named) and, alongside setting the stop flag, posts that id to
`/api/migrate/hurry`. Owner objection 2026-09-03: waiting out the current
project could be minutes, since sessions are summarized one LLM call at a time.
The hurry is fire-and-forget — if it never reaches the worker running the apply,
the import keeps its summaries and behaves exactly as before.
