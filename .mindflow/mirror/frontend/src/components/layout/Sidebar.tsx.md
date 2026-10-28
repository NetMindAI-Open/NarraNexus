---
code_file: frontend/src/components/layout/Sidebar.tsx
last_verified: 2026-09-06
stub: false
---

## 2026-09-04 (合并 dev #383) — 两边删除取并集

本分支删了 `useCreateAgent`（「+」改为进 [[../../pages/ChooseCreateMethodPage.tsx]]），#383 删了
`useChatStore` / `usePreloadStore` / `clearPreload`（会话清理收进 `lib/sessionWipe`）。合并后
两组都不在。

stub: false
---

## 2026-09-03 — 「+」改为进创建分叉页

CreateMenu 的 `onCreateAgent` 从直接调 `createAgent()` 改为
`navigate('/app/agents/new')`。一次点击直达是**用户没意识到自己创建了 agent**
的直接原因（见 [[ChooseCreateMethodPage.tsx]] 的归因），分叉页上的「从空白
开始」调的还是同一个 `useCreateAgent()`。

连带清掉：本文件不再需要 `useCreateAgent()`，`creatingAgent` 的 `disabled`
也随之失去意义（导航是瞬时的），一并删除，不留死状态。
## 2026-08-20 — Export 行深链进 Dashboard 导出标签

「导出功能融入智能体管理」:侧栏 Export 行不再去独立的 `/app/bundle/export`,
改跳 `/app/dashboard?tab=export`(DashboardPage 首帧读 `?tab=` 落到内嵌导出向导)。
active 高亮用 `dashboardTab = new URLSearchParams(location.search).get('tab')`
(不是 `search.includes('tab=export')`,否则 `?tab=exportfoo` 会误命中):Export 行
`pathname=/app/dashboard && dashboardTab==='export'`,Dashboard 行(智能体管理)
反过来 `dashboardTab!=='export'`,避免 `?tab=export` 时两行同亮。Export 行也补了
`prefetchDashboard`(它现在也是通往 dashboard chunk 的入口)。独立
`/app/bundle/export` 路由仍在(TeamDetail 快捷导出复用)。测试:sidebarExportEntry.test.tsx。


## 2026-08-19 — 账户入口指向设置内 pane

弹层 Account 项改 navigate('/app/settings?tab=account')——同一目的地,
少一跳别名重定向。

## 2026-08-19 — 账户弹层只剩身份;工作台升一级导航

- 弹层瘦身为:用户名+在线/模式/版本头、Account(NetMind 用户,去
  /app/account)、移动端反馈入口、Logout。主题/语言迁去 Settings →
  Personalization([[PersonalizationSettings]]),工作台入口迁出。
  langOpen 状态、语言列表、主题切换项及其 import 一并删除。
- 全局导航在 Marketplace 与 Settings 之间新增「你的工作台」行(/app/you,
  BookOpen 图标)——它是常用目的地,不该藏在身份弹层里。

## 2026-08-19 — 账户弹层点击页面任意处可关闭

账户弹层的全屏 backdrop 换 [[useDismissOnOutside]](挂 Zone-3 容器,dismiss
同时收起语言子菜单)。与行菜单/CreateMenu 同批统一:侧栏内所有自绘弹层的
关闭语义现在都是「点页面任意处/Escape」。

## 2026-08-19 — squash 残留清理

两条指向 `@/components/ui` 的 import 合并为一条(squash 自动合并残留,无行为
变化)。

## 2026-08-06 (3) — 账户弹层三项指向 /app/account

Account / Billing / Subscription 从 navigate('/app/settings?tab=account')
改为 navigate('/app/account')(Settings 的 account 面板已迁出,见
[[../../pages/AccountPage.tsx]])。仍以 netmindToken 存在为显示条件。

## 2026-08-06 (2) — Create team 入口改跳页

New 菜单的 Create team 从打开 TeamManagementModal 改为 navigate
('/app/teams/new')(新 [[../../pages/CreateTeamPage.tsx]]);modal 挂载
从 Sidebar 移除(Dashboard Teams 视图仍在用它管理既有团队)。

## 2026-08-06 — Chat UI v4:三层结构重写

