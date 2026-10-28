---
code_file: frontend/src/components/chat/TurnTimeline.tsx
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 ui.toolRenderers（第 18 个注册表）

tool_output 行先查 `TOOL_RENDERERS`：命中则在 `PluginBoundary`（崩溃→通用行）+ `Suspense`（懒加载
期间→通用行）里渲染插件组件，否则照旧 `ToolOutputBlock`。两层回落都是同一个通用行，所以加载中、
崩溃、未注册三种状态用户看到的都是正常输出行，不会出现空白。

## 2026-09-07 — a throwing plugin timeline-event component is isolated (I-6)

The plugin-provided timeline-event component is wrapped in `<PluginBoundary key={event.id}
owner={plugin.owner}>` so one bad event's render crash removes only that event's row, not the
whole timeline (nor the conversation above it). See `platform/PluginBoundary.tsx`'s mirror doc.

## 2026-08-31（二）— `defaultOpen` 整条删除：推理永远折叠

`ThinkingBlock` 的推理档 `useState(false)` 写死，`defaultOpen` prop 从
`TurnTimeline` / [[SegmentedReply]] / [[MessageBubble]] 三处一并移除
（铁律 #2：没有调用方的参数不留）。

Owner 的判据：**点开一轮的过程是要看结论，不是要读草稿纸。** 叙述句和工具行
是这一轮的可读骨架，provider CoT 比它们加起来还长——自动展开等于把读者真正
来看的东西埋掉。折叠 ≠ 丢弃，开关就在旁边（铁律 #16）。

这条推翻了前一版加 `defaultOpen` 时写的理由（「用户已经花过一次点击」）。
那个理由只看到点击成本，没看到**展开之后谁被埋掉**。

## 2026-08-31 — plan 的去处改名

文件头与 `processEvents` 处的注释原说 plan「渲染在 ProcessPanel 的钉底区」。
该组件已删除，plan 现在在 [[process/PlanStrip]]。分工本身没变：过程归时间线，
答案归 `SegmentedReply`，plan 归贴底细条。

## 2026-08-30（二）— 成为唯一的过程渲染器

直播、落定、团队观察三个面现在都渲染本组件（此前直播走
`ProcessEventRows`、观察面也是）。`ToolCallBlock` 补上
`data-testid={`tool-row-${event.id}`}` 与 `data-pending`，承接退役组件留下的
断言抓手——`documentFlowConsistency.test.tsx` 靠它断言"叙述 → 工具 → 推理
→ 正文"这个顺序。

分档规则未变（见上一条）：叙述常显 ink70 + `Milestone` 记号，推理折叠成
「已思考 ▸」。变的是它现在坐在**没有气泡**的文档里，所以这个对比第一次
真正可见。

## 2026-08-30 — ThinkingBlock 三档中的第二档：进度（独白）

原来是二元的：**答案**（气泡，正文色）vs **过程**（dim）。现在过程语域内部
再分两档，**用的是既有 ink 阶梯的三个档位，没有新 token、没有新色**：

| 档 | 内容 | 色 |
|---|---|---|
| 答案 | reply / native_output | `--nm-ink`（不变） |
| **进度** | `monologue=true` 的 thinking | **`--nm-ink70`**（新用法） |
| 推理 | provider CoT | `--nm-ink50`（不变） |

正文走 `.markdown-progress`（`index.css`，= `--text-secondary`），对位既有的
`.markdown-dim`。图标 `Brain` → `Milestone`（lucide，12px 档，继承
`currentColor`，符合 design_system §5），标签 `chat.timeline.narration`。

**刻意没做的事**：不做气泡、不加背景填充、不加圆角、不配身份色。任何一个都
会把它推进**消息语域**，而宪法承诺的正是「不是一条对你说的话」——A′ 之所以
不用改宪法，全靠这条线守住——承诺的原话是 "The user never receives it **as a
message**"，所以只要不进消息语域，承诺就仍然成立。表面填充另外还会平白消耗
design_system §2.5 的层级预算。

`narration` 由调用方算好（档位 AND [[uiStore]] 的 `interimNarration` 偏好），
组件本身只是纯色调切换。

## 2026-08-19 — 空名不渲染([输出] 与 [TOOL] 两行同规则)

工具名缺失时只显示标签本身——名字位置的占位词/空洞读起来像 bug。
真名恢复在上游(见 [[MessageBubble]]/[[../../lib/segmentTurn]])。

