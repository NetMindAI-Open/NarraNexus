---
code_file: frontend/src/components/chat/ChatHeader.tsx
last_verified: 2026-09-22
stub: false
---

## 2026-09-22 - Browser needs an explicit desktop entry

Registering a panel does not populate this hardcoded icon cluster. Browser now
has an NM icon button beside Artifacts, with an accessible name and Radix tooltip,
opening the existing drawer through `requestPanel('browser')`. It is disabled
without an agent and subscribes to PANELS so removing the browser contribution
also removes its entry. BrowserStreamPanel remains responsible for panel content.

## 2026-09-11 — Model & framework button reinstated (OWNER-REQUIRED entry)

Owner report 2026-09-11: "I cannot find where to change the framework — it used
to be at the chat's top right". #383 (19aae3ada) had dropped the ⋯ menu's
"Model & framework" item (and before that the header sliders icon) as a
duplicate of the profile page. The Owner overruled that: the chat view must have
its own door. A `SlidersHorizontal` icon button (Radix tooltip + aria-label
`chat.header.modelFramework`) now sits in the right-hand icon cluster between
the cost popover and the ⋯ menu, calling the new optional `onOpenAgentConfig`
prop. The prop is optional on purpose: the host passes it only to the agent's
owner (the llm-config routes 403 anyone else), and no handler → no button.
It opens the SAME AgentLlmConfigPanel the profile page and the sidebar row's ⋯
menu open — **do not remove it as "redundant"**; it is an Owner-required entry.

## 2026-09-11 — 注释跟随:抽屉标题切换器恢复

`DETAIL_GROUP_A` 上方注释不再说「抽屉切换器已退役、⋯ 是唯一回路」:抽屉关着时 ⋯ 菜单是回到
被收起 studio 的桌面入口,抽屉开着时标题切换器(`visibleCategories`)也提供它。行为不变。

## 2026-09-07（批 1 三轮复审移植）— `DETAIL_GROUP_A: BuiltinTabId[]`

壳自己写的菜单分组用窄类型，拼错即编译错误；`openPanel/DetailItem` 仍接受 `AtomicTabId`（插件 tab 也能从这里打开）。

## 2026-09-07 — the plugin-actions divider is conditional (M-1)

The divider between `DETAIL_GROUP_A`'s fixed panel entries and the plugin-contributed
`ui.chatHeaderActions` entries used to render unconditionally, so every user without a
chat-header-action plugin saw a dangling separator at the bottom of the ⋯ menu. It now only
renders when `headerActions.length > 0`; `data-testid="chat-header-plugin-divider"` was added so
`chatHeaderPluginDivider.test.tsx` can assert presence/absence directly.

## 2026-09-04 (合并 dev #383 后) — `builder` 成为 ⋯ 菜单的**条件项**

#383 把抽屉标题下拉整体退役，`visibleCategories` 失去承载它的 UI；桌面端用户收起
studio 面板后原本只剩 ⌘K 能回去。现在 ⋯ 菜单第一项按 [[../bookmarks/tabs.ts]] 的
`visibleTabs({ studioOpen, studioResumable })` 判定是否列出 `builder`：与「常驻入口会提供一个
对话没在驱动的面板」那条理由自洽——只有这个 agent 走过 AI 创建路径且没按完成时才出现。
`DETAIL_GROUP_A` 取 #383 的瘦身版（awareness / social / memory 归档案页）。

## 2026-09-03 (更正同日早条) — `builder` 不进 ⋯ 菜单

早条把 `builder` 加进了 `DETAIL_GROUP_A`，已撤回。Owner 定义：**配置面板只在
「通过 AI 创建」这条路径上出现**，「从空白开始」保持现状。常驻入口会在每个
agent 上都提供一个「对话并不驱动它」的表单，读起来像坏了，而不是多了个功能。

