---
code_file: frontend/src/pages/DashboardPage.tsx
last_verified: 2026-09-10
stub: false
---

## 2026-09-09 — left rail collapses to a horizontal strip below md (GitHub #130)

Same fix as SettingsPage.tsx (this rail already mirrors that nav's
shape): the fixed `w-56` column had no breakpoint and ate most of a
360px viewport. Below `md` the wrapper switches to `flex-col` and the
`<nav>` becomes a horizontal `overflow-x-auto` strip (items `shrink-0`)
instead of a fixed-width sidebar; `md`+ layout is unchanged.
The wrapper, `<nav>` and item class strings come from the shared
`lib/masterDetailNav` definition (same import as SettingsPage), not a
local copy.

## 2026-09-04 — framework 标签/图标改走 `lib/frameworkBrand`

本地 `FRAMEWORK_BRAND_ICONS` 与 `formatFramework` 删除（评审二轮 I2）；模型图标的暗色反色改问
`iconInvertsInDark`，不再 import `OpenAIBrandIcon`（三轮 M4）。

## 2026-09-03 (评审修订) — 行不再是 `role="link"`；渠道图标区分开关

- **M10**：外层 div 去掉 `role/tabIndex/onKeyDown`，只保留鼠标 onClick；进档案页的**可访问**
  入口是 agent 名称那个真按钮。链接里嵌复选框和按钮对辅助技术是语义混乱。
- **M2**：`bound_channels` 现在是 `{channel, active}`；关着的渠道图标降透明度 + 去饱和，
  tooltip 用 `pages.dashboard.channelOff`（10 locale）。
- `formatFramework` 认 `nexus_power` → "Nexus Power"。model-overview 那条死通道
  （AgentModelCard / AgentModelChip / getAgentsModelOverview / AgentModelOverview）随
  评审 I6 整体删除。

## 2026-08-26 — 就地看/改单 agent 模型

展开行在运行状态之后追加 [[AgentModelCard]]（懒加载 per-agent llm-config），其
「编辑」把 `llmCfgAgentId` 置为该 agent，页面末尾据此挂载共享
[[AgentLlmConfigPanel]]（列表场景用 `string|null` 存「当前编辑哪个 agent」，
不是单个 bool——多行各自可开）。保存后 `modelReloadKey++` 触发卡片重拉。

折叠行名字列下挂 [[AgentModelChip]]，数据来自页面级单次
`api.getAgentsModelOverview()`（`modelOverview` state，随 `modelReloadKey` 刷新）
——用一次 HTTP 调用替代 per-agent 的 llm-config 请求（后端 DB 层仍每 agent
一次查询，见 [[slot_service]]）。effect 依赖 `modelReloadKey` + 一个对
roster agent id 集合的**值 key**（`rosterIdsKey`），这样新建/删除 agent 会触发
一次重拉给新 agent 上 chip，但不会随轮询 tick 变成请求风暴（别把 agents 数组
本身放进依赖）。

## 2026-08-27 (3) — 行内展开详情整块移除,整行变成 Profile 链接

Owner 说的「下拉」指的是**行展开出来的那块运行详情**(不是任何 `<select>`)。
现在整块删掉,行回到 chat-ui-v4 分支的形态:**整行就是一个链接**
(`role="link"` + `tabIndex` + Enter/Space 键盘路径 → `openProfile()`),没有第二种
手势,所以上一节那个「身份块 vs 行」的 `stopPropagation` 分工也随之取消——身份块
退回普通 `<span>`。勾选格、Teams/Channels 格、Chat 按钮仍各自 `stopPropagation`。

连带清掉:`expanded` Set、`toggleExpand`、`ChevronRight`、`isOpen`/`owned` 两个
行内局部量,以及只有这块用到的七个 import(`AttentionBanners` / `SessionSection` /
`JobsSection` / `QueueBar` / `Sparkline` / `RecentFeed` / `MetricsRow`)。

**这七个组件当时全仓库没有调用方了**(`expandState.ts` 和 `senderIdentity.ts` 里
只是注释提到名字,不是 import)。刻意没删——它们表达的信息在 [[AgentProfilePage]]
上没有对应位置,是把那部分信息搬进 Profile 时的现成材料。

> **同日已落地(见 [[AgentProfilePage]] 2026-08-27 (3))**:`AttentionBanners` 置顶,
> `Sparkline` / `MetricsRow` / `SessionSection` / `RecentFeed` 收进新的
> [[../components/agents/AgentActivityCard.tsx]]。**仍然没有调用方的只剩
> `QueueBar` 和 `JobsSection`**:前者的位置被 [[../components/jobs/JobStatusMeter.tsx]]
> 占了(同款比例条 + 计数图例,塞第二个就是重复),后者的内容 JobsPanel 本来就覆盖。
> **这两个已于同日删除**(Owner 拍板),连同 `dashboard.jobState` /
> `dashboard.queue` / `dashboard.jobs` 三棵已无调用方的 i18n 子树。
> `lib/api.ts` 的 `retryJob` / `getJobDetail` **保留**——它们对应的后端端点仍然
> 存在(见 [[../../../../backend/routes/dashboard/routes.py]] 的端点清单),
> 客户端缺一个方法比留一个未调用的方法更糟。

