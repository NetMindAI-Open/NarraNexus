---
code_file: frontend/src/components/inbox/AgentInboxPanel.tsx
last_verified: 2026-07-03
stub: false
---

## 2026-07-03 — messages sorted via compareInboxMessages (microsecond, ascending)

Per-room message sort switched from an inline ``new Date().getTime()``
(millisecond, DESC) comparator to ``compareInboxMessages`` (lib/inboxOrder),
which compares created_at as microsecond ISO strings and orders ASCENDING
(chat reading order: oldest at top, Q1 A1 Q2 A2). The old millisecond clock
collapsed a turn's 1µs-apart inbound/reply to an equal value, leaving the
reply-vs-question order to chance; the old DESC direction also contradicted
the "chat-style list" the panel renders. Room-list sort (by latest_at) is
unchanged.

## 2026-07-03 — hosts BusFailuresSection (upstream #52)

The panel body now renders `<BusFailuresSection agentId>` above the room
list — the recovery surface for messages the agent permanently gave up
on. Self-hiding when there are no parked failures.

## 2026-06-10 — embedded mode

`embedded` prop drops the outer Card + duplicate title when hosted in
the bookmark drawer's [[ActivityPanel]]; Load-all/Refresh actions stay.
Default rendering unchanged.

## 2026-05-28 — clicking the channel row clears that channel's unread

Pre-fix `toggleRoom` only marked the latest VISIBLE message as read
when the user EXPANDED a room (collapse → nothing happens, no
messages loaded → nothing happens, latest has no message_id →
nothing happens). Combined with the 50-message cap on the inbox
list, channels with > 50 unread had a residual tail that the badge
never zeroed.

New behavior: every click (expand OR collapse) calls
`api.markAgentRoomRead(roomId, agentId)` → backend advances
`last_read_at` to NOW → all that channel's unread cleared. Then
`refreshAgentInbox` is fired so the badge disappears without
waiting for the next poll.

The same change was made to [[InboxPanel.tsx]] — both panels share
this UX.

# AgentInboxPanel.tsx — Matrix MessageBus inbox with dashboard KPIs

## 为什么存在

Shows the agent's received messages from the Matrix inter-agent communication layer. Messages are grouped into rooms (Matrix channels) and sorted newest-first, so the most recent activity is always at the top.

## 上下游关系
- **被谁用**: `ContextPanelContent` (lazy-loaded when 'inbox' tab is active).
- **依赖谁**: `usePreloadStore` (rooms, unreadCount), `useConfigStore` (agentId), `Markdown`, `Badge`, `KPICard` (local inline copy).

## 设计决策

**Load all**: By default, the store loads 50 messages per room. "Load all" calls `refreshAgentInbox(agentId, false, -1)` (limit = -1 = no limit). After loading all, the "Load all" button disappears.

**Room sort**: `sortedRooms` is a `useMemo` that sorts rooms by `latest_at` descending and also sorts each room's messages newest-first. This is separate from the preloadStore sort, which may not guarantee this order.

**KPICard duplicate**: This file has an inline `KPICard` component that duplicates the one in `ui/KPICard.tsx`. This is a known issue — the shared `KPICard` was extracted after this file already had its own copy, and the local copy was not removed.

## Gotcha / 边界情况

`refreshAgentInbox(agentId, false, 0)` on manual refresh resets the stored `_inboxLimit` to the default (50) before re-fetching. Passing `0` is the signal to the store to reset, not to fetch 0 items.

Messages within an expanded room are displayed newest-first — this differs from the chat panel where messages are oldest-first. This is intentional for the inbox use-case (see most recent first).
