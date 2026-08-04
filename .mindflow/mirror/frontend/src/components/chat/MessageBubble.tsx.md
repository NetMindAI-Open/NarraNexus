---
code_file: frontend/src/components/chat/MessageBubble.tsx
last_verified: 2026-08-01
stub: false
---

## 2026-08-01 — 余额用完的第二个入口（Owner 决定）

`actionReason === 'insufficient_balance'` 且**存在 NetMind 会话**（`configStore.netmindToken`，
与 SettingsPage 隐藏账户 tab 用的是同一个信号）时，多渲染两个按钮：
**升级 Nexus Pro** → `/pay`、**查看套餐与充值** → `/app/settings?tab=account`。

这**推翻**了此前「BYOK 余额不足刻意不给按钮，塞升级引导是劫持」的立场 —— Owner 2026-08-01
决定：花完余额的人也该有一次点击直达，而不是只有一句话让他自己去翻设置。

两个约束因此写进了实现：

1. **套餐按钮两边共用，「用我自己的 provider」不能跟过来**。后者的前提是这个人**没有**自己的
   卡，而这里的人恰恰正在用自己的卡。套餐按钮走 `/pay`，已订阅的人会被它回落到账户页 ——
   标题对他们不贴切的代价是一次跳转，不是死路（所以可以共用）。文案本身也绝不能复用免费额度
   那句：跟一个在付费的人说「你的免费额度用完了」是假话。
2. **第二个按钮只能说我们的目的地，不能说「给你的账号充值」**。`insufficient_balance` 是
   **故意不区分服务商**的（DeepSeek 402 / OpenAI quota / Anthropic 余额 / 用户自己的
   NetMind 都落在这一个 reason），而 reason 里没有任何信息指明是谁干了 ——
   `get_provider_source()` 粗粒度，[[resolver]] 对所有非平台卡一律返回 `"user"`。
   「查看套餐与充值」在以上每种情况下都成立，「去充值你的账号」则不然。

副作用是：NetMind 已登录、但用的是自己 DeepSeek key 的用户，key 干了也会看到这个按钮 ——
那是一次 upsell。属 Owner 已知并接受的范围。要精确到「只对我们能充值的卡显示」，需要
per-slot 的卡片来源，与 free-tier 误判那条是同一个前置依赖。

## 2026-07-30 — 免费额度用完的两个入口（变现漏斗接回来）

`actionReason === 'free_tier_exhausted'` 时多渲染两个按钮：**升级 Nexus Pro** → `/pay`、
**用我自己的 provider** → `/app/settings?tab=providers`（`?tab=` 深链来自 #211，零新增 API）。
它们在**气泡外**，与 meta row 同层。

第一个按钮**原本指 `/app/settings?tab=account`，2026-08-01 改为 `/pay`**：#223 的
[[PayPage]] mint 完 checkout session 后同标签页 `location.replace` 直达 Stripe，一跳完成；
而它每一种退化情况（已订阅 / 桌面 webview / 非 Power / 401）**都回落到账户页** —— 也就是
这个按钮原来的目标。所以绕设置页换不到任何东西。套餐同期改名 Nexus Pro（#222），用词与既有
「升级 Nexus Pro」对齐。

**为什么只有这一个 reason 有按钮**：其他 reason 的补救措施用户自己能想到（换模型、改
model id、给自己的账号充值）。免费额度用完是唯一一个「文案里那两条出路他都做不到」的情形
—— 钱包不能自助充值、key 从未在他手里 —— 所以真正可行的两条路必须直接给出来。BYOK 卡余额
不足**刻意不给**这两个按钮：那是他自己的账号，充值本来就可行，塞一个升级引导是劫持。

**为什么在气泡外**（2026-08-01）：初版放在正文里，也就是压在 isError 气泡的**实心
`--color-error` 填充**上。本项目所有控件令牌都假定**纸底** —— `--text-secondary` 70% 墨、
`--border-default` 22% 墨，压红上一个发浑一个消失；`--accent-primary` 更糟，亮色是墨、
**暗色是奶白**（配 `text-white` 直接白字白底）。为此连修三轮特例令牌（`--on-error` /
`-hair` / `-fill`），每轮修好一个又露出下一个：填充按钮和描边按钮**在实心色块上无法共用
同一个文字色**（前者要深字后者要白字），于是主次一对总是不成对；把主按钮降成半透明填充
求同色，它又糊进气泡里没了边界。

