---
code_dir: frontend/src/services/
last_verified: 2026-04-10
stub: false
---

# services/ — Singleton non-React services

## Directory role

Long-lived services that manage connections or state outside the React lifecycle. Currently contains only `wsManager.ts`. Files here are plain TypeScript classes or objects — no hooks, no components. They are designed to outlive any individual component mount/unmount cycle.

## Key file index

| File | Responsibility |
|------|---------------|
| `wsManager.ts` | Singleton `WebSocketManager` class — owns all WebSocket connections, one per agent, routes messages to `chatStore`. |

## Collaboration with other directories

`wsManager` writes directly to `chatStore` via `useChatStore.getState()`, bypassing React subscriptions for performance. It reads `configStore` for the JWT token and `runtimeStore` (`getWsBaseUrl`) for connection URL. The `hooks/useWebSocket.ts` adapter is the React-layer entry point for components.
