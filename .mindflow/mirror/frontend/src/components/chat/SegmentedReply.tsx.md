---
code_file: frontend/src/components/chat/SegmentedReply.tsx
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 剥掉流式回复里的 `<agent_draft>`

`segment.reply.content` 两个分支（流式 pre-wrap + 落定 Markdown）都过
`stripAgentDraft`。**流式那条是关键**：草稿块的开标签比闭标签早到很多帧，这里
正是原始 JSON 会在读者眼前滚过去的地方，`stripAgentDraft` 连未闭合形态一起杀。

## 2026-08-31（二）— `defaultOpen` 删除

透传给 [[TurnTimeline]] 的 `defaultOpen` 随全链路一起删掉：推理档永远折叠。
理由见 TurnTimeline 的同日条目。同名测试里那两条只为这个 prop 存在的用例，
改写为一条「折叠 + 一次点击可达」。

## 2026-08-31 — 文件头那条 live/settled 分工作废

头注释原写「live（showProcess=false）：过程在 ProcessPanel」。这在上一个 commit
就已经不成立（[[ChatPanel]] 直播态传 `showProcess`），`ProcessPanel` 本身今天
也删了。改写为实情：`showProcess` 是**prop 不是模式**，聊天两个方向都传；
保留 off 分支是给「只要答案」的调用方（预览 / 摘要）用的契约，聊天自己不用。

同名测试的标题与理由一并订正——它断言的是 prop 契约，不是某个面板的存在。

## 2026-08-30（二）— 抽屉退役：过程就是文稿本身

settled 态从「两种形态由偏好选」收敛成**一种**：`segment.process` 直接以
文档形式渲染（叙述常显 → 工具行 → 折叠的推理），reply 作为正文铺在下面。
`Reasoning & tools (N)` 那个整体抽屉、以及 `promoted` / `expanded` /
`useNarrationTier` 在本组件里的用法一并删除。

**为什么把上一轮刚立的东西拆掉**：上一轮让抽屉由偏好保留、并对"没有叙述的
轮次"保留抽屉，理由是不给 claude/codex 用户换形态。Owner 视觉验收否了整个
方向——留着抽屉就是**同一个产品里两种文稿形态**，而抽屉本身正是把叙述藏起来
的那个东西。现在偏好只管色调（它的名字一直就只承诺这个），不管版面。

reply 挂 `markdown-reply`；流式分支的字号/行高与落定后的 markdown 对齐
（`0.95rem` / `1.75`），**避免落定瞬间整段回流**。

## 2026-08-30 — 过程从抽屉里提到消息流内（布局提级）

settled 态多了一种形态，由 [[useNarrationTier]] 偏好选择：

- **提级（缺省）**：`segment.process` 直接渲染，**没有外层抽屉**。叙述句按
  正文量级可读、工具卡行内、推理块各自折叠（分档由 [[TurnTimeline]] 逐块决定）。
  提级的全部意义就是**不点任何东西就能读完这一轮**。
- **偏好关**：原样回到那一个 `Reasoning & tools (N)` 折叠入口。

「今天可见的内容一条不许丢」：两种形态下事件集合完全相同，差别只有默认展开
到哪一层——推理正文在提级形态下也只是收在各自的「已思考 ▸」里，一点即得。

## 2026-07-30 (r2) — 流式段 plain text + defaultOpen

- **流式段不走 Markdown**：每 delta 全量重解析拖死主线程（与 ThinkingBlock
  2026-05-12 同一教训），表现为「蹦几个字→卡住→整段一次性出来」。流式期间
  渲染 plain pre-wrap + 光标，落定切 Markdown（旧 ReplyBlock 同款取舍）。
- **defaultOpen**：历史气泡点一次「View reasoning」已经是一次点击，fetch
  完落在又一层折叠入口上等于要点两次（Owner 反馈）。fetch 路径传
  defaultOpen 全部展开，仍可手动收起；新鲜消息（stopStreaming 带 segments）
  保持折叠入口一次点开。

# SegmentedReply.tsx — 把 Segment[] 渲染成 agent 实际说话的那几次

## 为什么存在

一轮可能说多次话（n 次工具调用里有 m 次是回复用户）。后端仍是一轮
一条记录；这个组件把那一条渲染成 m 个「说话」，每个可带上导致它的
过程。段的切法由 `lib/segmentTurn` 决定——这里只负责画。

## 设计决策

- **同一个组件服务直播与历史**，差别只有 `showProcess` 一个开关：
  直播中 false（过程在 ProcessPanel，两处都画就重复）；结束后 true
  （面板已卸载，过程折叠回各段自己的气泡上，`<TurnTimeline>` 渲染）。
- **展开状态记在本组件**（按段索引）：父组件在流式期间每个 delta 都
  重渲染，状态放这儿才不会被重置。
- **helper_llm 恢复徽标**（2026-07-30 自 TurnTimeline 的 ReplyBlock
  迁入）：`reply.via` 为 `helper_llm_no_reply`（或 legacy
  `helper_llm_fallback`，2026-05-25 改名前的持久化值，不 backfill）
  显示 info 徽标；`helper_llm_after_error` 显示 warning 徽标。i18n
  keys 沿用 `chat.timeline.helperFallback*` / `recoveredAfterError*`。
- **流式光标**只给最后一段（`isStreaming && isLast`）。

## Gotcha

- `reply=null` 的段（零回复轮次）不渲染气泡但保留过程入口——过程
  不因没说话而丢失。
