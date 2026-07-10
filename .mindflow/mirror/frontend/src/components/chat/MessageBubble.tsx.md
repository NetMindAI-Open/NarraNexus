---
code_file: frontend/src/components/chat/MessageBubble.tsx
last_verified: 2026-07-03
stub: false
---

## 2026-07-03 — red error badge (any error surfaces on the bubble)

A red AlertCircle badge sits at the bubble's top corner whenever the message
carries isError (whole turn failed: no reply / silent fallback / expired login
— content IS the error text) OR warnings (reply came through but something
errored). Click opens a Popover explaining the situation (failed vs
finished-with-errors) + the raw error detail. The pre-existing red-bubble/red-
text/amber-warning-list rendering is kept; the badge is the unified, always-
visible entry point the Owner asked for.

## 2026-06-20 — own bubble switched to Carbon (reverses the 2026-05-19 gray rule)

Per the Narra Agent App design ref, the user's own bubble is now the **Carbon
(human) species variant**: `--color-carbon-soft` fill, `--color-carbon-hair`
border, 3px solid `--color-carbon` stripe on the RIGHT — mirroring the AI
bubble's silicon-on-the-LEFT. This **supersedes** the 2026-05-19 "own bubble
stays neutral gray, species reserved for the other party" decision below: the
product reads as an explicit human(carbon)·AI(silicon) dialogue, and a fresh
product decision (Owner) chose that contrast over the multi-user-fan-out
rationale. Both tints flip in dark mode via token redefinition. If multi-user
rooms land later, sender-species disambiguation must be re-solved another way
(it is no longer carried by "own = gray").

**Meta row moved OUTSIDE the bubble** (reverses the 2026-05-19 "footer inside"
note below): the time + copy/download row now sits just below the bubble,
aligned to the bubble's side (own → right, agent → left), so the bubble wraps
only its content and loses the internal footer whitespace — a tighter, more
refined bubble.

## 2026-05-20 — assistant avatar label uses agent name (was hardcoded 'A')

The assistant `RingAvatar` label was a literal `'A'` regardless of which agent
was replying, so every chat looked identical and didn't match the sidebar
`[[AgentList]]` (which derives its label from the first 2 chars of the agent
name). Added an `agentName?: string` prop; `avatarLabel` for assistant messages
is now `agentName?.slice(0, 2) || 'AI'`. `ChatPanel` passes
`agentName={currentAgent?.name || agentId}`. Note: `AgentInfo` carries no avatar
*image* field — initials are the canonical avatar everywhere, so this just
brings the bubble in line with the list.

## 2026-05-19 — NM canonical FinBubble styling

Bubble surfaces rewritten to match the NM design's `FinBubble` (light-blue silicon fill + 3px LEFT species edge for AI, neutral `--nm-own-bubble` gray fill + 3px RIGHT `--nm-own-edge` for own). The species (carbon/silicon) tokens are now reserved for the *other* party — multi-user fan-out semantics: in a future shared room the *receiver* will see the *sender* in a species color, but your own outgoing messages stay gray because you don't need a species cue to identify yourself.

Footer (time + copy/download) moved INSIDE the bubble, bottom-right, mono 9.5px in `--nm-subtle`. All `<BracketEdge>` corner marks removed — the radius + 3px edge stripe carry the species/own signal alone, no top-left rectangle.

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
