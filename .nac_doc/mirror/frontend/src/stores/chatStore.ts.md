---
code_file: frontend/src/stores/chatStore.ts
last_verified: 2026-04-10
stub: false
---

# chatStore.ts — Multi-agent concurrent session state

## Why it exists

Every agent can run concurrently in the background. A single flat "current session" state would force the user to wait for one agent before talking to another. `chatStore` solves this with an `agentSessions` map keyed by `agentId`, giving each agent an independent bubble of streaming state, message history, tool calls, and errors.

## Upstream / Downstream

Fed by `wsManager.ts` — when a WebSocket message arrives, `wsManager` calls `useChatStore.getState().processMessage(agentId, message)` directly, bypassing React lifecycle entirely. The connection-to-store pipeline is: backend → WebSocket frame → `wsManager.onmessage` → `chatStore.processMessage`.

Consumed by `ChatPanel.tsx` (reads `messages`, `isStreaming`, `currentSteps`, `history`), `AgentCompletionToast.tsx` (reads `toastQueue`), `useAutoRefresh.ts` (calls `isAgentStreaming`, writes to `completedAgentIds` and `toastQueue` when background polling detects a new server-initiated turn), and `useAgentWebSocket.ts` (reads `isStreaming` to surface `isLoading`).

Depends on `@/lib/utils` for `generateId` and `@/types` for the `RuntimeMessage` discriminated union.

## Design decisions

**Flat field projection.** Every Zustand `set()` call runs through a custom wrapper that re-derives the flat top-level fields (`messages`, `isStreaming`, `history`, etc.) from the active agent's session after each update. This lets legacy consumers read flat fields without knowing about the multi-agent session map.

**Shared frozen default.** Sessions that do not yet exist return the single frozen object `DEFAULT_AGENT_STATE` rather than allocating new arrays on every access. This avoids reference churn in components that subscribe to session data before an agent has ever been opened.

**No persistence.** Deliberate: in-flight streaming state does not survive a page reload. Conversation history is re-hydrated from `preloadStore` (backed by the server) on mount, not from localStorage.

**`send_message_to_user_directly` as display content.** The agent's final visible reply is extracted by filtering tool calls whose name ends with that string. The store is otherwise agnostic to tool semantics — all tool calls are stored but only this specific one populates the chat bubble.

**Rejected: separate stores per agent.** Would require dynamic store creation and explicit cross-store wiring for the toast/badge system. A single store with a session map is easier to subscribe to and requires no lifecycle management for agent removal.

## Gotchas

**Stale `entry` reference in `wsManager`.** The `onclose` callback captures `entry` from the closure and checks `this.connections.get(agentId) === entry` before deciding whether to call `stopStreaming`. If `close()` or a second `run()` already replaced the map entry, reading from the map would target the wrong session. This race was the root cause of phantom "unexpected disconnect" warnings in early multi-agent builds.

**`stopStreaming` deduplication guard.** Both the `complete` message handler and `ws.onclose` may call `stopStreaming`. The store guards with `if (!session.isStreaming) return {}` so only the first caller commits the history round and assistant message.

**Background agent toast lifecycle.** When `stopStreaming(agentId)` fires for a non-active agent, it pushes to both `completedAgentIds` and `toastQueue`. Consumers must call `dismissToast(agentId)` after displaying the toast and `clearCompletedNotification(agentId)` when the user switches to that agent. Omitting either leaves stale badge indicators permanently.

**`processMessage` silently drops unknown types.** If a future backend version emits an unrecognized message type, the store does nothing. If the backend stops sending a `complete` message (protocol change), the session stays `isStreaming: true` until the WebSocket closes and `onclose` triggers `stopStreaming` as a fallback.