## 2026-07-30 — 降级为只渲染过程（答案层迁出）

聊天区过程面板改造确立新分工：**过程归时间线，答案归气泡**。

- `ReplyBlock` / `NativeOutputBlock` → 删除。答案由 `SegmentedReply`
  渲染（`lib/segmentTurn` 切段）；helper_llm 恢复徽标（含 legacy tag
  兼容）随答案层一起迁到 `SegmentedReply`。
- `PlanBlock` → 删除。plan 是「现在到哪了」，由 `ProcessPanel` 底部
  固定区渲染，不参与滚动。
- 组件体内先过滤出 process 事件再渲染——保留 reply 会让同一句话在
  气泡和折叠区各出现一次。

本组件现在的消费者：`ProcessPanel`（运行中）不用它——面板自己有更紧凑
的 terminal 行渲染；`SegmentedReply` 的折叠详情区和 `MessageBubble`
的历史详情用它渲染过程块。下方 2026-05-14 的 ANSWER/PROCESS 两层样式
记录中 ANSWER 层的部分已随迁出成为历史。

## 2026-07-29 — `PlanBlock` + 流式回复渲染

新增 `PlanBlock` 渲染 NexusPower 的实时计划（沿用时间线既有的设计语言，不另起
一套视觉），并把 reply-delta 气泡接进时间线。

判断依据是**消息形状**不是框架名：有 plan 消息才渲染 plan。这样别的框架的
时间线一个像素都不变，将来第四个框架发同样形状也自动能用。

## 2026-05-25 — Two-mode fallback badge on ReplyBlock

`ReplyBlock` now takes `fallbackKind: 'none' | 'no_reply' | 'after_error'`
(was `isFallback: boolean`). `fallbackKindFromReplyVia` maps the
backend's `reply_via` tag to that enum:

- `helper_llm_no_reply` → info badge "↻ helper_llm fallback" (silicon-
  tinted, soft). Nothing broke — agent forgot to call the reply tool,
  helper_llm wrote what it should have.
- `helper_llm_after_error` → warning badge "⚠ recovered after error"
  (warning-tinted). A step in this turn actually failed; reply was
  written from completed work + error knowledge.
- legacy `helper_llm_fallback` (pre-2026-05-25 persisted rows) → mapped
  to `no_reply` so historical replies still surface as recovered. The
  rename happened in step_3 and chat_module already accepts any
  `helper_llm_*` tag, but persisted DB rows from before the rename
  carry the old string and we don't backfill.

Tooltip on each badge carries the user-facing explanation; raw
`error_type` stays in the dev-tools log so the UI never leaks technical
strings.

## 2026-05-14 (r2) — two-tier styling: ANSWER vs PROCESS

The first 2026-05-14 pass ("make Thinking dimmer, Reply larger") was a
**no-op for settled content** and fixed the wrong pair. Two root causes:

1. **`.markdown-content` override.** Settled `thinking` / `reply` bodies
   render through `<Markdown>`, whose `.markdown-content` rule sets an
   explicit `color` and `font-size`. Those win over any ancestor utility
   class — so `text-[var(--text-tertiary)]` on the ThinkingBlock
   container and `text-[15px]` on the ReplyBlock body reached only the
   label + the brief streaming plain-text path, never the settled body.
2. **Wrong pair.** The hard-to-tell-apart pair was Thinking ↔
   **NativeOutput** (both muted, both dashed-tertiary border), not
   Thinking ↔ Reply. `native_output` had never been touched.

r2 reworks all three "speech-ish" blocks into **two semantic tiers**,
keyed by border style:

- **ANSWER tier — SOLID left rule** (content the user should read):
  - `reply` — peak: thick solid *accent* rule + faint accent fill +
    accent label + body one notch larger.
  - `native_output` — same tier, one notch below: solid *secondary*
    rule, full-strength body, neutral tone, no fill. (Was: dashed
    tertiary + `opacity-80` — i.e. visually identical to thinking.)
- **PROCESS tier — DASHED left rule** (skimmable):
  - `thinking` — dashed tertiary rule, dimmest tone throughout.
  - `tool_call` / `tool_output` — unchanged mono affordances.

**The override is now defeated properly:** two `markdown-*` variant
classes in `index.css` (`.markdown-content.markdown-dim`,
`.markdown-content.markdown-reply`) — two-class selectors, specificity
0,2,0, beat `.markdown-content`'s 0,1,0. `ThinkingBlock` passes
`className="markdown-dim"` and `ReplyBlock` passes
`className="markdown-reply"` to `<Markdown>`. `native_output` never uses
Markdown so its container styling applies directly.

