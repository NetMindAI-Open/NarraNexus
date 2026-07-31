---
code_file: frontend/src/components/chat/team/useTurnDetail.ts
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 (PR review) — failure is 'error', not 'empty', and retryable

A network/server failure used to settle as `kind: 'empty'` — rendered as
"no process record", a false statement about a turn that has one, cached
forever. Now it settles as `kind: 'error'` (consumers show
`chat.team.detailLoadFailed`) and clears the request marker, so the next
open re-fetches instead of hitting the cache line.

# team/useTurnDetail.ts — one finished turn's process, fetched once

## Why it exists

Extracted verbatim from [[TeamRosterPanel]]'s private `useMemberTurnDetail`
when the transcript's per-message disclosure ([[TeamMessageProcess]]) grew the
identical need. Both consumers live in a room that re-renders every second and
re-polls every 3s — a request per render would be a request storm on a detail
that cannot change once written.

## Design decisions (inherited from the roster hook — see its 2026-07-30 review)

- **Fetch keyed by `agent:event`, once per key.** The key is also the race
  arbiter: a response whose turn is no longer current is dropped, never
  painted under a newer turn's header.
- **"Loading" is not a stored state** — it is the absence of a settled result
  for the current key (a synchronous setState in an effect is an eslint error
  here).
- **No unmount-scoped `alive` flag**: collapsing mid-flight would strand the
  row on the spinner forever (re-expanding hits the "already requested" line).
- Exports `isProcessEvent` (thinking/tool_call/tool_output filter) so both
  consumers cut the timeline identically before handing it to [[TurnTimeline]].
