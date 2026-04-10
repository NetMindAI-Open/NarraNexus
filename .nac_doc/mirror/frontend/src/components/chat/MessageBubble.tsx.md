---
code_file: frontend/src/components/chat/MessageBubble.tsx
last_verified: 2026-04-10
stub: false
---

# MessageBubble.tsx — Single message row with lazy-loaded thinking/tool-call details

## 为什么存在

Renders one message in the timeline. Handles two very different data contexts:
1. **Real-time** (session messages): thinking and tool calls arrive inline from the WebSocket.
2. **History** (DB messages): thinking and tool calls must be fetched on demand from `GET /event-log/{event_id}`.

## 上下游关系
- **被谁用**: `ChatPanel`.
- **依赖谁**: `Markdown`, `api.getEventLog`.

## 设计决策

**Lazy event log loading**: History messages carry an `eventId`. The first time the user clicks "View reasoning & tools" (or expands the thinking/tools section), the component fetches `GET /event-log/{event_id}`. Results are cached in a `useRef<Map>` — no store, no prop drilling, component-local cache.

This design avoids loading event log details for every message in a long history page, keeping the history load fast.

**`canLoadEventLog`** flag: `true` only when the message is an assistant message with no real-time data and has an `eventId`. Prevents pointless API calls for user messages or streaming messages.

**Copy and Download**: Available on completed assistant messages only. Download saves as `.md` with a timestamp in the filename.

**Inline `ToolCallItem` and `ToolCallOutput` components**: Defined in the same file because they are tightly coupled to `MessageBubble` rendering and have no other consumers.

## Gotcha / 边界情况

The event log cache (`eventLogCacheRef`) is per-component-instance. If the same message is rendered multiple times (e.g., after re-keying), the cache is lost and the API is called again.

`tool_output` is only present on `EventLogToolCall` (history), not on `AgentToolCall` (real-time WebSocket). The output section only renders for history messages.