`pages.dashboard.noStatusYet` 同样失去调用方,留在 locales 里没删。

## 2026-08-27 (2) — 两张表统一成无边框目录

**表格 chrome 换成 chat-ui-v4 那套无边框目录**:外层不再是
`rounded + border + overflow-hidden` 的卡片,表头不再是 `font-mono` + 下边框,行
之间不再有 hairline 分隔线。改成表头 `font-medium` 小字压在 `--nm-paper` 上、行
`px-4 py-3` 靠 `hover:bg-[var(--nm-row-hover)]` 区分,整表 `font-sans`(agent_id
子行也不再 `font-mono`)。展开详情的左缩进从 `pl-[60px]` 跟到 `pl-[68px]`,对齐新的
`px-4` 行内距。

**团队表也一起改了**,虽然来源 commit 里它仍是带边框的老样式——那更像是没顾上,
不是决定。同一页两张表用两套表格语言在设计系统层面说不通(见
`design_system.md` §2.5 surface ladder)。要严格照搬来源分支的话,把团队表那两处
类名改回 `rounded/border/font-mono/border-b` 即可。

## 2026-08-27 — 两张表都换成「身份 + 运行时」目录(从 chat-ui-v4 择要迁入)

来源是 `feat/chat-ui-v4-dev-merge` 的 `40d353e1`。那个 commit 把整页重写成了
无左栏的 directory,本次**只取两张表的信息密度,不取它的 IA**:左栏三标签、KPI 四格、
团队筛选、勾选列、批量操作条、行内展开详情**全部保留**(Owner 2026-08-27 拍板)。
来源那边把批量管理挪去了别处,直接照搬等于删掉本页当前唯一的批量入口。

**智能体表 10 列**:勾选 / 身份(头像+名称+ID,公开且非己有时带 Globe) / 状态 /
Teams / Channels / Framework / Model / Last active / 来源 / Chat。

- Teams 列从彩色文字 chip 换成 [[../components/agents/AgentTeamAvatars.tsx]] 的
  重叠头像 + hover 简介卡。**代价:团队颜色不再出现在本页**——`GroupAvatar` 不接
  color。颜色仍可在 `TeamManagementModal` 里看到和改。这是取舍不是遗漏。
- Channels / Framework / Model 三列的数据来自 `AgentInfo` 的三个新字段,由
  [[../../../../backend/routes/auth.py]] 批量投影。**没有后端那两段富化,这三列
  永远是 `—`**。品牌图标见 [[../components/icons/ChannelBrandIcons.tsx]] /
  [[../components/icons/ModelBrandIcons.tsx]];OpenAI 的黑标在暗色下要 `dark:invert`。
- 容器 `max-w` 从 960 放到 **1180**:10 列挤在 960 里第一个被截断的是 model id,
  而那恰好是用户扫这张表时要看的东西。

**团队表 6 列**:Team(`GroupAvatar` + 行内改名) / Leader / Members / Created by /
来源 / 操作。操作列是 Chat + **保留的「管理」按钮** + `TeamRowMenu` 三者并存——
来源 commit 用 `TeamRowMenu` 顶掉了「管理」,但本仓库里 `TeamManagementModal` 是
改成员/改颜色的唯一入口,顶掉就没了。
`Created by` 取 `tm.team.owner_user_id` 而不是当前用户:来源 commit 硬编码了
`displayName || userId`,今天 teams 列表确实只含自己的团队所以看不出问题,但共享
团队一出现它就会说谎。

`AGENT_GRID` / `TEAM_GRID` 是模块级常量,表头、数据行、只读公开行共用同一份轨道
定义——10 列的 track list 抄三遍正是表格悄悄错位的经典成因。

**公开 agent 行的 Teams/Channels/Framework/Model 一律留空**,不是没做:那是别人的
私有配置,后端根本不会投影过来。

## 2026-08-27 — 身份块变成 Profile 的门,行展开原样保留

> **已被同日 (3) 推翻**:行展开整块删掉了,现在整行就是链接。下面这节保留是为了
> 记录中间态和当时的理由——尤其是「展开里的 sparkline / recent events 在 profile
> 页没有对应位置」这条判断,它今天依然成立,只是 Owner 选择接受这个信息缺口。

智能体行现在有两个手势,**刻意分工**:

