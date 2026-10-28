---
code_file: frontend/src/pages/AgentProfilePage.tsx
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — rename report + delete moved into `useAgentActions` (shared with the sidebar)

The sidebar agent row's ⋯ menu is back (Owner-required), so this page is no
longer the only door for rename / delete. `warnAboutUpdateSideEffects` and the
confirm → `api.deleteAgent` → refresh → `clearAgent` → re-point-active sequence
now come from [[../hooks/useAgentActions]] (called right after `useConfirm`,
before the early returns — rules of hooks), using this page's own confirm
host. `handleDeleteAgent` keeps the `deleting` flag and navigates to
`/app/dashboard` when the hook reports `deleted`. Behaviour is unchanged
(this page makes its agent the active one on mount, so the hook's
"re-point only if active" branch always fires here). Clear data stays
page-only; the header-kebab and clear-data comments were corrected to say so.
New test: Delete confirms, calls `api.deleteAgent`, clears the session and
lands on the Dashboard.

## 2026-09-04 — framework 标签/图标改走 `lib/frameworkBrand`

本地 `FRAMEWORK_ICONS` 与 `formatFramework` 删除，`frameworkBrandIcon(undefined)` 仍返回
Bot、`formatFramework(undefined)` 仍返回 `—`（评审二轮 I2）；暗色反色改问 `iconInvertsInDark`
（三轮 M4）。

## 2026-09-03 (评审修订) — owner 门禁、删除清 store、`key` 只留 agentId、框架不兜底

- **I2**：`isOwner = agent.created_by === userId`，据此隐藏头部 ⋮ 与 Settings tab。用
  agent 列表而不是 `ownedStatus`：后者来自 dashboard 状态流，首帧为 null 会让 owner 自己的
  按钮闪一下。这是侧栏 kebab 下线时丢掉的既有门禁。
- **I3**：删除成功后 `await refreshAgents()` → `clearAgent` → `setAgentId(remaining[0] ?? '')`
  再导航。本页挂载时把全局 active agent 指到了它，不复位就会让 /app/chat 挂在死 agent 上，
  且 `agents` 持久化在 localStorage 里会留鬼行。
- **I5**：`GeneralSettingsPanel` 的 `key` 只留 `agentId`。含 name/description 时改名成功
  即换 key 重挂载，「已保存」永远不出现。
- **C2**：`framework` 不再 `|| 'claude_code'`；缺失渲染 `—`。兜底在后端 model_identity。

## 2026-08-27 (3) — Overview 补上「最近怎么样」这一层

本页原本只答「这个 agent 是什么」(概览卡)和「它手上有什么」(Jobs / Inbox),
**没有时间维度**——没有活动节奏、看不到今日成本、不知道谁正在跟它说话。
同日 Dashboard 拆掉行内展开后,`Sparkline` / `MetricsRow` / `SessionSection` /
`RecentFeed` 正好是这个缺口的现成材料,收进新的
[[../components/agents/AgentActivityCard.tsx]]。

Overview 现在的纵向顺序,**每一层都比下一层更概括**:

1. `AttentionBanners` — 置顶。任务失败 / 依赖受阻 / 额度停摆不属于同一个面板,
   这是唯一跨领域的一层,而且是「你得管一下」的等级,必须在首屏。
2. `AgentOverviewCard` — 静态身份,不动。
3. `AgentActivityCard` — 时间维度。
4. Jobs / Inbox 两栏 — 具体条目,不动。

顺序不是随手排的,`AgentProfilePage.ui.test.tsx` 用 `compareDocumentPosition`
钉住了 2→3。

**两块都由 `ownedStatus` 守门**。它们读的字段全在 `OwnedAgentStatus` 上,公开
agent 只有 `PublicAgentStatus`,`ownedStatus` 为 `null` 时两块都不渲染——这既是
隐私边界也是类型边界,不需要在组件内部再判一次。首次进页面 `ownedStatus` 可能还是
null(要等一次轮询),**刻意不给骨架**:闪一下骨架再换成内容,比晚半秒出现更吵。

**没有收进来的两个**:`QueueBar` 原计划移进 JobsPanel 头部,实现时发现
[[../components/jobs/JobStatusMeter.tsx]](同日新增)已经就是那个东西,塞进去等于
把它刚消灭的重复再造一遍。`JobsSection` 的内容 JobsPanel 本来就覆盖。
这两个组件因此**已于同日删除**(Owner 拍板)。