按 v4 设计稿(specs/2026-08-06-chat-ui-v4-design.dc.html)整体重排:
① logo + 收起按钮(panel 图标);② 全局导航列 — New(下拉:Create agent /
Create team / Import .nxbundle / Import from other source,见 [[CreateMenu.tsx]])
/ Export / Dashboard / Marketplace / Settings / System(gated),之后才是
Chats 列表;③ 底部用户行 → **账户弹层**(工作台 / Account / Billing /
Subscription(netmindToken 才显示)/ Theme / Language(内联展开列表)/
Logout)+ Find Us 外链(从 TopBar 迁入,保持 target=_blank + noopener)。

关键不变量(沿袭):logout / 切模式仍是 wipeAllSessionData() + **硬跳转**
window.location.href;SHOW_MODE_SWITCHER 逻辑保留在弹层里未删。
收起态改为 **整个 aside 隐藏**(uiStore.sidebarCollapsed,md:hidden),
72px 图标栏退役;展开按钮在聊天头部 / MainLayout 浮动 chip 上,不在本组件。
TeamManagementModal / ImportAgentModal 的挂载点从 AgentList 移到这里。
宽度 288→272px。ThemeToggle / LanguageToggle 组件不再被本文件引用
(账户弹层内联实现),组件本身保留给其他调用方。

## 2026-08-12 — 悬停预取 DashboardPage 补 `.catch`（复审二轮 🟢）

`prefetchDashboard` 的 `import('@/pages/DashboardPage')` 加 `.catch(() => {})`——删掉全局 `vite:preloadError` 监听后没人 `preventDefault`,后台预取失败本应静默(真导航会重试、[[ChunkErrorBoundary.tsx]] 兜到达 render 的),把「故意忽略」写进代码而非留成 unhandled rejection。纯加固,无行为变化。

## 2026-07-28 — Beta 徽章挂到 logo 旁

展开态 logo 旁新增 [[BetaBadge.tsx]](wrapper `gap-0`→`gap-2`),向用户声明
产品处于 Beta 阶段;悬停出多语言预期管理说明。收起态 logo 本就隐藏,徽章
随之不渲染。

## 2026-07-21 — Marketplace 一级入口

Dashboard 与 Settings 之间新增「Marketplace」导航(Store icon,展开/收起
两种形态都有),路由 `/app/marketplace` → pages/MarketplacePage。


## 2026-07-10 (4) — footer "clear history" button removed

The footer 🗑️ "clear history" button (active-agent scope) is gone, along with
`handleClearHistory` and the now-unused `api` / `Trash2` / `agentId` refs.
Clearing moved to the per-agent ⋮ menu as a scoped multi-select wipe
([[AgentRowMenu.tsx]] → [[ClearAgentDataDialog.tsx]]); the old button was also
ineffective (DB-only, never cleared the on-disk narratives/trajectories).

## 2026-07-10 (3) — footer 反馈入口改为移动端专属

桌面端入口已是右下角浮动 [[FeedbackButton.tsx]]；footer 这个入口用
`isMobile` 门控保留,因为移动端右下角归 composer。每个视口恰好一个入口。


## 2026-07-10 (2) — 反馈入口移出 footer

Owner 反馈 footer 位置不好；入口移到右下角浮动按钮
[[FeedbackButton.tsx]]（问号正上方），Sidebar 恢复到无反馈入口状态。


## 2026-07-10 — footer 反馈入口

footer 行（ThemeToggle/LanguageToggle 旁）新增 MessageSquarePlus 图标按钮，
打开 [[FeedbackDialog.tsx]]。入口刻意放常驻 footer——显式反馈是 Agent 自动
submit_feedback 的兜底通道，必须随时可达。


## 2026-07-03 — cloud/local mode switcher hidden

Both sidebar entry points to the mode switcher (expanded button+popup and the
collapsed icon button) are gated behind a module-local `SHOW_MODE_SWITCHER =
false` — users should not choose the deployment mode. All switching logic
(handleSwitchMode, mode state, /mode-select navigation, the data-wipe on
switch) is kept intact and still referenced (so lint stays clean); only the UI
is hidden. Flip the flag to true to restore the control.

