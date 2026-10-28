---
code_file: frontend/src/lib/segmentTurn.ts
last_verified: 2026-08-30
stub: false
---

## 2026-08-30 — `timelineToEvents` 带上 monologue 判别位

档位如果只活在直播路径，同一轮刷新后就会掉回普通 thinking——**恰好打破本文件
立起来的那条不变量**（「直播看到的和刷新后看到的必然一致」）。

判别位由后端 [[chat_history_timeline]] 收敛好（那边按档切块，一个 timeline
条目只含一个档次），这里**只是透传**：`monologue: !!entry.monologue`。前端不在
回放路径上重跑相等判定——后端已经判过，重判一次就是两份实现，正是本文件反对
的事。

存量行没有该字段 → `false` → 普通 thinking，与改动前逐像素相同。

**三条回放路现在都成立**：已结束轮的 `/event-log` 重载（本文件）、运行中刷新的
WS 重连、以及团队面板的观察 socket（见 [[wsManager]] / [[useRunObservation]]）。

## 2026-08-19（二)— 字面 'unknown' 视同缺失

历史上占位符有两个源头:API 读侧(chat_history 曾默认 "unknown")与
写侧(response_processor 曾把它持久化进 event_log)。两侧均已治根;
`realName()` 归一化('unknown' 与空同待遇,继承前一调用名)保留为
**存量数据**兜底——数据库里已写入的 "unknown" 行无法回改。

## 2026-08-19 — tool_output 承接调用名 + 转换唯一化

- `lastToolName` 顺承:存储态 tool_output 无名时继承最近一次 tool_call,
  空名留空由渲染层隐藏。测试:segmentTurn.test.ts。
- `timelineToEvents` 新选项 `convertOwnerReplyTool`(默认 true):
  [[../components/chat/MessageBubble]] 的折叠态披露用 `false`——reply 工具
  调用保持为普通 process 行,这是设计差异,现在以显式参数表达,转换实现
  全仓只此一份(MessageBubble 的手写副本删除)。

## 2026-08-17 — 回放路径的 reply 判定同样走 `isOwnerReplyTool`

和直播路径（[[chatStore]]）用同一个判定。两条路径对「哪次工具调用是 owner 回复」必须给
同一个答案，否则重载一轮之后气泡会消失。判定见 [[ownerTools]]。


# segmentTurn.ts — 一轮事件按「用户可见片段」切段的纯函数

## 为什么存在

聊天区过程面板改造（2026-07-30）确立分工：过程在 composer 上方的
ProcessPanel 滚动，答案留在气泡。一轮天然是
`[思考, 工具, 工具, 回复₁, 思考, 工具, 回复₂, …]`——每个回复之前的
过程属于它。`segmentTurn` 在每个用户可见片段处切开，产出 `Segment[]`。

**一个函数，三条路径**：运行中的 `currentEvents`、刚结束的
message、刷新后 `/event-log` 的 timeline（经 `timelineToEvents` 归一）。
直播看到的和刷新后看到的必然一致——不是两份实现碰巧对上，而是同一份
实现。测试里专门有一条用例钉住这个等价性。

## 切段规则（每条都是判断而非显然默认）

- **末尾残留过程归最后一段**：说完最后一句还干了活，正是该被看见的。
- **零回复轮次**产出一个 `reply=null` 的段——过程不丢。
- **连续 native_output 合并**：是同一句话拆成的多个 delta；一旦中间
  夹了工具调用，就是又说了一次，开新段。合并条件含 `via===undefined`
  （走了表达工具的 reply 不和原生文本合并）。
- **plan 不属于任何段**：它是「现在到哪了」，由 ProcessPanel 底部
  固定区单独渲染。

## timelineToEvents

原先内联在 MessageBubble 且**故意跳过 reply**（回复文本已在
message.content 渲染过）。切段需要 reply 作切点，所以这里保留——
重复渲染改由「气泡只渲染 segment.reply」来避免。

**关键事实（2026-07-30 实测）**：后端 `/event-log` 的 timeline **从不产
type='reply'**——回复以 `reply_owner` 的 tool_call
条目存储（`tool_input.content` 即回复文本，`reply_via` 在条目上）。
所以这里把 send_message 的 tool_call 转成 reply 事件（与直播路径
chatStore 的同一转换对齐）；回执 tool_output 照旧作为过程事件。不做
这个转换，NexusPower 的历史轮次切不出任何 reply，刷新后整体回落单段。

## 上下游

- 型别 `Segment` / `SegmentReply` / `ProcessEvent` 定义在
  `types/messages.ts`（types 不反向依赖 lib）。
- 消费者：ChatPanel（直播）、chatStore.stopStreaming（存段）、
  MessageBubble/SegmentedReply（历史）。