- 点头像+名字这一块 → `navigate('/app/agents/<id>',
  { state: { from: 'dashboard' } })`,进 [[AgentProfilePage]];
- 点行内其它任何位置 → 仍旧是 `toggleExpand`,展开那块行内详情
  (verb_line / QueueBar / sessions / jobs / sparkline / recent events)。

两者靠身份块 `onClick` 里的 `stopPropagation` 分开。**没有**照搬
chat-ui-v4 分支「整行即链接、行展开取消」的做法:那块展开详情里的
sparkline 和 recent events 在 profile 页没有对应位置,直接换掉是净丢
信息(Owner 2026-08-27 拍板保留展开)。

无障碍:身份块是真 `<button>`,带 `pages.dashboard.openProfile`
的 title/aria-label(10 语言全补),因此键盘可达,不需要给外层
`div` 硬套 `role="link"`。

## 2026-08-20 — 顶部 Agents/Teams 切换 → 左侧三标签(和设置一致)

Owner 要求「智能体管理」(=本页,zh title 就叫智能体管理)的视图切换改成
**和 SettingsPage 一样的左侧竖排标签栏**,并拆出第三个「导出」标签:
- `view` 类型扩到 `'agents' | 'teams' | 'export'`。**`?tab=` 是唯一真相源,
  `view` 直接 DERIVE(`parseTab(searchParams.get('tab'))`),没有 useState 副本**
  ——本页是单一 `<Route path="dashboard">`,侧栏 Export 行只是
  `navigate('/app/dashboard?tab=export')` 不会重挂载,派生态天然覆盖「已在
  Dashboard 时深链」。合法 id 只有 `TAB_ITEMS`(模块级常量)一份,不再散成四份。
  `selectTab` 用 `new URLSearchParams(searchParams)` **增量**写回(agents 删 tab,
  teams/export set tab),保留其它参数、URL/侧栏高亮始终一致。
- 外层从居中 `ScrollArea>max-w-960` 改成 `h-full flex flex-col`:标题 header +
  `flex flex-1 min-h-0`(左 `w-56 border-r` nav 三项 + 右 detail)。agents/teams
  两个 pane 仍走 padded ScrollArea+max-w-960;**export pane 内嵌
  `<Suspense fallback={居中 spinner}><BundleExportPage embedded /></Suspense>`**
  ——BundleExportPage 保持 `lazy()` 独立 chunk(与 App.tsx 路由级分包一致),否则
  进 dashboard 就连带下载整个导出向导。fallback 用中性 spinner 不用
  DashboardSkeleton(后者是 dashboard 网格形状,进导出会闪成 layout shift)。它
  自带 h-full 滚动/页脚,故渲染在 padded ScrollArea 之外。`lazy`/`TAB_ITEMS`/
  `parseTab` 等模块级声明放最后一条 import 之后(不夹在 import 里)。
- agents pane 顶部加「创建智能体」按钮(复用 [[../../hooks/useCreateAgent]]);
  teams pane 加「创建团队」按钮(→ `/app/teams/new` CreateTeamPage)。summary
  BracketSectionLabel 从旧 header 移进各 pane。
- 轮询 effect 在 export tab 早退,**依赖是布尔量 `exportOpen = view==='export'`
  而非三值 `view`**:agents↔teams 切换 `exportOpen` 恒 false、依赖不变、轮询循环
  不受打扰(否则每切一次就重启并立刻多打一次 `/dashboard/status`,能自伤成 429);
  只有跨 export 边界才真正停/起。轮询节奏仍归 dashboardStore 所有。
- 待办:三个 pane 仍在同一函数里(~900 行),抽 AgentsPane/TeamsPane 是独立重构,
  记于 `reference/self_notebook/todo/2026-08-20-extract-dashboard-panes.md`。
测试:dashboardTabs.test.tsx、dashboardDeepLink.test.tsx(已挂载深链 + 点 tab 写 URL)。


## 2026-08-19 — review 轮:筛选统一、shift 区间防崩、批量结果如实上报、Manage 带行上下文

- 文本筛选抽成 `matchesFilterText`,public 行同样过它;任何团队筛选下 public 行
  直接不渲染(public agent 没有 roster 团队归属)。空态判断因此自然变成
  「过滤后两段皆空」。
- `filterText`/`filterTeam` 变化即重置 `lastClickedIdx`;shift 区间循环内再加
  一道 `if (!row) continue`——修「筛选变短后 shift-点越界 TypeError 白屏」。
  前者修根因,后者防御索引来源以后再变,两道都要。
- `handleBulkAddToTeam`/`RemoveFromTeam` 改成 `handleBulkDelete` 的形状:
  success/failed 统计 + 失败分支文案(`danger`),remove 路径补结果提示并清空
  选择。新 i18n key `addedToTeamResultWithFailures` / `removedFromTeam*`(×10)。