> 2026-06-24: Sidebar is now the shell for the TEAMS/AGENTS restructure. It no
> longer owns any team grouping itself — it just renders `<AgentList collapsed/>`
> inside the ScrollArea, and AgentList is what splits the roster into a TEAMS
> section (each row a team group-chat) above a flat AGENTS section. The old
> `TeamFilterBar` chip-filter approach is gone for good.

## 2026-06-24 — user header avatar → sm + "YOU ›" affordance

The expanded user-header `RingAvatar` is `size="sm"` (32px) to match the agent
rows + team avatars (uniform avatar size across the sidebar). A right-aligned
**"YOU ›"** cue (mono label + `ChevronRight`) marks the row as clickable → the
"You" workspace; faint at rest, carbon on hover/active. It's a visual cue inside
the row-button, not a nested button.

## 2026-06-23 — user avatar opens the "You" workspace

The carbon user-info block (avatar + name + Online) is a `<button>` navigating to
`/app/you` ([[YouWorkspace]]) — the owner-scoped Memory / Network / World + Notes
page, the carbon counterpart to selecting a silicon agent. Both expanded and
collapsed variants carry the click + active highlight when on `/app/you`.

## 2026-06-11 — show NetMind nickname, not the opaque userSystemCode

user_id is a 32-hex NetMind userSystemCode in cloud mode. The user block shows
`displayName || userId` (configStore.displayName = NetMind nickName), falling
back to user_id in local mode where it IS the chosen username.

# Sidebar.tsx — Collapsible left rail: branding, user, the team/agent roster, nav, mode-switch

## Why it exists

Single place that owns the persistent left-rail shell: branding, the carbon
user-header (→ "You" workspace), the silicon roster ([[AgentList]]), the nav
actions (Dashboard / Settings / System), the local↔cloud mode-switcher, and the
destructive logout / clear-history actions. Collapsible to a 72px icon-only rail;
on mobile it becomes an off-canvas drawer toggled from the TopBar.

## How it works / design

- **It is a shell, not a list owner.** With the team group-chat redesign the
  sidebar delegates the whole roster to [[AgentList]], which renders the TEAMS
  section (group-chat rows) over the flat AGENTS section. Sidebar's own concerns
  are chrome only: logo, user header, nav, footer. The retired `TeamFilterBar` /
  `TeamFilterAndAgents` chip-filter is intentionally not coming back — grouping
  lives in [[agentGroupUtils]] / [[AgentList]].
- **Hard reload on mode-switch & logout.** Both call `wipeAllSessionData()`
  (`logout()` + `clearChat()` + `clearPreload()` to reset Zustand, then direct
  `localStorage.removeItem()` of every known persisted key) and then
  `window.location.href = '/…'` — a full document reload, NOT React Router
  `navigate()`. A soft navigate keeps the React tree, closure-captured store
  snapshots and module caches from the prior mode alive, which is exactly how
  cloud data bled into a subsequent local session. The direct removeItem calls
  are the authoritative clear; we don't trust persist to flush before reload.
- **Upstream/downstream**: rendered by [[MainLayout]]; depends on [[AgentList]],
  `useConfigStore` / `useChatStore` / `useRuntimeStore` / `usePreloadStore` /
  `useUIStore`, and `api.clearHistory`. Dashboard nav prefetches the lazy
  `DashboardPage` chunk on hover/focus (static literal → Vite-resolved, no
  injection risk) to pair with MainLayout's inner Suspense.
- **Gotchas**: the System link is feature-flagged behind
  `features.showSystemPage` (runtimeStore). The mode-switch popup is a raw
  positioned `div`, not a Popover — it doesn't close on outside-click; you toggle
  it by clicking the button again.

## 2026-08-27 — session wipe extracted

`wipeAllSessionData` moved to [[sessionWipe]]: the first-run flow's rail also
offers "log out", and a second hand-rolled half-logout is how cloud data bled
into a later local session before. The sidebar still owns the confirm dialog and
the `window.location.href` reload — only the wipe itself is shared.

Merged with the plugin platform (2026-09-06): pages, drawer panels, sidebar items, commands and agent-row badges come from the frontend registries (`platform/registries`, registered in `platform/builtin.ts`); this file keeps dev's behaviour on top of that.