## 2026-08-27 (2) — 头部 ⋮ 加 "Clear data"(在 Delete 上面),改名提示一并接管

侧边栏 agent 行的 ⋮ 被 Owner 拿掉([[../components/layout/AgentList.tsx]]
同日条目),那个菜单里的两件事必须有新家,本页头部 `AgentHeaderMenu` 承接:

- **Clear data** 排在 Delete **上面**,顺序按 blast radius:清数据保住
  persona / channel 绑定 / 账号,删除是整个 agent 没了。样式上 Clear 是
  普通 ink 项,Delete 仍是红字,视觉上不并列。弹窗直接复用
  [[../components/layout/ClearAgentDataDialog.tsx]],本页持有
  `clearOpen`/`clearBusy`,成功且勾了 conversations 时
  `clearAgent(agentId)` + `requestHistoryRefresh()`——否则挂着的 ChatPanel
  会继续显示服务端已经删掉的消息。
- **改名的两个"成功但你得知道"**(`name_clash_with` /
  `identity_record_updated === false`,深圳 P1 的起点)原来只在 AgentList
  的三个改名调用点里报。本页 Settings → General 现在是**唯一**的改名入口,
  所以 `warnAboutUpdateSideEffects` 跟着搬过来,由 `GeneralSettingsPanel`
  的 `onWarn` prop 在 `setSaveState('saved')` **之后**调用(先让面板显示
  已保存,再弹提示)。i18n key 随组件删除从 `layout.editAgentDialog.rename*`
  改名为 `layout.agentRename.{warnTitle,clashWarn,memoryWarn}`,10 语言同步。

上方 08-25 (3) 那条里"AgentRowMenu 带四个本页 Settings 已有归宿的入口,
所以不复用"的判断仍然成立——现在那个组件已经删除,本页的 ⋮ 是全站唯一的
agent 操作抽屉。

## 2026-08-27 — 从 chat-ui-v4 移植进 dev,两处按本分支现状调整

页面本体(Overview / Capabilities / Settings 三 tab、AgentOverviewCard、
AgentHeaderMenu)整体照搬 `feat/chat-ui-v4-dev-merge` 的
`40d353e1`,只改了两处**必须**改的:

- 删除成功后跳 `/app/dashboard`,不是 v4 分支写的
  `/app/dashboard?view=agents`。本分支 [[DashboardPage.tsx]] 的 tab 参数
  叫 `?tab=`,且 agents 是默认视图(写回时是「删掉参数」),`?view=agents`
  在这里是个会被 `parseTab(null)` 忽略的无效参数。
- 入口只有两个:[[../components/chat/ChatHeader.tsx]] 的身份块
  (`from: 'chat'`)和 Dashboard 智能体行的身份块(`from: 'dashboard'`)。
  v4 分支的第三个入口 `agents/new` 整页创建向导**没有**跟着移植,本分支
  创建 Agent 仍走弹窗。

依赖在 dev 侧已经齐备,无需迁就:`BookmarkPanelHost` 支持
network(→social)/memory/skills/mcp/channels/awareness 全部 tab,
`JobsPanel`/`AgentInboxPanel` 都有 `embedded`,后端
`/api/auth/agents` 已投影 `agent_framework`/`model`/`bound_channels`。
新增的只有 [[../hooks/useMCP.ts]] 与 [[../components/agents/AgentOverviewCard.tsx]]。

## 2026-08-25 (4) — Overview 顶部三卡 + 头部 Framework/Model 合并成一张归属信息卡

