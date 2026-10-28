---
code_file: frontend/src/lib/unread.ts
last_verified: 2026-08-14
stub: false
---

# unread.ts — agent-sidebar unread bookkeeping

## Why it exists

Extracted so the sidebar's "unread count" logic is a pure, testable unit
(and decoupled from the Awareness indicator it used to be tangled with).

The bug it fixes: the unread count was computed against
`lastSeenAwarenessTime:<aid>`, a marker written only when the user opened
the Awareness tab — never when they read the chat. The count zeroed only
while the agent was the active row and reappeared on switch-away, because
nothing advanced a "read" marker. This module owns a dedicated, monotonic
`lastReadMessageTime:<aid>` marker that reading advances.

## Design decisions

- **Monotonic marker.** `markAgentRead` never moves the marker backwards, so
  a late-arriving older message can't "un-read" what the user already saw.
- **Strictly-newer comparison.** `countUnread` counts non-user messages with
  `timestamp > lastReadMs` (equal = already read).
- **Best-effort persistence.** All localStorage access is try/caught — unread
  is a nicety, never a hard failure.

## 2026-08-14 — team rooms get a mark, and it is a dot

A team room is an async space by design: the user hands it work and leaves.
Without a mark, leaving was a one-way door — the sidebar row looked identical
whether six agents had been talking for ten minutes or nothing had happened,
so the only way to find out was to open every room and read.

**Why a dot and not a count.** The count would have to come from the server,
and the server cannot count "unread on this device": the watermark lives in
localStorage, per device, and a list endpoint parameterised by N watermarks is
a worse API than a boolean the client derives itself. So the work splits —
`_team_room_activity` (see [[teams.py]]) answers *when this room last said
something worth returning for*, this module owns the watermark it is compared
against, and `teamHasUnread` is the comparison.

**Two different rules for "what counts", on purpose.** The server EXCLUDES the
user's own messages (sending one would mark the room you sent it from) and the
platform's own notices (a bulletin notice fires on the user's own edit). The
client's `latestTeamMessageMs` INCLUDES everything, because it answers a
different question: not "is this worth a mark" but "what has the user looked
at" — and a line rendered in front of them has been looked at whoever wrote it.
Marking less than what is displayed would leave a room that only narrated
itself permanently marked with nothing the user could do to clear it.

**Its own key space** (`teamLastReadMessageTime:`), not shared with the agent
marker. Not because a collision is known to be possible — it could not be
established from the code either way — but because being wrong is silent: one
entity would clear the other's mark, and the symptom reads as a bug in the
counting rather than in the key.

Two surfaces advance the same watermark: [[AgentList.tsx]] up to what the LIST
response reported (so opening a room clears its row), and
[[TeamChatPanel.tsx]] up to what is actually on screen, every poll. Both are
monotonic, so whichever is further ahead wins and neither can undo the other.

## Upstream / downstream

- **Used by**: [[AgentList.tsx]] — `getRowMeta` (count) + a `useEffect` that
  marks the active agent read up to its latest message.
- The `completedAgentIds` glowing-dot notification (in `useChatStore` +
  `useAutoRefresh`) is a SEPARATE mechanism for background new-message
  alerts; this module only governs the numeric unread pill.
