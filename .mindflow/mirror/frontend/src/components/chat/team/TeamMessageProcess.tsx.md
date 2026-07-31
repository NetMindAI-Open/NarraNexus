---
code_file: frontend/src/components/chat/team/TeamMessageProcess.tsx
last_verified: 2026-07-31
stub: false
---

# team/TeamMessageProcess.tsx — per-message "view reasoning & tools"

## Why it exists

The roster can only ever show the LATEST turn (`bus_agent_activity` is one
row per member; `start()` resets its event_id each turn). History belongs on
the messages themselves: every agent reply carries its own
`bus_messages.event_id`, and this component is the single-chat
"View reasoning & tools" affordance rebuilt on that handle — same i18n keys,
same [[TurnTimeline]] renderer, so the two surfaces cannot drift.

## How it works

- Rendered by [[TeamChatPanel]] inside the bubble, only for
  `!is_user && event_id` — legacy rows (NULL event_id) degrade to a plain
  bubble with no dead button.
- Open state is local (unlike the roster's controlled expansion): each
  message's disclosure is independent and nothing else needs the selection.
- Fetch via [[useTurnDetail]] — lazy on first open, cached per turn, race-safe.
- Empty timeline → `chat.team.noProcess`; fetch failure →
  `chat.team.detailLoadFailed` (distinct states — a failure must not read as
  "no record", and reopening retries it). Both keys live at `chat.team.*`,
  NOT under `roster.*`, precisely because they are shared with the roster
  (moved 2026-07-31 per PR review).

## Gotchas

The reply text itself is NOT re-rendered here — `isProcessEvent` filters the
timeline to thinking/tool events only; the bubble already shows the reply
(same double-render rule TurnTimeline's header comment states).
