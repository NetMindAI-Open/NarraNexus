---
code_file: frontend/src/lib/buildTimeline.ts
last_verified: 2026-07-03
stub: false
---

## 2026-07-03 — carry error state (isError/warnings) through the timeline

TimelineItem gained isError + warnings, and toSessionItem now copies them.
The May-2026 unified-timeline refactor defined TimelineItem WITHOUT these two
fields, so a failed turn lost its error flag on the session→timeline hop and
rendered as an innocuous message — the red error bubble/warning list (still
live in MessageBubble) silently stopped firing. This re-connects the data.

# buildTimeline.ts — pure history⊕session merge + dedup for the chat timeline

## Why it exists

The chat view shows one chronological conversation, but the data comes
from two independently-produced sources:

- **history** — `agent_messages` rows from `getSimpleChatHistory`
- **session** — live messages in `[[chatStore.ts]]` (the user prompt added
  on send; the assistant reply assembled at `stopStreaming` from the
  `send_message_to_user_directly` tool args)

Once a turn finishes it exists in **both** — history has the persisted
copy, session still holds the live copy. Something has to drop the
session copy so the turn renders once. That "something" used to be a
~50-line block buried in a `ChatPanel` `useMemo`. It was extracted here
as a pure function specifically because the dedup has caused **two**
production bugs and needed to be unit-testable in isolation
(`__tests__/buildTimeline.test.ts`).

## The dedup — event_id first, content heuristic only as fallback

**Primary key: `(role, event_id)`.** Every message of a turn — the user
prompt and the assistant reply — is persisted with that turn's
`event_id`. `[[chatStore.ts]]` now stamps the SAME `event_id` onto the
session copies (`setCurrentRunId` backfills the user prompt from the
`run_started` frame / reconnect; `stopStreaming` stamps the assistant
reply). `${role}:${event_id}` is therefore an exact, formatting-immune
identity.

This replaced a `${role}:${content}` exact-string key. That key silently
missed whenever the session-assembled content and the DB-persisted
content drifted by even one character — and they ARE produced by
different code paths (session joins `send_message_to_user_directly` args
with `\n\n`; the backend persists independently and sometimes *rewrites*
content, e.g. owner-notify substitution in `agents_chat_history.py`).
That drift made the latest reply occasionally render twice — the bug
this file fixes.

**Why not fall through from event_id to the content heuristic:** if a
session message HAS an `event_id` but history doesn't carry that
`(role, event_id)` yet (turn just finished, history hasn't reloaded), we
render the session copy and stop. event_id is authoritative — falling
through could false-positive against an unrelated row with the same
text, *or* false-negative and vanish the just-finished reply.

**Fallback — `(role, content)` + 5-min window + match-and-consume:** used
ONLY for messages with no `event_id` (legacy history rows; a session
message created before `run_started` landed). The *consume* step (splice
the matched history timestamp) preserves Bug-19 semantics: a user who
sends the exact same text twice gets each session copy paired one-to-one
with a history row — the retry is not swallowed.

## Upstream / Downstream

- **Called by**: `[[ChatPanel.tsx]]` — one `useMemo(buildUnifiedTimeline,
  [historyMessages, messages])`.
- **Depends on**: nothing but types — pure, no React, no store, no I/O.
- **Owns**: the `TimelineItem` type (moved here from ChatPanel).

## Gotcha

The two history index structures (`historyEventRoleKeys` set,
`historyByContentKey` map) are built from `items` *after* only the
history rows have been pushed and *before* the session loop. If you ever
reorder those phases, the session messages would index themselves and
dedup against each other — don't.
