---
code_file: frontend/src/services/wsManager.ts
last_verified: 2026-09-08
stub: false
---

## 2026-09-08 — 注释同步

注释里的 ChatModule 方法名改为能力契约词汇（`after_turn`），无行为变化。

## 2026-08-30 — `translateReconnectFrame` 带上独白档位（缺口已补）

两个 replay 分支（`thinking_partial_replay` / `replay.thinking_segment`）现在
都带档位：[[run_recorder]] 按 tier 写 `thinking_segment` /
`thinking_segment_monologue` 两种 kind，[[broadcaster]] 的在途 partial 也带
`monologue` 布尔，本文件翻译回 `AgentThinking`。

**于是运行中刷新不再有色阶断层**——重放出来的前半截和重连后的新帧同档，
[[chatStore]] 直接合并成一块。

**改这里的人先看这条**：`AgentThinking.monologue` 是**子集字符串**不是 bool。
tier 为真的帧必须把自己的正文**重复**进这个字段（谓词是
`monologue === thinking_content`）；塞 `true` 会拿 bool 和 string 比，恒 false，
改完看起来「没生效」。

`thinking_segment`（无 monologue 后缀）同时覆盖两种情况：provider 推理，以及
**本改动之前写入的存量行**——那些行的档位确实未知，回放成 receded，也正是它们
一直以来的样子（无回填，铁律 #2/#6）。

## 2026-08-26 — `translateReconnectFrame` 回放兜底 step id 不得撞相位 id

`tool_call` / `tool_output` 回放帧在 payload 解析失败(`safeParseJson` 返 null)
时,合成 progress 的 step id 兜底值从 `'3.4'` 改成 `'3.4.replay'`。

**这是一条约束,不是流水账**:`'3.4'` 是 run-agent 相位的 id。currentSteps
按 step id upsert([[chatStore]] / [[useRunObservation]]),裸 `'3.4'` 会**覆盖
真正的 run-agent 相位行**——`tool_output` 分支合成的是 `status:'completed'`,
于是直播中途面板显示「Agent 运行中… ✓」。兜底值必须是 `3.4.x` 形状的子步:
`parseFloat('3.4.replay')` 仍是 3.4(落定逻辑不变),但不在
[[processShared]] 的 `PHASE_STEP_IDS` 白名单里,永远渲染成工具行、绝不成相位
行。**谁把它当冗余魔法字符串「清理」回 `'3.4'`,就复现上面的 bug。**
测试 `wsManager.reconnect.test.ts` 钉住「不得撞相位 id」这条约束本身。

## 2026-08-24 (review #349) — 续接锚点只认 running;时间戳解析归一

- **I2**:`markResumedRun` 加 `raw.state === 'running'` 守卫——后端对
  **任何**可 attach 的 run 都先发 run_reconnect,「早已结束」是常见
  情形,不加守卫会对昨天完成的 run 打出「已续接进行中 · 已运行
  1500 分钟」(chip 要消灭的误判反向复现)。判定条件与后端补发
  run_ended 的分支逐字一致。测试补 `state: 'completed'` 用例。
- **I1**:`started_at` 与 `input_timestamp` 的解析都改走
  [[../lib/backendTs]](naive-UTC 规则收敛单点;`input_timestamp`
  原来裸 `Date.parse`,云端 UTC+8 下注入的用户气泡会排到 8 小时前)。

## 2026-08-21 — run_reconnect 落「续接」锚点(深圳复测 B1)

