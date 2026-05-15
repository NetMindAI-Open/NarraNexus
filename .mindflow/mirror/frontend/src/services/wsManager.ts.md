---
code_file: frontend/src/services/wsManager.ts
last_verified: 2026-05-13
stub: false
---

## 2026-05-13 — Reconnect: 注入触发本次 run 的 user bubble

收到 `run_reconnect` 帧（reconnect WS 第一帧）时，如果带了
`input_content`（非空字符串），就**额外**调一次
`chatStore.addUserMessage(agentId, input_content, undefined, tsMs)`，
其中 `tsMs = Date.parse(raw.input_timestamp)`（后端给的是
`events.created_at` 的 ISO）。

为什么时间戳要这么精确：`ChatModule.hook_after_event_execution`
在 run 结束后会把 user 行持久化到 `agent_messages`，使用
`user_ts = event.created_at.isoformat()`——和我们 inject 时用的是
**同一个时间基准**。这样下一次拉 simple-chat-history 时，
ChatPanel 的 `role:content + 300_000ms` dedup 自动匹配上、
match-and-consume 掉那一对，不会出现"两条相同 user 气泡"。
（5min 窗口足够覆盖 DB 端 `datetime('now')` 与 Python `utc_now()`
之间几毫秒的偏差。）

注入只发生一次（run_reconnect 是第一帧，没有重放可能），不需要
idempotency 状态。`input_content` 缺失 / 非字符串 / 空串都跳过——
reconnect 仍正常进行，只是首屏缺一条用户气泡，比 reconnect 整体
崩掉好。

随后照旧走 `translateReconnectFrame`——它对 `run_reconnect` 返回
null，这帧不会再被 processMessage 处理一次。

## 2026-05-13 — Phase C: reconnect() + replay frame translation

新增 `reconnect(agentId, userId, runId, options)` 方法，配合后端
`BackgroundRun` lifecycle 实现 "关 tab → 重开 → 完整历史回放 + live
接续"。语义上对应业内的 **resumable WebSocket session / SSE-style
last-event-id resumption**——event_stream 充当 event store
(event-sourcing)，server-side run 是 LRO (long-running operation)，
WS 是 resumable subscription，而不是请求/响应通道。

行为：
1. `close()` 已有连接（同 agent 不并存两条 WS）
2. 新开 WS → 第一帧 `{run_id, user_id, token}`（注意：不是
   `{agent_id, user_id, input_content, ...}`——后端用 run_id
   存在与否区分 fresh vs reconnect 分支）
3. `startStreaming(agentId)`——让 AgentList spinner / ChatPanel
   live-activity preview 在 replay 期间就能保持 active 状态
4. `onmessage` 走 `translateReconnectFrame()`：
   - `heartbeat` → 跳过
   - `run_reconnect` / `run_ended` / `reconnect_warning` → 协议级
     metadata，返回 null（不进 store）
   - `thinking_partial_replay` `{content}` → 包装成
     `agent_thinking { thinking_content }`
   - `replay {kind, seq, payload}` → 按 kind 反演成 live 对应的
     RuntimeMessage：thinking_segment → agent_thinking；
     text_delta → agent_response/text；tool_call/tool_output →
     progress (running/completed)；progress → 透传后回写
     `type:'progress'`；error → error
   - 其他 type → 视为 live frame 原样透传
5. `run_ended` 或 `complete` 触发 `onComplete` 回调

设计要点：**translation layer 让 chatStore 完全不需要知道是回放还
是 live**——同一份 processMessage 渲染两者。这也意味着不会有
"replay 一段、live 又一段、UI 双轨道"的状态机问题；只有一个
streaming 时间轴。

### Gotcha

- `translateReconnectFrame` 把回放产生的 timestamp 都写成
  `Date.now()`——历史 chat bubble 的顺序由 chatStore 内的 list
  append 顺序决定，而不是 timestamp 排序；server 端 seq ASC 已经
  保证 append 顺序，所以 timestamp 用 `Date.now()` 不影响显示。
  若以后改成 timestamp-sorted UI，需要后端给 replay 附上
  `payload_ts` 字段。
- mock 模式 reconnect 是 no-op（没有 BackgroundRun 可订阅）

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
