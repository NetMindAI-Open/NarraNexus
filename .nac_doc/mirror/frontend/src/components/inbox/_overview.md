---
code_dir: frontend/src/components/inbox/
last_verified: 2026-04-10
stub: false
---

# inbox/ — Agent-to-agent communication inbox (Matrix MessageBus channel messages)

## 目录角色

Displays messages from the Matrix MessageBus — the inter-agent communication channel. When agents communicate autonomously (e.g., delegating to another agent via the social network module), those messages land here, grouped by room.

The directory contains two panel components:
- `AgentInboxPanel` — the active panel mounted in `ContextPanelContent`. Full dashboard with KPIs, sort, and load-all.
- `InboxPanel` — an older, simpler version. No longer mounted anywhere; effectively dead code.

## 关键文件索引

| File | Role |
|------|------|
| `AgentInboxPanel.tsx` | Active inbox panel. Rooms grouped, sorted newest-first, expandable messages. |
| `InboxPanel.tsx` | Legacy simpler version. Not currently mounted. |

## 和外部目录的协作

- `usePreloadStore`: `agentInboxRooms`, `agentInboxUnreadCount`, `refreshAgentInbox`.
- `useConfigStore`: `agentId`.
- Unread count also drives the badge in `ContextPanelHeader` — the store is the single source of truth.
