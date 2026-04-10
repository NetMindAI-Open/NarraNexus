---
code_file: frontend/src/components/inbox/AgentInboxPanel.tsx
last_verified: 2026-04-10
stub: false
---

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
