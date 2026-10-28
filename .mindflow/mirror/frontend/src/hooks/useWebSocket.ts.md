---
code_file: frontend/src/hooks/useWebSocket.ts
last_verified: 2026-08-24
stub: false
---

## 2026-08-24 — 暴露 `steer`(仅此一个,不再 wrap `isSteerable`)

照 `stop` 手法 wrap `wsManager.steer(agentId, content, clientMsgId)`(返回是否真发出),供 [[ChatPanel.tsx]] 运行中发送用。**不**wrap `isSteerable`:ChatPanel 用 store 的响应式 `currentSteerable` 门控 composer(快照读取不会随 run_started 重渲染),`steer()` 自身在发送时再查活性;`wsManager.isSteerable` 作为公开面留给非 React 调用方(#354 云端交付/team 面板),但 hook 层为一个不存在的调用方建 wrapper 是 dead surface,已删。

## 2026-08-14 — chat fast mode: run() 增第 6 参 fastMode

`run(agentId, userId, inputContent, agentName?, attachments?, fastMode?)`
——可选布尔原样进 wsManager.run 的 options.fastMode（[[useFastMode]] 是
状态源，[[wsManager]] 负责上 wire）。缺省 undefined = 今日行为。

## 2026-05-13 — Phase C: 增加 reconnect 回调

hook 现在导出三个动作：`run / reconnect / stop`（外加 `close` 和
`isLoading`）。`reconnect(agentId, userId, runId, agentName?)` 转发
到 `wsManager.reconnect`——本身依然是 thin shim，没有业务逻辑。

新行为：ChatPanel mount 时若检测到 `currentAgent.active_run.run_id`
非空且本地 `isLoading` 为 false，就调一次 `reconnect`——这是 Phase C
全恢复语义的前端入口。具体 useEffect 在 ChatPanel.tsx 里。

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