根因是位置不是配色。挪到气泡外的纸底上后，直接用 `<Button variant="accent">` +
`<Button variant="outline">` —— 这本来就是全站的主/次一对，`--on-error` 那组特例令牌随之
从 index.css 删除。放置层级与下方 meta row 相同（同为「拉到气泡外」），`gap` 比相邻轮次紧
以示同属一轮。**规则：不要给实心语义色表面造平行调色板，把可操作控件放到纸底上。**

本组件因此开始使用 `useNavigate`，三个既有测试文件随之补上 `MemoryRouter` —— 它们此前
不带 Router 直接 render 能过，只是因为组件恰好没用过 router hook；运行时 chat 一直在
`/app` 下。

## 2026-07-30 — 助手轮按段渲染（SegmentedReply）

一轮后端仍是一条记录，但 agent 可能说了 m 次话。消息带 `segments`
（stopStreaming 切好）或 event-log fetch 后由同一个 `segmentTurn` 现切
时，走 segment 模式：每次「说话」带自己的可折叠过程区，替代 content
整块渲染——两者都画会把每句话打印两遍。

- **fetch 已是轮次级**：一条消息一次 `getEventLog`，切段后服务段内
  全部 m 个片段；历史气泡点开「View reasoning」后从单块升级为按段。
  fetch 路径传 `defaultOpen`（2026-07-30 r2）：用户已经点过一次，过程
  直接展开，不落在第二层折叠入口上。
- **零回复轮次不特殊处理**（design §3）：segments 里没有任何 reply
  就回落 legacy 路径——content 是 "(Agent decided no response
  needed)"，过程在全局 toggle 后面。isError 消息同样不走段模式。
- content 保留 join 全文：通知/复制/下载/搜索仍用它，老消息兜底。

## 2026-07-23 — full date on time hover

The HH:mm:ss stamp gained `title="{formatDate} {formatTime}"` so a
single message reveals its calendar day on hover without needing a
day separator in view.

## 2026-07-22 — executor-infra 徽章标题区分

`actionReason` 徽章标题按类别选：reason 为 `infra_transient` 或以 `executor_`
开头 → `chat.error.titleInfra`（"执行环境异常"）；否则沿用
`chat.error.titleActionable`（"Action needed"）。正文复用现有
`chat.error.action.${actionReason}` 路径（executor_oom/executor_unreachable 的
i18n key 天然区分，10 个 locale 均已补）。因为平台侧失败的补救是"重试/拆小任务"，
不是"去 Settings 改配置"。

## 2026-07-14 — actionable popover for config_actionable failures

The same red badge/popover now branches on `message.actionReason`. When set
(a deterministic self-serviceable failure — context window too small / no
credits / bad model id, mirrored from backend `config_actionable`), the popover
shows the localized "Action needed" title + per-reason "what you can do"
guidance (`chat.error.action.<reason>`, falling back to
`chat.error.action.generic`) instead of the generic "Run failed" / "Finished
with errors" copy. The raw provider detail (English, carries the concrete token
numbers) still renders in the mono block below. This is the user-facing end of
the "black box" P1 fix — the turn no longer silently masks a fixable cause.

Two rendering fixes exposed by the first live test of this path:
- **Red-on-red bug**: an `isError` bubble already sets a solid red background +
  white text on the CONTAINER, but the content `div` was ALSO forcing
  `text-[var(--color-red-500)]` → red text on red bg → an empty red box
  ("大红框里什么都没有"). Removed the override so the body inherits white. This
  was pre-existing but only surfaced now: before the P1 fix, context-window
  errors were `recoverable` + masked, so a fatal `isError` bubble rarely
  rendered at all.
- **Body copy for actionReason**: the bubble BODY now shows the localized
  guidance line (same key as the popover), NOT the raw English `error_message`
  blob (guidance + "Provider detail: {json}"). Full detail stays in the
  popover. Requires the `actionReason` prop to actually reach here — see the
  wiring in [[buildTimeline.ts]] + [[ChatPanel.tsx]].

## 2026-07-03 — red error badge (any error surfaces on the bubble)

A red AlertCircle badge sits at the bubble's top corner whenever the message
carries isError (whole turn failed: no reply / silent fallback / expired login
— content IS the error text) OR warnings (reply came through but something
errored). Click opens a Popover explaining the situation (failed vs
finished-with-errors) + the raw error detail. The pre-existing red-bubble/red-
text/amber-warning-list rendering is kept; the badge is the unified, always-
visible entry point the Owner asked for.