reconnect 的全量重放此前没有任何「这是同一个 run 在继续」的信号,刷新
中途的测试者把重放记成「重新生成」。`run_reconnect` 分支现在把帧里的
`started_at` 解析成 epoch ms 并 `markResumedRun` 进 [[../stores/chatStore.ts]]
(UI 据此渲染「已续接 · 已运行 N 分」chip)。解析坑:后端 `_format_dt`
输出 DB 的 **naive-UTC** ISO(无时区后缀),浏览器 `Date.parse` 会按
本地时区解——无后缀的串显式补 'Z',带偏移的原样信任。重放协议本身
零改动(铁律 #16:同帧同序同内容,只加呈现锚点)。
## 2026-08-24 — mid-run steer 发送 + steerable 捕捉

`run_started.steerable` 存进 `entry.steerable`;新增 `steer(agentId, content, clientMsgId)`:仅当连接在、`entry.steerable`、**`!entry.completed`**、`ws.OPEN` 才发 `{action:'steer',input_content,client_msg_id}` 并返 true;否则返 false——调用方须自行处理(ChatPanel 把乐观气泡标 `rejected`),**绝不回退新 run**(那会 `close()` 掉正在跑的这条 socket、中止用户想补话的那一轮)。`isSteerable(agentId)` 读 `entry.steerable && !entry.completed`——**跑完的 run 不可 steer**(已无法 drain),`complete`/circuit-open/error 等任一收尾路径置 `entry.completed=true` 即统一失效,无需各处重置 `steerable`。steer_queued/consumed/rejected 帧照常经 `processMessage` 进 [[chatStore.ts]]。

## 2026-08-14 — chat fast mode: 首包可带 fast_mode

`run()` options 增 `fastMode?: boolean`；开启时首包多一个字段
`fast_mode: true`，关闭/缺省时**省略字段**（wire 上与旧 payload 逐字节
相同）。仅此一处上 wire——backend `AgentRunRequest.fast_mode` 对应接收，
纯透传到 AgentRuntime；reconnect 首包不带（profile 不持久化）。

## 2026-08-06 — 两处 auth 桥接改调 `reportWsAuthFailure(frame)`

`run()` 与 `reconnect()` 的 onmessage 里，`dispatchAuthExpired()` →
`reportWsAuthFailure(message/raw)`，把帧本身传下去（里面带
`error_code`）。语义变化见 [[wsAuthError.ts]]：AuthError 帧不再直接登出，
先经 [[sessionGuard.ts]] 探针确认。

## 2026-07-31 — translateReconnectFrame 导出（观察面共用翻译器）

[[useRunObservation]] 消费同一个观察端点（任意 run by run_id），必须
说同一种帧方言 —— 导出唯一翻译器，聊天重连与观察两个面不会漂移。
纯可见性变更，逻辑未动。

## 2026-07-13 — Agent 实时层熔断器接入

run() 的 onmessage 识别后端 `agent_circuit_open` 帧 → `dispatchAgentCircuitOpen({agentId,reason})` + 标记 completed + processMessage + stopStreaming（清 spinner，避免卡在 streaming），mirror auth-expired 桥。仅 run() 路径（reconnect 不过 fresh-run 闸门）。

## 2026-06-10 — run_reconnect 注入幂等 + 终结性 reconnect 错误帧停止重连循环

两个修复，配合后端同日的「`_finalize` 广播终结 `complete` 帧」
（background_run.py.md）：

1. **user-bubble 注入改为幂等**。2026-05-13 的注入逻辑假设 reconnect
   只发生在"重开 tab"场景（session 是空的）；但 A3 auto-reconnect 在
   **同一个 tab** 内被动断线后也走这条路，session 里已经有这条 prompt
   —— 再注入一次就是用户看到的"我发的消息变两条"（直到下一次
   history 轮询用 event_id dedup 合并回去）。现在注入前检查 session：
   已存在 `role=user && event_id===runId` 的消息（run_started 在断线
   前到达过），或队尾是 event-id-less 且内容相同的 user 消息（断在
   run_started 之前）→ 跳过注入。
2. **终结性协议错误不再无限重连**。后端 reconnect 预检失败
   （`NotFound` / `Forbidden` / `DBError`）会发 error 帧后关 WS；原来
   这个关闭被当作被动断线 → 对同一个死 run_id 无限退避重连，
   isStreaming 卡 true（"Starting up…" 转圈到刷新）。现在精确匹配这三
   个 error_type：标记 entry.completed、把 error 帧交给 processMessage
   呈现、stopStreaming 收敛。其他 error 帧（agent 运行期错误 / replay
   error）行为不变（精确异常过滤，lessons #3）。

测试：wsManager.reconnect.test.ts 新增 4 例（fresh-tab 注入保持、
event_id 幂等、event-id-less 幂等、NotFound 终止循环）。

## 2026-06-02 — reconnect `run_ended` must clear isStreaming (stuck "Acting…" fix)

The reconnect onmessage handler ran the `translated === null` early-return
guard BEFORE its terminal-frame block, so `run_ended` (which
`translateReconnectFrame` absorbs to null) returned early and never set
`entry.completed` / fired onComplete / called `stopStreaming`. Symptom: a
run that finished *during the outage that triggered the reconnect* replays
its history, the server sends `run_ended`, the client drops it, and the
session stays `isStreaming = true` forever — the "Acting…" spinner spins
until a manual page refresh. Fix: hoist the `run_ended` / `complete` block
above the translate/early-return, and for `run_ended` call
`stopStreaming(agentId, agentName)` directly (`complete` still reaches
stopStreaming through processMessage as a live frame; stopStreaming is
idempotent via its `!isStreaming` guard, so the double path is safe). This
is a root-cause fix, NOT a timeout — no iron-rule #14/#15 ceiling; we
simply stop dropping the server's own terminal signal. This also covers the
"complete frame lost on the fresh-run WS" case: that path reconnects, and
the reconnect now retrieves `run_ended` and clears the spinner.

## 2026-05-29 — auto-reconnect on passive disconnect (A3)

An unexpected onclose (not a `complete` frame, not an explicit close())
no longer just stopStreaming. If run() captured a run_id (from the
`run_started` frame, stored on ConnectionEntry), wsManager schedules a
capped-exponential-backoff (1→2→4→8→16→30s, RECONNECT_BACKOFF_MS) retry
that re-attaches to the still-alive BackgroundRun via the existing Phase C
reconnect() path. NO attempt ceiling (iron rule #14); the cap only stops
a tight loop. The reconnect WS carries the same context so ITS own
onclose re-arms the backoff (flapping networks keep retrying);
run_reconnect resets the counter. No run_id → stopStreaming fallback
(unchanged). reconnectTimers / reconnectAttempts maps track pending
retries; close()/closeAll() cancel them. **Gotcha**: close() must NOT
reset reconnectAttempts (reconnect() calls close() internally — resetting
there would defeat backoff growth); the counter resets via run() / a
successful re-attach instead.

## 2026-05-27 — bridge WS AuthError frames to narranexus:auth-expired

Both `run()` and `reconnect()` onmessage handlers now call
`isAuthErrorMessage` (see [[wsAuthError]]) at the top of the parse
branch. On a match they `dispatchAuthExpired()` and bail — App.tsx
listens for the event, calls configStore.logout(), and shows the
"Your session expired" banner. Pre-fix the REST 401 path already
dispatched this event but the WS path swallowed AuthError frames into
the chat surface as red "Token expired" bubbles, leaving cloud users
stranded. TODO: 2026-05-26-auto-logout-on-expired-jwt.md.

## 2026-05-13 — Reconnect: 注入触发本次 run 的 user bubble

收到 `run_reconnect` 帧（reconnect WS 第一帧）时，如果带了
`input_content`（非空字符串），就**额外**调一次
`chatStore.addUserMessage(agentId, input_content, undefined, tsMs)`，
其中 `tsMs = Date.parse(raw.input_timestamp)`（后端给的是
`events.created_at` 的 ISO）。

为什么时间戳要这么精确：`ChatModule.after_turn`
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
   - `run_reconnect` / `run_ended` → 协议级 metadata，返回 null
     （不进 store；`reconnect_warning` 已随 2026-07-31 tail-follow
     删除——后端不再发，翻译器不再认）
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
