---
code_file: src/narranexus/platform/migration/hurry.py
last_verified: 2026-09-06
stub: false
---

## 2026-09-03 — 注释里的两条承诺现在都成立

「finally 里 clear」由 [[applier.py]] 09-03 修订兑现；「Ids are bounded」由
[[../../../backend/routes/migrate.py]] 的长度上限兑现。前端每次尝试换新 id
（[[../../../frontend/src/hooks/useAgentImport.ts]]），重试天然不继承上一次的 stop。

# migration/hurry.py — "stop waiting for this import"

## Why it exists

[[applier]]'s narrative loop summarizes each imported session with ONE
helper-LLM call, **serially**. A 12-session project is therefore 12 sequential
model calls — minutes on a slow provider. The import queue's stop button used to
mean "finish the current project, then stop": correct (never cut a write in
half) but an unbounded wait, which the Owner rejected outright (2026-09-03:
"don't make me wait for the current project — give it a fallback").

This registry is the third option between "wait it out" and "abort and corrupt":
the running apply consults it **before each remaining session** and drops to
`_summarize_session`'s existing no-LLM path (title + raw transcript). Every
session still lands; only the summary quality of the rest of that one project
drops, and `ApplyResult.summaries_degraded` reports how many so the UI can say
so out loud.

## Design decisions

- **In-process and best-effort, deliberately.** The mark must reach the worker
  running that apply. Local — the only deployment where migration exists at all
  (detect/scan 503 on cloud) — is single-process, so it always does. In a
  multi-worker deploy it may miss, and then the import simply keeps its LLM
  summaries and the user waits as before: **degraded speed, never degraded
  data**. Same caveat and pre-flip TODO as the netmind provisioner's in-process
  lock.
- **Ids come from the client** ([[useAgentImport]] mints one per row) because the
  UI has to be able to name a request that is already in flight; the server has
  no handle to hand back mid-stream.
- **Bounded**: apply clears the mark in its own flow, and a 256-entry cap evicts
  the oldest so a client that marks ids it never applies cannot grow it forever.

## Gotcha

- `mark()` before the apply starts also works (the loop reads it per session) —
  that is what makes "stop" race-free: whether the click lands before or during
  the apply, the next session sees it.

Merged with the plugin platform (2026-09-06): pages, drawer panels, sidebar items, commands and agent-row badges come from the frontend registries (`platform/registries`, registered in `platform/builtin.ts`); this file keeps dev's behaviour on top of that.
