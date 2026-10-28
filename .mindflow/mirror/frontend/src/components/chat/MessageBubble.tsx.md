---
code_file: frontend/src/components/chat/MessageBubble.tsx
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — no-reply markers are localized on render (GitHub #87)

`visibleContent` (the single text source for the bubble, copy and
download) now runs assistant content through `localizeTurnMarker` from
[[turnMarkers]] after `stripAgentDraft`: content exactly equal to
`(Interrupted by user)` renders `chat.stoppedByUser`, content exactly equal
to `(Agent decided no response needed)` renders `chat.noResponseNeeded`;
anything else passes through. The store and the backend both keep the
English marker as the stored content, so a live bubble and its reloaded
history row read the same in the viewer's language. Test:
`__tests__/MessageBubble.turnMarkers.test.tsx`.

---

## 2026-09-07 — a throwing plugin message renderer is isolated (I-6)

The shell-bubble JSX (the fallback path when no plugin renderer matches) is extracted into a
`renderShellBubble = () => (...)` closure. When a renderer DOES match, it is now wrapped in
`<PluginBoundary owner={rendererOwner} fallback={renderShellBubble}>` instead of being rendered
bare. Before this, a throwing message renderer bubbled to the route-level `ChunkErrorBoundary`
above `MessageBubble`, replacing the ENTIRE conversation with an error page over one bad message
— see `platform/PluginBoundary.tsx`'s mirror doc for the boundary itself and why it is a shared
module rather than duplicated per content registry.

---

## 2026-09-03 (修订同日早条) — 两个方向都要剥

早条只讲了用户消息里的指令。现在两个方向都有机器：**用户**消息裹着指令 +
当前配置信封，**助手**消息尾部有 `<agent_draft>` 配置块。`visibleContent` 按
`isUser` 分派到 `decodeBuilderTurn` / `stripAgentDraft`，用在气泡、复制、下载
三处出口。

剥离必须留在**渲染路径**而不是 store：[[useStudioTurn.ts]] 要从已落定消息里
parse 出原始块。

## 2026-09-03 — 剥掉 Builder 指令块

新增 `visibleContent = stripBuilderInstruction(message.content)`，用在**所有
展示消息文本的出口**：用户气泡、复制、下载。

为什么必须做：创建工作室 v0 的 Builder 指令藏在**用户消息**里（对话跑在用户
自己的 agent 上，没有别处可放 —— v0 不擅自写 Awareness）。漏一处，整段 prompt
就出现在用户自己的气泡里。普通消息原样透传，所以对全部流量安全。

助手侧分支没有改 —— 我们的标记只会出现在用户消息里。
## 2026-08-31 — 历史轮次的抽屉：自己把自己卸载了

Owner 报了两个症状：点开「查看推理与工具」后**推理仍是折叠的**，而且**再也
合不上**。两个症状来自两条不同分支，根因是同一个：

**那个开关的显示条件是 `segmentsForRender === null`，而它触发的 fetch 恰好
就是让它变成非 null 的东西。** 点击 → 拉到 event log → `segmentTurn` 切出
带 reply 的 segment → 整个持有按钮的块被卸载 → 过程留在展开态、按钮消失。
一个**能被 fetch 翻转的谓词，不能用来管这个 fetch 自己的开关**。

另一条分支（无 reply 的后台轮次）`segmentsForRender` 始终为 null，所以按钮
还在、能收合，但它渲染 `TurnTimeline` 时**没传 `defaultOpen`**，于是用户花了
一次点击换来第二个折叠的开关。

修法：

- 新增 `processIsRemote`，**只由 props 推导**（`canLoadEventLog` 且没有
  `message.timeline` / 带 reply 的 `segments` / 错误态）。fetch 翻不动它，
  所以开关在整个交互过程中稳定存在。
- 开关**只出现在欠一次 fetch 的轮次上**。过程已在手的轮次（直播落定的、
  带 timeline 的）直接内联铺开，和 [[ChatPanel]] 的直播态同一形态，不花点击。
- 推理**保持折叠**。第一版我在这里传了 `defaultOpen`，理由是「用户已经点过
  一次」——Owner 当场否掉：点开过程是要看**结论**（叙述句 + 工具行），不是要
  读模型的草稿纸。provider CoT 比其余所有内容加起来还长，自动展开等于把读者
  真正来看的东西埋掉。`defaultOpen` 因此在全链路删除（见 [[TurnTimeline]]）。
- segment 路径的 `showProcess` 也交给同一个开关（`!processIsRemote ||
  showDetails`），所以 fetch 把气泡升级成分段渲染之后，按钮依然管得住它。

**为什么保留这个开关、而不是像别处一样彻底去抽屉**：它守的不是版面而是
**一次网络请求**。历史消息的过程只在服务端，内联铺开就等于滚动时每条消息
一次 fetch。付过代价之后能收起来，是合理的；别处的抽屉藏的是**已经在手的
内容**，那才是纯粹的阻碍。这条区分写在这里，免得下次「统一」时一起删掉。

## 2026-08-30（二）— agent 一轮退出气泡，成为页面正文

用户消息**保持气泡**（warm 填色 + hairline + 右缘 carbon 条 + carbon 头像
环）：它是一条发出去的话，有边界是对的。**agent 的一轮不再取表面层**——无
填色、无边框、无圆角、满宽，直接坐在页面底色上。规范写进
`design_system.md` §2.6。

三个连带决定，都不是可选项：

- **agent 侧头像去掉。** RingAvatar 会开一条 gutter，把文档挡在满宽之外，
  并且把这一轮重新读成"又一条消息行"。谁在说话由上方的用户气泡（每轮的
  锚点）和 header 里的 agent 名承担。silicon 仍是 agent 身份色，只是不再
  用来给回复画边框（design_system §1）。
- **错误一轮不再是实心红面板**：改左缘 `--color-error` 细条 + 正常 ink
  正文。满宽实心红是警报不是阅读；而且**那个实心填色正是当年把补救按钮
  挤到气泡外的原因**（见下方「红底压红字」段）——面没了，那个约束也就跟着
  消失了，按钮留在原处只是因为没必要动它。
- **错误徽章改为块首行内**。气泡有角可以挂 `-top-2 -right-2`；满宽文档没有
  角，挂在最右边会读成脱离正文的浮标。

`isUser` 仍然走原来那条气泡分支，一个像素没动。

`agentName` prop 随头像一起删除（它只喂过那个头像标签）——三个调用点同 commit
清掉，不留一个没人读的 prop（铁律 #2）。

### 刻意没做的事

- **没给 agent 一轮加"谁在说话"的替代标记**（名字行 / 小图标 / 色条）。
  单聊里 header 已经写着 agent 名，再加一个就是把刚拆掉的框换个形状装回来。
  **多 agent 场景（团队房）不走这条路**——那里每条消息本来就带作者名。
  真要在单聊里区分多个 agent 时再说，现在加属于预支。
- **turn 之间没加分隔线**，理由见 design_system §2.6：一轮一条横线在长对话
  里会堆成流水账。间距 + 用户气泡锚点已经够。
- **没动 markdown 内部的表面**（code / table 仍取 L2 warm）。它们是文档里的
  嵌套面，本来就该取层；这次退出阶梯的是**回复容器**，不是它的内容物。
- **没碰 `nm-bubble-*` 相关的历史 CSS**：那批随 2026-08-06 的 v4 纸面气泡
  已经退役，与本次无关。

## 2026-08-28 — 单轮用量终于出现在 Conversation 里

`/event-log` 的响应从运行卡片改版起就带着 `meta`（本轮 token / 花费 / 时长 /
模型），但只有 Inner Thoughts 那张卡片渲染它：用户在自己的聊天里只能从顶部
popover 看到 Agent 级 7 天汇总，看不到任何一轮的消耗。而这个气泡为了「查看
推理」本来就在 fetch 同一个响应——`response.meta` 一直被丢掉。现在存进
`eventLogMeta`，用共享的 [[RunStatChips]] 渲染。

**为什么 chips 放在 disclosure 外面而不是里面**：同一次 fetch 可能把气泡升级
成 segment 模式（`segmentsForRender` 一旦非空，disclosure 整块 unmount）。放
里面的话，chips 会恰好在**有回复的那些轮次**上一闪就没——也就是最该看到它的
那些轮次。

**已知边界**：懒加载沿用原有触发点，用户点开一次「查看推理」才发请求（否则
每条消息一个请求）。刚跑完、还带着实时数据的那一轮 `canLoadEventLog` 为
false，chips 要等下次按历史加载时才出现。这是刻意不动的既有取舍——放宽触发
条件会改变 `inlineEvents` 的数据源优先级，属于另一件事。**Owner 已确认保留
现状**（2026-08-31）：曾提案改为常驻展示（chat-history 接口顺带返回 event
花费汇总），结论是当前形态够用。

`hasEventLogData` 把 `eventLogMeta` 也算进去（08-31）：一轮没有 thinking、没
有工具调用的纯回复，fetch 回来只有 meta，漏掉它会让"是否已加载"对这类轮次一
直为 false。今天无害（`eventLogCacheRef` 会短路重复请求），但这个标志一旦在
缓存加上过期策略后就开始说谎。

## 2026-08-26 — assistant 回答改纯文本(答案无气泡),从 feat/chat-ui-v4-dev-merge 分支移植

从 `40d353e1`(feat/chat-ui-v4-dev-merge 分支,未并入 dev 主线)移植「answer 无气泡」这一处
设计改动到 dev。`isPlainAssistant = !isUser && !message.isError`:仅 user 轮保留纸面气泡 +
`RingAvatar`(carbon 环);assistant 正常回复变成无卡片、无描边、无头像的纯文本块
(`block w-full`,只留 `color: var(--nm-ink)`)。**推翻**了 2026-08-06「own/AI 气泡都是纸面
+ 3px 描边」的决定 —— 现在只有 own 气泡还保留 `--nm-paper-warm` 填色 + 右侧 carbon 描边;
AI 侧的 `--nm-paper` 填色 + 左侧 silicon 描边整套退役。

isError 气泡是唯一仍保留卡片处理的 assistant 情形 —— 红色实心卡片是需要用户主动注意的
标记态,不是普通回复,所以不下放到纯文本。

连带清理:`agentName` prop 整个删除(assistant 侧不再需要头像,`avatarLabel` 也就只剩
user 分支),**推翻** 2026-05-20 那条「assistant 头像用 agent 名字前两位」的决定 —— 头像
本体已经不存在了。`RingAvatar` 渲染也从无条件改为 `{isUser && <RingAvatar .../>}`。
`ChatPanel` 两处 `<MessageBubble agentName={...} />` 随之删除。

只移植了这一处气泡改动,未连带 `40d353e1` 里的 Messenger 侧栏合并 / AgentOverviewCard /
Provider 创建向导拆分等其余内容(那些是同一个 commit 里打包的独立功能,未经评估不应该
一起带过来)。

## 2026-08-24 — user 气泡三态尾标

带 `steerStatus` 的 user 气泡在正文下渲染一行小字:`queued`(排队中)/`merged`(✓已并入)/`rejected`(未送达+i18n reason,error 色)。i18n `chat.steer.*`。仅 owner 运行中 follow-up 气泡有,普通消息无。reason 渲染:有 `rejectReason`→`chat.steer.reason.<reason>`(缺 key 时 `defaultValue` 回退原串);`rejectReason` 为空(后端 reject 不带 reason)→回退 `chat.steer.reason.unknown`,**不**渲染成「未送达 — 」这种破折号后空白。

## 2026-08-19 — 历史时间线的 tool_output 承接调用名(实现并入 segmentTurn)

存储态 timeline 的 `tool_output` 条目常不带 `tool_name`,旧兜底把字面
'unknown' 打在每一行 [输出] 后面。Path-1 的手写转换副本删除,改调
[[segmentTurn]] 的 `timelineToEvents(tl, { convertOwnerReplyTool: false })`
——顺承逻辑只有一份;`false` 表示折叠态把 reply 工具当普通 process 行,
是显式设计差异。无可承接时留空串,由 [[TurnTimeline]] 隐藏空名。
测试:messageBubbleToolOutputName.test.tsx(真实渲染路径)。

## 2026-08-18 — 试过恒定列宽(w-full),当天撤回

短回复右侧留白一大片,Owner 对照实屏否了;保持双方气泡贴内容宽度。
代码里留了注释防止再试一遍。

## 2026-08-06 (2) — meta 行 claude.ai 化

- meta 行(时间+按钮)默认隐藏,行 hover 显现(根节点加 group);
  **仅最新一条**(ChatPanel 传 isLatest)常显 — cc 同款。
- user 消息也有 copy 了(download 仍仅 assistant — 用户消息不是 md 文档)。
- 时间从 HH:mm:ss 改为相对时长(lib/utils formatMessageAge,
  Intl.RelativeTimeFormat 按 i18n.language 本地化,秒→分→时→天→月→年);
  悬停 title 给完整年月日+时间。

## 2026-08-06 — Chat UI v4:纸面气泡

own/AI 气泡填色从物种 soft 色(carbon-soft / silicon-soft)改为纸面
(--nm-paper-warm / --nm-paper)+ hairline 边;物种信号完全由 3px 描边
(own 右 carbon / AI 左 silicon)+ 头像环承担 — 这是 Owner 在 v4 设计稿
里亲自定的,覆盖 2026-06-20「own = carbon-soft 填色」的旧决策。
nm-bubble-ai(蓝色 code/table 重绑定)随 silicon-soft 填色一起退役。
错误气泡(实心 error 红)与「补救按钮放纸面上」规则不变。时间戳 9.5→10px。

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

Merged with the plugin platform (2026-09-06): pages, drawer panels, sidebar items, commands and agent-row badges come from the frontend registries (`platform/registries`, registered in `platform/builtin.ts`); this file keeps dev's behaviour on top of that.
