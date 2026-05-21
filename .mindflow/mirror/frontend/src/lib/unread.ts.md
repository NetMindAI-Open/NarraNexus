---
code_file: frontend/src/lib/unread.ts
last_verified: 2026-05-21
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

## Upstream / downstream

- **Used by**: [[AgentList.tsx]] — `getRowMeta` (count) + a `useEffect` that
  marks the active agent read up to its latest message.
- The `completedAgentIds` glowing-dot notification (in `useChatStore` +
  `useAutoRefresh`) is a SEPARATE mechanism for background new-message
  alerts; this module only governs the numeric unread pill.
