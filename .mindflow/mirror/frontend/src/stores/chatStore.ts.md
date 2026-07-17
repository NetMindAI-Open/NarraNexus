---
code_file: frontend/src/stores/chatStore.ts
last_verified: 2026-07-14
stub: false
---

## 2026-07-14 — `currentActionReason` 透传确定性自助类错误

会话状态新增 `currentActionReason: string | null`（session-only，未进 flat
derived 字段——MessageBubble 从持久化的 `ChatMessage.actionReason` 读，不需要
flat）。`processMessage` 的 `error` 分支:当 `error_type === 'config_actionable'`
时 latch `errorMsg.action_reason`。`stopStreaming` 在 `isError` 时把它盖到
assistant 消息的 `actionReason` 上（有回复就不打标）。`startStreaming` 与
默认态一并重置为 null。这样前端能对确定性可自助失败（上下文太小/余额/模型）
渲染"你可以做什么"面板，而不是笼统的失败——对应后端
`SELF_SERVICEABLE_ERROR_TYPE`。

## 2026-07-10 — historyRefreshTick / requestHistoryRefresh

Added a global `historyRefreshTick` counter + `requestHistoryRefresh()` action.
`clearAgent` drops an agent's in-memory session, but [[ChatPanel.tsx]] holds
its OWN server-fetched history and only reloads on agent switch — so after a
data wipe ([[wipe_service.py]] / [[AgentList.tsx]]) it kept showing stale
messages. Bumping the tick makes ChatPanel re-fetch (now-empty) history
immediately. Deliberately a single global counter (not per-agent): a wipe is
rare and only the mounted panel reacts, so the extra generality isn't worth it.

## 2026-06-10 — run_started 帧驱动 bookmarkStore.onRunStart

`processMessage` 的 `run_started` 分支新增一行副作用：调用
[[bookmarkStore]] 的 `onRunStart(agentId)`，按 Owner 决策在**新 run
开始时**重置该 agent 的高亮层（badge 层豁免）。选 `run_started` 而非
发送路径的原因：它是权威的新 run 信号（含 cron/job 触发的 run），且
Phase C 重连走 `run_reconnect` 帧、不会发 `run_started`——同一 run 的
重连永远不会误清高亮。

## 2026-05-13 — progress 分支补齐 tool_output 回填（artifact column 实时更新）

之前的 progress 分支只在 `details.tool_name + arguments`（=tool_call
帧）时往 `currentToolCalls` push 一条新条目，**完全没处理 `details.output`
（=tool_output 帧）**。结果 `currentToolCalls[i].tool_output` 永远是
`undefined`，下游所有 `if (tc.tool_output)` 的消费者——
`ArtifactToolCallCards`、MessageBubble 的 reasoning panel、reconnect
replay 后的产物——都拿不到 tool 输出。这是 "artifact 生成后不自动出现，
要刷新页面才看到" 的根因：刷新走 `getSimpleChatHistory` 拉持久化的 tool
输出，inline card 才有数据可渲染、`ensureArtifactLoaded` 才会被触发、
`artifactStore.upsert` 才会被调用。

修复：progress 分支增加一个 `else if (outputStr !== undefined)` 分支，
按 `step` 字段精确匹配并回填到对应 tool_call 的 `tool_output` 字段。

**为什么 `step` 是最优匹配 key**：后端 `response_processor.py:410` 对
tool_call 和 tool_output 用**同样格式**的 step 编号 `3.4.{N}`，N 由
到达顺序决定（call 用 `tool_call_count+1`，output 用 `tool_output_count+1`，
两个计数器一一对应）。因此第 N 个 tool_call 和第 N 个 tool_output 一定
带相同的 step 字符串——比时间戳近邻 / "最近一个空 tool_output" 等
启发式都稳：并发 tool call、replay 乱序、SDK 跨帧重排都不会错配。

`AgentToolCall.step` 字段在 types/messages.ts 加上（optional）；tool_call
帧 push 时存进去，tool_output 帧拿出来按 step 匹配。如果某个老协议
不带 step（不应该发生，但兜底），fallback 到"最近一个无 tool_output 的
tool_call"。

同步往 `currentEvents` push 一条 `type: 'tool_output'` TurnEvent，让
TurnTimeline live 渲染也能看到 "Execution completed" 块——之前那条 case
分支是"死代码"（chatStore 从来不 push），现在终于通电。

reconnect 路径自动兼容：`translateReconnectFrame` 把 replay `tool_output`
翻成 `progress` 帧时保留了原始 `step` 字段，所以重连后 inline 卡片
和 TurnTimeline 都会按 step 重新建立 call↔output 对应关系。

## 2026-05-13 — addUserMessage 加可选 timestampMs

签名扩成 `addUserMessage(agentId, content, attachments?, timestampMs?)`。
默认仍然是 `Date.now()`——fresh-run 调用方都不传，含义不变。

只有 Phase C reconnect 路径会传——把 `events.created_at`（即
ChatModule 后续写进 `agent_messages.user_ts` 的同一个值）作为
user 气泡的 timestamp。这样 ChatPanel 的 `role:content + 300_000ms`
dedup 在 run 结束后从 history 拉回 user 行时能精准合并，避免"两条
完全一样的 user 气泡"。

非有限数会 fallback 到 `Date.now()`（NaN-safe）。

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
