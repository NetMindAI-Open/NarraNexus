---
code_file: frontend/src/stores/chatStore.ts
last_verified: 2026-09-11
stub: false
---

## 2026-09-09 (revised 2026-09-11) — a cancelled turn with no reply gets its own marker (GitHub #87)

`stopStreaming`'s no-reply fallback used to write the same content
("(Agent decided no response needed)") whether the agent genuinely chose
silence OR the user cut the turn short. The `cancelled` flag was already
threaded through (the `case 'cancelled':` branch in `processMessage`
passes `{ cancelled: true }`), it just wasn't branched on. A third arm
now writes `INTERRUPTED_MARKER` — `(Interrupted by user)`, the exact
string the backend chat module persists for an interrupted turn — and the
plain fallback writes `NO_RESPONSE_MARKER`; both come from
[[turnMarkers]]. The store deliberately writes the persisted marker, not
a localized label: the live bubble and its reloaded history row then carry
identical content, and literal comparisons (buildTimeline's junk filter,
the backend `is_no_response` check) keep working. `MessageBubble`
localizes the markers at render time.
Test: `__tests__/chatStore.blankReply.test.ts` drives
`processMessage(AGENT, { type: 'cancelled' })` end to end (dropping the
`{ cancelled: true }` argument turns it red) plus the uncancelled
no-reply case.

## 2026-08-30 — thinking 合并加一条「换档即边界」

`agent_thinking` 的相位合并（回溯找 open bubble，只有 tool_call/reply 打断）
现在多一个条件：**open bubble 与新帧必须同档**（[[monologueTier]] 的
`isMonologueFrame`）。

为什么不能只按相位合并：一个 thinking 块只带**一个** `monologue` 标记，而
独白与 provider CoT 走同一条通道。跨档合并出来的块必然把两档文本标成一档
——要么把独白дим掉，要么把草稿纸提亮。后者是不能犯的那一侧
（理由全文在 [[monologueTier]]）。

### 「未知档位」那套机制已经拆掉（2026-08-30 第二版）

曾经存在一个 `replayed: true` / `undefined` 档位的三态，用来兜住回放帧没有
档位这件事——它自己在 review 里错过两次（当成 false 会劈句子，向上采纳会把
CoT 提亮）。**根因在源头**：`run_recorder` / `broadcaster` 不持久化档位。

那条根因这一单补掉了（见 [[run_recorder]] / [[broadcaster]] / [[wsManager]]），
三条回放路现在都带真实档位，于是三态退回布尔，`replayed` 字段删除。
**边界的正确性从「前端防御性合并」换成了「上游不跨档合并」**——
[[_thinking_batcher]] 与 [[run_recorder]] 各自换档即 flush，所以到这里的帧
不可能骑在一次切换上。

**独白必须留在 thinking 车道**，这是一条红线，不是风格选择：本文件
`agent_response` 分支有 `alreadyReplied` 全量丢弃（本轮出现任何 `reply` 之后
的 native 文本一律 `return {}`，为治模型「回完又复述一遍」）。把独白改走
native_output 那条"直觉捷径"，会**静默吃掉 reply 之后的所有独白**——那是
减少用户今天已经看得到的内容，直接违反铁律 #16。夹具钉在
`__tests__/chatStore.monologueTier.test.ts`「红线 1」那条。

## 2026-08-24 (review #349 M4) — `runId` 接成守卫;`currentRunId` 进 flat fields

`resumedRun.runId` 原是死字段。现在 ChatPanel 的 chip 渲染条件要求
`resumedRun.runId === currentRunId`——锚点只给**它自己的** run 打标;
将来若出现第二条开流路径,陈旧锚点是隐形而不是错标。`currentRunId`
为此加入 flat fields。

## 2026-08-21 — session 增 `resumedRun`(续接锚点,深圳复测 B1)

`{ runId, startedAtMs } | null`:当前流式轮是「重连到既有 run」时由
[[../services/wsManager.ts]] 的 run_reconnect 分支写入,携带 run 的
**真实开始时间**。`startStreaming` 清空(重连流程是 onopen→startStreaming
→run_reconnect,清空发生在写入前,顺序安全);渲染只在 isStreaming 时,
settle 无需显式清。透传进 flat fields(`deriveFlatFields`)。
## 2026-08-24 — steer 三态 + currentSteerable

