---
code_file: frontend/src/services/wsManager.ts
last_verified: 2026-04-10
stub: false
---

# wsManager.ts — Singleton multi-agent WebSocket manager

## Why it exists

WebSocket connections should not be tied to a React component's lifecycle. If `ChatPanel` unmounts (user switches tabs) while an agent is running, the connection must stay alive and messages must keep flowing to `chatStore`. A singleton class that lives outside React solves this. It also manages concurrent connections — one per agent — so multiple agents can run in parallel without stepping on each other.

## Upstream / Downstream

Reads `getWsBaseUrl()` from `stores/runtimeStore` on every `run()` call (fresh, no caching) so mode switches take effect on the next session. Reads `useConfigStore.getState().token` at connection time to inject JWT into the first WebSocket message. Writes to `chatStore` via `useChatStore.getState().processMessage(agentId, message)` and `stopStreaming(agentId, agentName)`.

Entry point for callers is `hooks/useWebSocket.ts` (React adapter) and potentially direct `wsManager.run()` calls from non-React contexts.

## Design decisions

**JWT in first message, not in headers.** Browser's `WebSocket` constructor does not support custom headers. Auth is piggy-backed on the first JSON payload sent in `ws.onopen`. The backend reads `token` from this payload; local mode ignores it.

**`completed` flag on `ConnectionEntry`.** When the connection closes, the `onclose` handler checks whether the closure was expected (`entry.completed = true`) or unexpected (`entry.completed = false`). Only unexpected closures trigger `stopStreaming` with an error state. Calling `close()` marks the entry as completed before closing, so it does not appear as an error.

**`run()` closes existing connection before opening a new one.** If the user re-submits while an agent is still streaming, the old connection is terminated cleanly before the new one starts.

**`stop()` sends a JSON message, does not close.** The backend's WebSocket handler expects a `{ action: 'stop' }` message to cancel the running agent loop via `CancellationToken`. The connection stays open until the backend sends `complete` or `cancelled`, at which point the normal flow finalizes the session.

**`onclose` uses closure-captured `entry`, not map lookup.** After `close()` or a new `run()`, the map may already hold a new entry for the same `agentId`. Using the closure-captured reference prevents the wrong entry from being cleaned up.

## Gotchas

**No automatic reconnect.** If the connection drops unexpectedly (network glitch, server restart mid-run), `onclose` fires, `stopStreaming` is called, and the session ends with whatever partial state was collected. There is no retry logic. The user must re-submit.

**`stop()` is a no-op if the connection is not OPEN.** If called between `run()` invocation and `ws.onopen`, `readyState` is `CONNECTING` and the `stop` message is never sent. The user's stop request is silently dropped. In practice, the user rarely clicks stop within the first ~50ms.

**Heartbeat messages are silently skipped.** The `onmessage` handler returns early for `type === 'heartbeat'`. If the backend changes the heartbeat format, it may be routed to `processMessage` as an unknown type (and dropped by the switch default) rather than causing an error.

**`onCompleteCallbacks` are deleted after first use.** The callback is stored by `agentId` and deleted when `complete` fires. If `run()` is called again for the same agent with a new `onComplete`, the old callback is overwritten. There is no multi-subscriber support.