Order / position / event logic unchanged — styling only.

# TurnTimeline.tsx — Inline event timeline for a streaming agent turn

## 为什么存在

Before this component the chat panel had two parallel renderings of
the current turn:

- a streaming MessageBubble (thinking and tool calls collapsed inside,
  shown only after the first send_message)
- a "Live activity preview" italic stream + spinner list of tool steps

That double-rendering grouped events by *kind* (all thinking together,
all tools together) instead of by *time*. With multiple tool calls
thinking was pushed out of view and the user couldn't see the actual
rhythm of "think → tool → think → tool → reply → think". Xiong called
this out in the 5/11 review.

`TurnTimeline` replaces both of the above. It renders one block per
event in chronological order, so the user reads exactly what the
agent was doing at each moment.

## 上下游关系

- **被谁用**: `ChatPanel.tsx` — for both the currently streaming turn
  (`currentEvents`) and the just-completed turn that hasn't been
  collapsed yet (`lastTurnEvents`).
- **依赖谁**: `chatStore.processMessage` (which builds the events
  array out of the raw websocket frames); `@/types/messages.TurnEvent`
  (the discriminated union); `Markdown` (reply rendering).

## 设计决策

**One block per event, no grouping by kind**. Thinking → tool → think →
reply → think appears in exactly that visual order. This is the whole
point — it answers the "what is the agent doing now" question.

**Visual hierarchy expresses speech vs. thought**:
- Reply blocks get a coloured left border, bigger type and full markdown
  rendering — they are the user-facing speech.
- Native_output gets a dashed left border and muted styling — it is the
  agent's text that wasn't routed through the reply tool, so it sits
  one notch below a real reply visually.
- Thinking gets italics, smaller type, muted colour and a subtle left
  rule — it is internal monologue and should not compete with replies.
- Tool calls are a single line in a pill — the *what* (tool name +
  one-line arg preview) is enough for chat context; full args / output
  belong on the right-side Execution panel.

**Per-block expand/collapse with local state**. Each block keeps its
own `useState` for expanded. Because the parent ChatPanel keeps the
same TurnTimeline mounted across renders during a turn (events only
append), `key={event.id}` preserves state correctly. Reload of the
page unmounts the component → state resets → first-open default
(collapsed for long thinking, collapsed for tool args, expanded for
reply) — matches the "本次展开保持，下次打开折叠" rule from the 5/11
review.

**Friendly tool names**: `mcp__chat_module__get_chat_history` is
stripped of the MCP prefix and shown as `get_chat_history`. The full
canonical name is still in the underlying event for debug logs.

**helper_llm fallback indicator**: when a reply event carries
`reply_via === "helper_llm_fallback"` (i.e. the agent didn't call
send_message and we synthesised a reply via helper_llm in
step_3_agent_loop), a small "↻ helper_llm fallback" tag appears above
the reply. This is an operator-facing breadcrumb, not a user warning;
the reply content itself is normal.

## Gotcha / 边界情况

- Empty events array renders nothing (returns `null`). The "Starting
  up..." indicator is the parent's responsibility — see ChatPanel.
- Long thinking blocks are clipped to 280 chars with a "show full"
  toggle; the goal is to keep reply blocks visible without scrolling
  past several paragraphs of reasoning. If a reasoning model emits
  multi-thousand-character thinking this might still feel heavy —
  see follow-up TODO #3 in mindflow tasks.
- `Markdown` is imported for reply rendering only; thinking and
  native_output deliberately use plain `whitespace-pre-wrap` because
  they are not user-facing speech and shouldn't be elevated by
  formatting.

## 新人易踩的坑

- Adding a new event type: extend `TurnEvent` in
  `frontend/src/types/messages.ts` AND add the case in `TurnTimeline`
  AND add a push in `chatStore.processMessage`. Forgetting any one of
  the three silently drops events.
- The "skip the most-recent session assistant message in the unified
  timeline" logic lives in `ChatPanel.tsx::timeline useMemo`, not here.
  Without it, the just-completed reply would render twice (once as a
  history bubble and once as a reply block in this component).

## 2026-09-04 · UI slot points (batch 3d.2)

Event types registered in `timelineEvents` pass the process filter and render through the plugin's component in the same rail (`default` branch); unknown types still render nothing. Subscribed so a late registration is not filtered out.