## 2026-06-20 — own bubble switched to Carbon (reverses the 2026-05-19 gray rule)

Per the Narra Agent App design ref, the user's own bubble is now the **Carbon
(human) species variant**: `--color-carbon-soft` fill, `--color-carbon-hair`
border, 3px solid `--color-carbon` stripe on the RIGHT — mirroring the AI
bubble's silicon-on-the-LEFT. This **supersedes** the 2026-05-19 "own bubble
stays neutral gray, species reserved for the other party" decision below: the
product reads as an explicit human(carbon)·AI(silicon) dialogue, and a fresh
product decision (Owner) chose that contrast over the multi-user-fan-out
rationale. Both tints flip in dark mode via token redefinition. If multi-user
rooms land later, sender-species disambiguation must be re-solved another way
(it is no longer carried by "own = gray").

**Meta row moved OUTSIDE the bubble** (reverses the 2026-05-19 "footer inside"
note below): the time + copy/download row now sits just below the bubble,
aligned to the bubble's side (own → right, agent → left), so the bubble wraps
only its content and loses the internal footer whitespace — a tighter, more
refined bubble.

## 2026-05-20 — assistant avatar label uses agent name (was hardcoded 'A')

The assistant `RingAvatar` label was a literal `'A'` regardless of which agent
was replying, so every chat looked identical and didn't match the sidebar
`[[AgentList]]` (which derives its label from the first 2 chars of the agent
name). Added an `agentName?: string` prop; `avatarLabel` for assistant messages
is now `agentName?.slice(0, 2) || 'AI'`. `ChatPanel` passes
`agentName={currentAgent?.name || agentId}`. Note: `AgentInfo` carries no avatar
*image* field — initials are the canonical avatar everywhere, so this just
brings the bubble in line with the list.

## 2026-05-19 — NM canonical FinBubble styling

Bubble surfaces rewritten to match the NM design's `FinBubble` (light-blue silicon fill + 3px LEFT species edge for AI, neutral `--nm-own-bubble` gray fill + 3px RIGHT `--nm-own-edge` for own). The species (carbon/silicon) tokens are now reserved for the *other* party — multi-user fan-out semantics: in a future shared room the *receiver* will see the *sender* in a species color, but your own outgoing messages stay gray because you don't need a species cue to identify yourself.

Footer (time + copy/download) moved INSIDE the bubble, bottom-right, mono 9.5px in `--nm-subtle`. All `<BracketEdge>` corner marks removed — the radius + 3px edge stripe carry the species/own signal alone, no top-left rectangle.

# MessageBubble.tsx — Single message row with lazy-loaded thinking/tool-call details

## 为什么存在

Renders one message in the timeline. Handles two very different data contexts:
1. **Real-time** (session messages): thinking and tool calls arrive inline from the WebSocket.
2. **History** (DB messages): thinking and tool calls must be fetched on demand from `GET /event-log/{event_id}`.

## 上下游关系
- **被谁用**: `ChatPanel`.
- **依赖谁**: `Markdown`, `api.getEventLog`.

## 设计决策

**Lazy event log loading**: History messages carry an `eventId`. The first time the user clicks "View reasoning & tools" (or expands the thinking/tools section), the component fetches `GET /event-log/{event_id}`. Results are cached in a `useRef<Map>` — no store, no prop drilling, component-local cache.

This design avoids loading event log details for every message in a long history page, keeping the history load fast.

**`canLoadEventLog`** flag: `true` only when the message is an assistant message with no real-time data and has an `eventId`. Prevents pointless API calls for user messages or streaming messages.

**Copy and Download**: Available on completed assistant messages only. Download saves as `.md` with a timestamp in the filename.

**Inline `ToolCallItem` and `ToolCallOutput` components**: Defined in the same file because they are tightly coupled to `MessageBubble` rendering and have no other consumers.

## Gotcha / 边界情况

The event log cache (`eventLogCacheRef`) is per-component-instance. If the same message is rendered multiple times (e.g., after re-keying), the cache is lost and the API is called again.

`tool_output` is only present on `EventLogToolCall` (history), not on `AgentToolCall` (real-time WebSocket). The output section only renders for history messages.
