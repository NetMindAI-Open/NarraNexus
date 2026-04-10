---
code_dir: frontend/src/components/chat/
last_verified: 2026-04-10
stub: false
---

# chat/ — Main conversation surface

## 目录角色

Two components: `ChatPanel` (the full interaction surface) and `MessageBubble` (a single message row). Exported as a barrel.

The chat panel is the most complex component in the frontend: it merges DB history (paginated, polled) and real-time WebSocket streaming into a single unified timeline, handles multi-agent concurrent sessions, and renders an inline activity preview during agent execution.

## 关键文件索引

| File | Role |
|------|------|
| `ChatPanel.tsx` | Full chat UI: history loading, unified timeline, streaming, IME handling, bootstrap greeting. |
| `MessageBubble.tsx` | One message row: thinking/tool calls section (real-time or lazy-loaded from event log). |

## 和外部目录的协作

- `useChatStore`: session messages, streaming state, tool calls.
- `useAgentWebSocket` hook: triggers agent runs, surfaces streaming chunks.
- `api.getSimpleChatHistory` + `api.getEventLog`: history pagination and on-demand event details.
- `EmbeddingBanner` from `ui/`: mounted inside `ChatPanel` as a non-intrusive top strip.