`DETAIL_GROUP_A` / `DETAIL_GROUP_B` **是写死的 id 列表、不从
`STRIP_CATEGORIES` 派生**这条陷阱仍然成立 —— 将来要加常驻 tab 必须同步改这里。

## 2026-09-03 — `builder` 进 ⋯ 详情菜单

创建工作室的配置面板加进 `DETAIL_GROUP_A` 首位。

**这里有个陷阱**：`DETAIL_GROUP_A` / `DETAIL_GROUP_B` 是**写死的 id 列表**，
不从 `STRIP_CATEGORIES` 派生。所以在 [[tabs.ts]] 里注册一个新 tab **不够** ——
不同步改这里的话，那个 tab 只能从抽屉自身的切换器进得去，⋯ 菜单里根本看不到。
本次就是这么漏的一轮。

## 2026-08-27 — 左上角身份块 = Profile 入口;切换下拉退役,⋯ 菜单瘦身

Owner 定调:点左上角进 [[../../pages/AgentProfilePage]]。三处连带改动:

- **agent 切换下拉整个删掉**(连 `useConfigStore`/`useChatStore` 依赖、
  `switcherOpen`/`handleSwitchAgent` 一起)。头像+名字合成**一个**按钮
  → `navigate('/app/agents/<id>', { state: { from: 'chat' } })`,
  `state.from` 是 profile 面包屑区分「回对话/回目录」的唯一依据。
  代价明确记在这里:侧栏折叠时**没有**快速切 agent 的入口了,切换只剩
  [[../layout/AgentList]] 一条路——这是 Owner 拍板接受的取舍
  (2026-08-27),不是漏改。
- **⋯ 菜单从 9 项砍到 5 项**:awareness / social / memory 三个 tab 和
  「Model & framework」入口都删了,它们在 profile 的 Capabilities /
  Settings 里各有归宿,留在这儿是第二扇通往同一个房间的门。
  `DETAIL_GROUP_B`、`onOpenAgentConfig` prop 随之消失
  (面板本体没动,见 [[ChatPanel]] 同日条)。
- **`sessionLabel` prop 删除**:那条 mono「会话 · 时间」侧标随身份块改造
  一起下线,ChatPanel 里算它的 `useMemo` 也删了。

i18n:新增 `chat.header.viewProfile`(10 语言全补,`chat.header` 是
localeParity 的 COMPLETE_NAMESPACE);删除
`chat.header.sessionLabel/switchAgent/modelFramework`(10 语言)。
测试:`chatHeaderAgentSwitcher.test.tsx` 删除,换成
`chatHeaderProfileLink.test.tsx`(导航目标 / agentId 转义 / 无 agent 时
不导航);`chatHeaderTooltips.test.tsx` 补 MemoryRouter——本组件现在用
`useNavigate`,裸渲染会抛。

## 2026-08-20 — 面板入口按钮换 Radix 悬停 tooltip

Jobs/Inbox/Artifacts 三个图标入口原来只有原生 `title`(慢、无障碍名缺失)。
改为 `@/components/ui/tooltip` 的 Radix Tooltip(整簇一个 `TooltipProvider
delayDuration=200`),按钮补 `aria-label`、去掉 `title`——悬停即出样式化提示,
且图标有了可访问名(getByLabelText 可查)。⋯ 详情按钮保留原生 title+aria
(菜单开合与 tooltip 叠加有风险,不动)。测试:chatHeaderTooltips.test.tsx。

## 2026-08-19(二)— 身份组真的可收缩

切换器 wrapper 从 shrink-0 改 min-w-0(按钮同加):此前名字块最小
~246px 顶死,窄聊天列下左组会画到右组底下。现在收缩链
组→wrapper→按钮 全程 min-w-0,名字/会话标签各自 truncate。

## 2026-08-19 — Agent 切换下拉被裁剪修复

左侧身份组的 `overflow-hidden` 会把组内 absolute 下拉裁到头部条高度——
菜单打开等于什么都看不见。移除该属性,收缩改由 min-w-0 + 各子项自带
truncate 承担(sessionLabel 补 min-w-0)。教训:含 absolute 弹层的祖先
不能挂 overflow-hidden。