Owner 反馈 Current Task / Jobs / Inbox 三张独立 `PaperCard` 摆在
Overview 顶部,视觉上像可点入口,实际都不可点,是纯粹的 agent 归属
信息;头部 meta 行里的 Framework/Model 又跟 team/last-active 混排,
语义上更该跟"这个 agent 是什么"放在一起。改法:三卡 + 头部
Framework/Model 合并进新组件 [[AgentOverviewCard.tsx]],再补上
Skills/MCP 摘要(此前 Overview tab 完全看不到,只能去 Capabilities
tab)。设计定稿过程(三轮视觉稿迭代,最终选定"纵向清单 + 无边框圆角
tile,不用分割线"的方向)记录在
`reference/self_notebook/specs/2026-08-25-agent-profile-overview-card-
design.md`。头部 meta 行删掉 framework/model 两个 `MetaItem`,只留
agentId/team/last-active 三项;`SummaryCard` 局部组件随三卡一起
删除,不再被引用。

## 2026-08-25 (3) — Chat 放大,Delete 收进 "⋮" 抽屉

Owner 复审 (2) 的结果:Chat 按钮该更醒目(放大,不再是 Dashboard 表格行那种
紧凑尺寸)、Delete 不该跟 Chat 平级摆在一起(误触代价太高),要收进一个
"⋮" 点开的小抽屉里——参照给的参考图(浅色 Chat pill + 黑色主操作 + "⋮" →
点开弹出白底红字的 Delete 项)。改法:`agentChatButtonClass` 的字号/内边距
从表格行尺寸(`text-[11px]` / `px-2.5 py-1.5`)放大到 `text-sm` /
`px-4 py-2`,圆角跟着从 `radius-xs` 升到 `radius-sm`——保持"浅色卡片描边、
非 ink 填充"的样式家族,但不再要求跟 Dashboard 逐字节一致(尺寸语境不同)。
新增 `AgentHeaderMenu` 局部组件承接 Delete:样式与交互抄自已退役的
[[AgentRowMenu.tsx]](click-outside 遮罩 + 右对齐绝对定位面板),但**没有
复用那个组件本身**——它带 rename/edit/clear/public-toggle 四个跟 Delete
无关的入口,而这四个操作本页 Settings tab 已经各有归宿,硬套进来等于为了
第五个入口多接四个不需要的 handler。菜单项文案复用已有的
`layout.agentRowMenu.options`/`layout.agentRowMenu.delete`(触发按钮
aria-label / 菜单项 label),确认弹窗文案仍是 (2) 里选定的
`layout.agentList.deleteAgentTitle/deleteAgentMessage/deleteAction/
deleteFailedTitle/deleteAgentFailedMessage` 五个 key,删除流程本身
(`useConfirm` → `api.deleteAgent` → 成功 `navigate` / 失败 `alert`)不变。

## 2026-08-25 (2) — 头部 Chat 按钮去黑,加 Delete(后续 (3) 里改成抽屉里)

Owner 反馈头部的 Chat 按钮用 `Button variant="primary"` 是纯黑填充,视觉太重,
要求跟 Dashboard 智能体列表行的 Chat 按钮同款样式。改法:不复用 `Button`
组件,直接照抄 [[DashboardPage.tsx]] `agentChatButtonClass` 的类名字符串
(浅色卡片描边 pill,而不是 ink 填充)。同时在旁边加一个 `Button
variant="danger"` 的 Delete 按钮,复用已有的
`layout.agentList.deleteAgentTitle/deleteAgentMessage/deleteAction/
deleteFailedTitle/deleteAgentFailedMessage` 五个 i18n key,删除走
`useConfirm()` 确认弹窗(`danger: true`)→ `api.deleteAgent` → 成功后
`navigate('/app/dashboard?view=agents')`,失败弹 `alert`。本页此前没有引入过
`@/components/ui`,新增的 `useConfirm` 是本页第一次跨这两套组件系统
(`@/components/nm` 的 `Button` + `@/components/ui` 的
`useConfirm`)——两者互不依赖,可以在同一文件混用。Delete 按钮本身的位置
(跟 Chat 平级)被 (3) 推翻,收进了 "⋮" 抽屉。

## 2026-08-25 — breadcrumb 返回目标按来源区分:Dashboard vs Chat

Owner 指出:profile 页现在有两个入口——Dashboard 列表行(`DashboardPage.tsx`)
和 Chat 头部头像(`ChatHeader.tsx` 的 `goToProfile`),原来 breadcrumb 一律
`navigate('/app/dashboard')`,从 Chat 进来的用户点返回却被送去 Dashboard,
不是原路返回。改动:两个调用方 `navigate(...)` 时都带上
`state: { from: 'chat' | 'dashboard' }`;本页新增 `ProfileEntryState` 类型,
用 `useLocation().state` 读出来源,`cameFromChat` 为真时 breadcrumb 文案换成
`pages.agentProfile.backToChat`("Chat"/"对话"),点击复用既有 `openChat()`
(而不是裸 `navigate('/app/chat')`)以确保 configStore/chatStore 的活跃 Agent
先被正确设置;否则回退到原来的 `navigate('/app/dashboard')`。`location.state`
在刷新页面后会丢失,此时按设计回退到 Dashboard——这是可接受的默认值,不是 bug。

# AgentProfilePage.tsx — Agent 的独立工作与能力档案

## 为什么存在

Dashboard 是可扫描的目录,不应因展开某一行而被大块运行详情打断。本页承接
`/app/agents/:agentId`,把单个 Agent 的工作状态、真实操作面板和能力配置组织成
稳定的 Profile。进入页面时把路由 Agent 写入 configStore,从而让 MainLayout
既有 preload/auto-refresh 链路为正确 Agent 水合数据。

## 信息架构

- Overview:归属信息全部收进 [[AgentOverviewCard.tsx]] 一张只读卡——
  Framework/Model(原页头 MetaItem)、Current Task(读
  `AgentInfo.active_run` 与 dashboard status)、Jobs/Inbox 计数(读
  preloadStore)、Skills/MCP 摘要(卡内部自己订阅
  `useSkillsList`/[[useMCP.ts]],不经过页面 props)。整卡不可点击,与
  下面两个真正可点击的完整面板区分开;两个完整面板仍直接复用
  `JobsPanel embedded` 与 `AgentInboxPanel embedded`,不复制数据访问
  或操作逻辑。
- Capabilities:左侧原子目录一次只挂载一个能力。Network/Memory/Skills/MCP/
  Channels 复用 `BookmarkPanelHost` 的既有 tab 映射。
- Settings:位于 Capabilities 右侧,左侧顺序固定为 General → Awareness →
  Model & Framework。General 复用现有 `api.updateAgent` 保存名称与描述,保留 255
  字符前端约束并显示当前身份头像预览;Awareness 复用 `BookmarkPanelHost`;
  Model & Framework 显示 agents 列表中的有效值,配置动作打开既有
  `AgentLlmConfigPanel`。配置入口集中在 Settings,不再重复出现在 Capabilities。
- Header:Agent 身份、运行状态、Team、Last active,以及放大版浅色 Chat
  pill + "⋮" `AgentHeaderMenu`(Delete 收在里面,红字)。Framework/Model
  2026-08-25 起搬进 Overview 的 [[AgentOverviewCard.tsx]],不再出现在
  页头(详见上方 changelog (4))。Team 使用双人语义图标加共享
  `AgentTeamAvatars`,因此与 Dashboard 保持同一套双色重叠头像和悬浮
  Profile。返回 breadcrumb 按入口来源(`location.state.from`)分别指向
  Dashboard 或 Chat(2026-08-25,详见上方 changelog),因此 MainLayout
  不再为此路由叠加浮动 X。

## 数据与性能边界

页面直接打开时补拉一次 dashboard status;Agent 列表与 Teams 沿用 store refresh。
Capabilities 与 Settings 都保持“一次一个重面板”的原子挂载约束,避免 Awareness、
Network、Memory、Skills、MCP 和 Channels 同时发请求或加载重依赖。General 沿用
现有 Agent 更新接口,Model 配置沿用既有面板；页面不新增后端接口。

## 2026-09-04 — Settings gets a fourth section: Capabilities

`SettingsSection` widened from `'general' | 'awareness' | 'model'` to add
`'capabilities'`, with its own left-nav item (`Puzzle` icon, between
Awareness and Model & Framework) and its own render branch
(`<AgentCapabilities agentId={agentId} />` from `AgentCapabilitiesPanel.tsx`,
wrapped in a plain padded div — it is not `BookmarkPanelHost`-mounted like
Awareness/Network/Memory/Skills/MCP/Channels, because it is not a bookmark
drawer tab).

Per-agent module toggles (plugin platform batch 5c: which registered
capability modules take part in THIS agent's turns) live here rather than in
a drawer panel because they are a **settings decision** scoped to one agent,
not a live-data surface a user monitors — same category as General
(name/description) and Model & Framework, not Jobs/Inbox/Network. Putting it
in Capabilities (the atomic-tab left directory, one panel mounted at a time)
instead would have implied it is something to check on repeatedly, which it
is not: it is set once per agent and read by the turn pipeline thereafter.
