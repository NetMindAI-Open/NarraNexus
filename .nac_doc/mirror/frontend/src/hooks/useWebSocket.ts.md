---
code_file: frontend/src/hooks/useWebSocket.ts
last_verified: 2026-04-10
stub: false
---

# useWebSocket.ts — React adapter for wsManager

## Why it exists

`wsManager` is a plain TypeScript class (singleton) that lives outside React. Components need a React-friendly API: `useCallback`-wrapped handlers for stable references, and a reactive `isLoading` value derived from `chatStore`. This hook bridges those two worlds without duplicating any connection logic.

## Upstream / Downstream

Delegates all connection work to `services/wsManager`. Reads `isStreaming` from `useChatStore` to derive `isLoading`. The hook adds no state of its own.

Used by `ChatPanel.tsx`, which calls `run(agentId, userId, inputContent, agentName)` to start a session and `stop(agentId)` to cancel it.

## Design decisions

**Zero connection logic in the hook.** The hook is deliberately a thin shim. All reconnect decisions, message routing, and close handling belong in `wsManager`. If the WebSocket strategy changes (e.g., adding reconnect), only `wsManager` needs to change.

**`isLoading` reads active agent's `isStreaming`.** The flat `isStreaming` field in `chatStore` reflects the active agent's session, not all sessions. `ChatPanel` only cares about its own agent's streaming state, so this is correct for the primary consumer.

**`onComplete` callback in options.** The hook forwards `options.onComplete` to `wsManager.run`. `ChatPanel` uses this to trigger `refreshAll()` from `useAutoRefresh` after the agent finishes.

## Gotchas

**`options.onComplete` in `useCallback` dependency array.** If the parent component creates a new `onComplete` function on every render (common with inline arrow functions), `run` will be a new reference on every render. Consumers should wrap their `onComplete` in `useCallback` to prevent unnecessary `wsManager.run` re-references.

**`isLoading` reflects the ACTIVE agent, not the called agent.** If you call `run('agent-B')` while `agent-A` is active, `isLoading` stays `false` until the user switches to `agent-B`. This is correct for `ChatPanel` (which always shows the active agent) but would mislead a component that displays loading state for a background agent.