## 2026-08-19 — 点名字=切换 agent,设置只走 ⋯

头部 agent 名不再打开 detail 菜单(弹窗还错位地锚在最右的 ⋯ 下面):
名字现在是 **Agent 切换器**——左锚下拉列出全部 agent(头像+名字,当前项
打勾),选中走 setAgentId+setActiveAgent(与侧栏行同一条路)。设置/面板
只保留 ⋯ 一个入口。key `chat.header.switchAgent` 替换 `agentDetailTitle`
(×10,旧键删除)。测试:chatHeaderAgentSwitcher.test.tsx。

## 2026-08-19 — ⋯ 菜单换 useDismissOnOutside

手写全屏 backdrop 换 [[../../hooks/useDismissOnOutside]],与侧栏各弹层同一
实现(此处今天没有 transform 祖先所以旧写法碰巧能用,但它是最后一个手写
backdrop,留着就是下一个被照抄的样板)。顺带获得 Escape 关闭。

## 2026-08-11 — agent 名与侧栏同字族(sans),主角感靠字号/字重

Owner 对照截图指出头部 "New Agent" 与侧栏行观感不一致:此处原为
Space Grotesk 15px semibold,侧栏行是 Inter 14px medium——同名同屏双字族。
按 design_system.md §4.1(display 只用于大标题),改为 `--font-sans`
text-base(16px)semibold:字族与侧栏统一,「主角」层级由字号+字重承担。

## 2026-08-06 (2) — Artifacts 入口改开 drawer + 换 glyph

图标从 FileText 换成 ArtifactsGlyph(tabs.ts,Owner 截图指定);点击从
"切 artifactStore.collapsed" 改为 openPanel('artifacts') — 与其他条目
完全同构。徽标仍读 artifactStore.artifacts.length。

# ChatHeader — v4 聊天头部(agent 名主角)

## 为什么存在

Chat UI v4 把三层头部(品牌行 / tab 行 / 安全横幅)压成一行,并接管了
被退役的右缘 BookmarkStrip 的全部入口职责。桌面专属(hidden md:flex);
移动端由 MainLayout 顶条 + ChatPanel 里 md:hidden 的 tab 行代替。

## 结构

左:侧栏展开钮(仅 sidebarCollapsed 时)→ 身份块按钮
(RingAvatar + agent 名,整块点进 Profile 页,见 08-27 条)。
右:ExecutionPopover(流式时)→ Chat/Inner Thoughts segmented(状态在
ChatPanel)→ Jobs / Inbox / Artifacts 图标(徽标来自
deriveTabStatus / artifactStore)→ CostPopover → ⋯ detail 菜单。

## 设计决策

- **只做门,不做房间**:所有条目通过 uiStore.requestPanel 打开 ChatView
  里既有的 BookmarkDrawer 面板;面板内部零改动(Owner 2026-08-06 口头
  确认:设计稿未提到的界面细节保持不变)。
- ⋯ 菜单 = 旧 strip 的 config 类目,去掉已被 Profile 页接管的四项
  (awareness / social / memory / Model & framework,见 08-27 条),
  只剩 workspace / channels / skills / mcp / smarthome 五项。
- Artifacts 图标 `openPanel('artifacts')` 开抽屉面板(collapsed 机制已退役)。
- 徽标/markTabOpened 语义沿用 tabs.ts 注册表,不另造信号源。

## 2026-09-04 · UI slot points (batch 3d.2)

The ⋯ menu lists `chatHeaderActions` entries (when-filtered for `conversationKind:chat`, ordered) after the model/framework row; each runs with `{agentId}` and closes the menu.

## 2026-09-04 · capabilities entry (batch 5c)

The detail menu gains "Capabilities" (`onOpenCapabilities`) under "Model & framework".