- Teams 视图行级 Manage 传 `tm.team.team_id`,经 [[TeamManagementModal]] 新的
  `initialTeamId` prop 在 open 上升沿选中——修「每一行都打开第一个团队」的
  误操作入口。
- `TableCheckbox` 加 `onToggle`+`tabIndex`+Space/Enter+`ariaLabel`(点击仍归
  父级 label/cell 保留大热区,焦点/键盘/无障碍名归 checkbox 本体;行级用
  agent 名,表头用全选/反选文案);首个 effect 的两个悬空 promise 显式 `.catch`。
  键盘 toggle 不设 shift 锚点(区间选择是鼠标语义,有意为之)。

## 2026-08-06 — Chat UI v4:吸收 ManageAgentsPage,监控 + 管理合一

页面重写为 v4 形态:标题 + Agents/Teams segmented 切换 + 4 个 KPITile
(Running / Queued / Errors / Cost today,仅统计 owned agents — public 无
metrics,规避 NaN)+ 搜索/团队过滤条 + 批量操作条(全选 / 选团队 /
加入 / 移出 / 删除,shift-click 范围选择保留)+ **状态×管理合一表**:
checkbox | chevron+头像+name/id | 状态点 | 团队 chips | 来源。行可展开
(**Set 多展开**,替代旧单 expandedId),展开内容复用 AttentionBanners /
QueueBar / MetricsRow / SessionSection / JobsSection / Sparkline /
RecentFeed。数据 = configStore.agents(roster)⨝ dashboardStore.agents
(status)按 agent_id;status 缺席显示 "—" 行。public agents(状态流里
非 roster)渲染只读行,保住旧 PublicCard 的可见性。

不变量全部沿袭:轮询 FSM 节奏归 dashboardStore.computeInterval;清理
必须 active=false + clearTimeout;429 → onRateLimited;tray badge 仅在
计数变化时 setTrayBadge;listenTauri 空返回用 ?.() 解绑。

Teams 视图:团队表(色点/名称/成员数/来源)+ Manage 按钮开既有
TeamManagementModal(内部不动)。AgentCard / DashboardSummary /
DurationDisplay / ConcurrencyBadge / dashboard 版 StatusBadge 删除;
ManageAgentsPage 删除(路由 / TopBar crumb / Settings nav 项一并清理)。
批量删除仍是逐行循环、部分成功弹窗 — 无事务语义,故意的。

> 2026-06-24: Renamed from the mis-named `DashboardPage.md` to the canonical
> `DashboardPage.tsx.md` and rewritten in English to house format. Behavior is
> unchanged — still the polling FSM dashboard mounted at `/app/dashboard`.

# DashboardPage.tsx — Agent Dashboard v2: a self-throttling polling status board

## Why it exists

The cross-agent operations view (`/app/dashboard`): a card grid showing every
agent's run health at a glance, separate from any single agent's chat. It is the
one screen that has to keep itself fresh without a websocket, so its real job is
to poll the backend status endpoint at a rate that adapts to whether the user is
actually looking — cheap when hidden/idle, responsive when focused.

## How it works / design

- **Polling FSM lives in [[dashboardStore]], not here.** The page is a thin view:
  it feeds the store the FSM inputs (`visibility` from the `visibilitychange`
  event, `tauriFocused` from Tauri `tauri://blur` / `tauri://focus`) and runs a
  self-rescheduling `tick()` whose next delay is `store.computeInterval()`. The
  store decides cadence from `visibility × tauri-focus × any_running`; an interval
  of `Infinity` parks the loop entirely (e.g. tab hidden, nothing running).
- **Tray badge is a side effect of polling.** After each successful fetch the
  page computes the running count and calls `setTrayBadge(running)` only when it
  changed (Tauri desktop; web mode is a no-op). This keeps the dock/tray count
  live without a separate loop.
- **429 is handled as backpressure, not an error.** A 429 routes to
  `store.onRateLimited()` (exponential backoff via `computeInterval`) instead of
  the red error banner; other failures go to `onFetchError`.
- **Upstream/downstream**: subscribes to [[dashboardStore]]; renders
  [[DashboardSummary]] (health legend/counts) over a grid of [[AgentCard]] (each
  card owns its own expand/collapse). Data via `api.getDashboardStatus`; tray via
  `lib/tauri`.
- **Gotchas**: the cleanup must set `active=false` AND `clearTimeout` or a stale
  `tick` keeps firing after unmount. `listenTauri` returns null off-desktop, so
  unlisten with `unlistenFn?.()`. One page-level `expandedId` means a single card
  expands at a time (a `Set` would be needed for multi-expand). `DashboardSummary`
  counts are aggregated frontend-side — public agents are forced to
  `healthy_idle` since they carry no `health`; if the backend ever adds health to
  public agents, fix the aggregation too.