AgentChatState 加 `currentSteerable`(run_started 设、startStreaming 重置 false、进 deriveFlatFields 让 ChatPanel reactive)。新增 `addSteerMessage(agentId,content,clientMsgId)`=乐观 `queued` 气泡;`markSteerRejected(agentId,clientMsgId,reason)`=按 `steerClientMsgId` 就地翻 `rejected`+`rejectReason`,供**发送根本没离开客户端**(`steer()` 返 false)时用——后端不会来 ack,故本地标红,不让气泡永远挂 `queued`(与后端 `steer_rejected` 帧殊途同归,无匹配 id 时无害 no-op)。processMessage:`steer_consumed{ids}`→把 `steerClientMsgId∈ids` 的气泡翻 `merged`;`steer_rejected{client_msg_id,reason}`→翻 `rejected`+`rejectReason`;`steer_queued`/`run_reconnect` no-op(乐观气泡已 queued;重连不可 steer)。无匹配 id 的 consumed 是无害 no-op。**run 收尾兜底**:`stopStreaming` 里把仍 `queued` 的 steer 气泡统一翻 `rejected`+`rejectReason='run_ended'`——run 已结束、不会再来 `steer_consumed/steer_rejected`(晚到、跑完没 drain 的 steer,或 error/circuit-open 无 ack 收尾),否则气泡永挂 `queued`,违反三态不变量;已 resolve 的(merged/rejected)不动。`not_sent`/`run_ended` 两个 client 侧 reason 在 `chat.steer.reason.*` 有 i18n key(否则前端直显裸 token)。**四处「按 steerClientMsgId 认气泡+翻状态」的 map 收敛成一个 helper `patchSteerBubbles(messages, match, patch)`**(markSteerRejected/steer_consumed/steer_rejected/stopStreaming 兜底共用):helper 内建 `m.steerClientMsgId &&` 前置守卫——一条无 `steerClientMsgId` 的普通消息永不是 steer 气泡、永远不动,这道守卫挡住「后端某天发来 `steer_rejected` 而 `client_msg_id` 缺失(undefined)→命中所有 undefined-id 的历史气泡、整屏误标 rejected」;`match` 同时收(已定义的)client id 和整条 m,故收尾兜底能按 `steerStatus==='queued'` 匹配。一处定义,加第四态或新调用方(#354/team)时只改一处、守卫不漂。`run_reconnect` 分支:通常在 wsManager 上游被 translateReconnectFrame 吸收(返回 null),store 侧保留一个显式 no-op(reconnect 无 live channel、无 steer 状态可改),不靠 switch 静默兜底;`RunReconnectMessage` 也删掉了永不被读的 `steerable?` 字段(见 [[messages.ts]])。

## 2026-08-17 — 气泡提取改用 `isOwnerReplyTool`

三处按工具名挑 owner 回复内容的地方（`stopStreaming` 的拼接、内联时间线的 reply 事件、
`complete` 的 `hasReply`）改用 [[ownerTools]] 的共享判定。

后端把一个 owner 工具拆成了 `reply_owner` / `notify_owner`，每轮桌上只有一个。前端只匹配
其中一个名字时，另一条表面上的回复内容是真的、字也在，**气泡就是不渲染**——一个不报错的
静默失败。

## 2026-08-14 — ToastItem 成为可辨识联合

`ToastItem` 从 `{agentId, agentName, timestamp}` 改成
`{kind:'agent',…} | {kind:'team',…}`，`dismissToast` 从按 agentId 改成按
`toastKey(item)`（`agent:<id>` / `team:<id>`）。

原因见 [[AgentCompletionToast.tsx]]：团队房间的 toast 既没有可切换的 agent，也没有
可以当键的 agent id。**键按 kind 限定**，是因为无法从代码确认 team id 和 agent id
是否可能相等——不能确认的事就让它不可能发生，而不是依赖它。

这个改动的价值在改的过程中就兑现了：tsc 立刻指出 [[useAutoRefresh.ts]] 里还有一处
没带 `kind` 的入队。联合类型让"少写一个字段"从运行时的怪现象变成编译错误。

## 2026-08-10 — rendered reply as value outcome

On `complete`, the store verifies a nonblank user-facing reply, commits the
assistant message, then records `reply_rendered` with run/agent IDs. Error-only
and no-response turns never count as value DAU.

## 2026-08-04 — 回复提取过滤空白部分；删除死方法 getUserVisibleResponse

stopStreaming 的回复提取 `.filter(Boolean)` 收紧为
`.filter((s) => !isBlankText(s))`（谓词统一在 lib/isBlankText.ts）：空
白 content 的 send_message 调用不算回复，落到既有占位文案分支——与后
端 persist_turn 的 strip 守卫同口径，否则出现「当场有空气泡、刷
新后消失」的 session/落库漂移。同批删除 getUserVisibleResponse（接口
+实现）：frontend/src 内零生产调用方，铁律 #2/#8 不留死码。

## 2026-07-30 — stopStreaming 存 segments

助手消息落定时除 `timeline` 外再挂 `segments = segmentTurn(currentEvents)`
——MessageBubble 据此把一轮渲染成 agent 实际说话的 m 次。`content` 仍是
join('\n\n') 全文：通知、复制、搜索的纯文本载体，也是老消息的兜底。

## 2026-07-30 — pending tool_call 按 tool_call_id 原地替换

工具名先到（参数还在流式生成）时后端发一条 `pending=true` 的
tool_call；参数齐了再发同 `tool_call_id` 的完整事件。store 在
`currentEvents` **和** `currentToolCalls` 里都按 `tool_call_id` 找到
pending 条目原地覆盖（保留原 event id，React 不重挂行）。

替换不是装饰性的：`currentToolCalls` 是 stopStreaming 提取用户可见
回复的数据源，pending 条目若和完整版并存，会往轮次内容里注入一个
空回复段——表现为一条凭空的空白消息。

## 2026-07-29 — reply-delta 增长气泡 + plan 原地替换

两条 NexusPower 专属流的落点：

- `agent_reply_delta` 按 `call_id` 聚成**一个**增长中的气泡（同一次表达工具
  调用的片段属于同一句话）；该次调用完成的 `tool_call` 事件不再另起一条，而是
  折叠进这个已经开着的气泡——否则用户会看到同一句话出现两遍。
- `agent_plan` 是整份快照，所以**原地替换**而非追加。

其他框架永不发这两种消息，既有分支逐字未变。

## 2026-07-23 — stopStreaming fires the desktop completion notification (#44)

`stopStreaming(agentId, agentName?, opts?)` gained `opts.cancelled`. Before
the state update it calls [[desktopNotify.ts]]'s
`notifyAgentReplyCompleted` when (a) the session WAS streaming (the
duplicate-call guard also guards the notification — flag read before
`set()`), (b) not `opts.cancelled` (the `cancelled` WS case passes it), and
(c) `!document.hasFocus()` — a focused window already shows the in-app
toast/timeline. Web mode stays silent inside the lib (Tauri check there).
Tests: `__tests__/chatStore.notify.test.ts`.

## 2026-07-22 — `currentActionReason` 也认 `infra_transient`

error case 里锁存 `action_reason` 的条件从只认 `config_actionable` 扩到**也认**
`infra_transient`（缺省回退到 `'infra_transient'`）。这样 executor-infra 失败
（`executor_oom` / `executor_unreachable`）的 reason 也会 stamp 到 assistant 消息，
[[MessageBubble.tsx]] 据此渲染"执行环境异常"徽章与对应引导文案。

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

## 2026-08-10 — workspaceRefreshTick / requestWorkspaceRefresh

The same idea one layer over, for [[TeamChatPanel.tsx]]'s workspace panel.

A SEPARATE counter rather than a reuse of `historyRefreshTick`, because the two
wipes are independently selectable. Clearing only a team's files empties the
workspace (and its artifacts — they live in that folder) while leaving the
transcript byte-identical, and the workspace loader keys on message count, so
nothing about the chat changes to tell it the rows are gone. Firing the history
tick for a file-only wipe would ALSO make every mounted ChatPanel refetch
history it already knows is current — one signal covering two unrelated wipes
is how a refresh turns into a stampede.

Producer: [[AgentList.tsx]]'s clear-team-data flow, on the `files` scope.

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

**`reply_owner` as display content.** The agent's final visible reply is extracted by filtering tool calls whose name ends with that string. The store is otherwise agnostic to tool semantics — all tool calls are stored but only this specific one populates the chat bubble.

**Rejected: separate stores per agent.** Would require dynamic store creation and explicit cross-store wiring for the toast/badge system. A single store with a session map is easier to subscribe to and requires no lifecycle management for agent removal.

## Gotchas

**Stale `entry` reference in `wsManager`.** The `onclose` callback captures `entry` from the closure and checks `this.connections.get(agentId) === entry` before deciding whether to call `stopStreaming`. If `close()` or a second `run()` already replaced the map entry, reading from the map would target the wrong session. This race was the root cause of phantom "unexpected disconnect" warnings in early multi-agent builds.

**`stopStreaming` deduplication guard.** Both the `complete` message handler and `ws.onclose` may call `stopStreaming`. The store guards with `if (!session.isStreaming) return {}` so only the first caller commits the history round and assistant message.

**Background agent toast lifecycle.** When `stopStreaming(agentId)` fires for a non-active agent, it pushes to both `completedAgentIds` and `toastQueue`. Consumers must call `dismissToast(agentId)` after displaying the toast and `clearCompletedNotification(agentId)` when the user switches to that agent. Omitting either leaves stale badge indicators permanently.

**`processMessage` silently drops unknown types.** If a future backend version emits an unrecognized message type, the store does nothing. If the backend stops sending a `complete` message (protocol change), the session stays `isStreaming: true` until the WebSocket closes and `onclose` triggers `stopStreaming` as a fallback.

## 2026-08-18 — ToastItem 增 artifact-repointed 变体 + 通用 pushToast

heal 重指的诚实提示(标题/旧新路径尾/hash 是否验证)。pushToast 按 toastKey
去重(同键重推=重置自动消失计时)。
