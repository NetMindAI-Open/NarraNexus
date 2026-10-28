---
code_file: frontend/src/pages/SettingsPage.tsx
last_verified: 2026-09-10
stub: false
---

## 2026-09-09 — left nav collapses to a horizontal strip below md (GitHub #130)

The master–detail `<nav>` was a fixed `w-56` (224px) column with no
breakpoint, eating most of a 360px viewport and leaving the detail pane
unusably narrow. Below `md` it now becomes a `flex` row (`overflow-x-auto`,
items `shrink-0`) stacked above the content (`flex-col md:flex-row` on the
wrapper) instead of a fixed-width sidebar; at `md`+ it's pixel-identical to
before (`md:block md:w-56 md:space-y-1 md:border-r`). DashboardPage's
left rail is the same component shape and got the identical treatment
in the same change. The wrapper, `<nav>` and item class strings are not
written here: both pages import them from `lib/masterDetailNav`
(`MASTER_DETAIL_ROW_CLASS`, `MASTER_NAV_CLASS`, `masterNavItemClass`), so
the two rails cannot drift.

## 2026-09-03 — 面板与导航来自 `SETTINGS_SECTIONS` 注册表

分区组件拆到 `settings/sections.tsx`，由 `settings/registerBuiltinSections.ts` 在本 chunk 注册；
`sortedSettingsSections(opts, useRegistryEntries(SETTINGS_SECTIONS))` 承担 desktopOnly/cloudHidden 过滤
并订阅晚注册；`?tab=` 首帧读取与 `neverDefault`
回退逻辑不变。既有 nav/deep-link 测试原样通过。

## 2026-08-28 — 新增 `plugins` nav 项（[[PluginsSettings]]）

放在 `modeldefaults` 和 `artifacts` 之间——插件是模型配置的前置条件（框架选
不了就是因为插件没装），紧挨着放阅读顺序更顺。`ModelDefaultsSettings` 新增
`onManagePlugins` 跳转回调（选中未装插件框架时弹窗里的按钮）——**云端传
`undefined`** 让 ModelDefaults 走纯文本降级分支（见下方 08-31 补段的护栏演进）。`PluginsSettings` 自己在
`cloud_managed` 时返回 `null`——云端央管这些插件，本地安装按钮在云端没有意义
（装端点也会 403），所以这里不用像别的 pane 那样加"云端不可用"的占位文案，
面板本身就是空的。

## 2026-08-19(三)— neverDefault 取代内联 id 比较

「account 不做默认落地页」从 `it.id !== 'account'` 字符串比较改为
NavItem 的声明式 `neverDefault` 标记——规则住在数据上,读 NAV_ITEMS 即见。

## 2026-08-19(二)— Account pane 全会话可见 + powerOnly 机制退役

- 非 NetMind 会话点 Account 看到登录提示(pages.account.powerOnlyHint 复活),
  而不是被过滤后静默落到 Providers——?tab=account 的深链对任何会话都有着陆点。
- nav 的 `powerOnly` 字段随之无消费者,整个机制删除(铁律 #2)。

## 2026-08-19 — Account 变**页内 pane**,左侧标签栏永不消失

href 导航项撤销:点 Account 曾整页跳去 /app/account,左侧标签栏消失、
无法切换其他页——现在它是普通 pane(内嵌 [[NetmindAccountPanel]]),
nav 常驻。?tab=account 直接落本 pane(Stripe 回跳带查询参数原样工作),
原 Navigate 重定向删除。[[AccountPage]] 退化为旧链接别名(反向 302 进来)。

## 2026-08-19 — 设置成为唯一配置前门:个性化栏目 + 账户入口 + 内容居中

- 新「Personalization」pane([[PersonalizationSettings]]):主题三档 + 语言
  列表,从侧栏账户弹层整体迁入——两个「设置」并存让用户分不清差别,现在
  可配置的东西只此一处。排序放 privacy 之后,默认落点仍是 Providers。
- NavItem 支持 `href`:「Account & billing」对 NetMind 用户显示,点击
  `navigate('/app/account')` 而非切 pane(账户页仍是 user-scoped 独立页,
  设置只做入口)。`?tab=` 初始化忽略 href 项。
- 内容列 `max-w-3xl` 加 `mx-auto` 居中([[AccountPage]] 同改)。

## 2026-08-18 — "管理 Agent" 入口移除(与智能体管理页重合)

merge 时从 dev 移植的 ManageAgentsContent(一个跳转按钮)被 Owner 裁掉:
v4 的 Dashboard 已吸收批量管理,侧栏一级入口(中文现名"智能体管理")直达,
设置里再放个跳转门是冗余。NAV_ITEMS 'agents' 项、组件、Users/useNavigate
引入一并删除。locale 里 settings.manageAgents / nav.manageAgents 两处
key 刻意**留存未删**——同名 key 在别的命名空间(agentList 菜单、旧
pages.manageAgents)有活引用,机械清理误伤过一次;死 key 无害,留待
下次 i18n 大扫除时人工核对。

## 2026-08-06 (2) — 纯 app 设置:account/bundle 项移除

Owner 指示:Settings 只留 app 级配置(providers / modeldefaults /
artifacts / updates)。`bundle` 项删除(入口在侧栏 New 菜单 + Export 行);
`account` 项(连同 powerOnly 机制)移到用户级 [[AccountPage.tsx]]
(/app/account,侧栏账户弹层进入)。**Stripe 回跳契约保住了**:
?tab=account 深链在首渲染即 <Navigate replace> 到 /app/account 并整串
保留 query(status=…),后端 billing.py::_return_urls 无需改。
其余 ?tab= 规则(首渲染独占、未知回退第一项、懒挂载)不变;
SettingsPage.nav.test.tsx 已按新行为重写(重定向断言取代 powerOnly 断言)。

## 2026-08-06 — manage-agents nav 项移除

Chat UI v4 把 agent 批量管理并入 Dashboard(见 [[DashboardPage.tsx]]),
`agents` nav 项与 ManageAgentsContent 删除。?tab= 深链、懒挂载、
desktopOnly/powerOnly 过滤规则不变。

## 2026-08-11 — Privacy 导航项(隐私面板首次可达)

新增 `privacy` nav 项 + `PrivacySettings` 面板。后端的 analytics
opt-out 自 06-08 就存在,但唯一 UI 活在**从未挂载**的 SettingsModal
里——本条目让它(以及新的遥测同意开关)第一次对用户可达。
`?tab=privacy` 深链是 TelemetryNotice 首次告知横幅的跳转目标,nav
测试守住这条(横幅承诺"可去设置关闭",深链失效即承诺失效)。

## 2026-07-30 — `?tab=<nav id>` 深链（付款回跳的落点）

`active` 的初值改为读 `useSearchParams().get('tab')`。存在的理由不是"顺手支持
深链"，而是 Stripe 付款完成后会把用户送到
`/app/settings?tab=account&status=…`（见 [[billing]] 的 `_return_urls`）——
没有这一步，付款者会落在恰好排第一的面板上，并把它读成"我的钱付到哪去了"。

三条判断：

- **只有首屏渲染认这个参数**（`useState` 惰性初值，不是 effect 同步）。之后
  选择权归用户的点击，一个陈旧的 query 不该跟用户抢面板。
- **未知 id、或本会话看不到的 id（powerOnly/desktopOnly 被过滤掉），回落到第一个
  可见项**，而不是渲染一个空内容区 —— 非 Power 会话拿着 `tab=account` 进来正是
  这种情况。
- URL 里用的是**导航项自己的 id**（`account`），不是工单里随手写的 `billing`；
  少一张会腐烂的别名映射表。

## 2026-07-21 — settings shell follows the active locale

The page title, master navigation, and every section header/action owned by
this shell now resolve through `pages.settings` locale keys. Navigation items
store translation keys rather than display strings, so visibility filtering
remains language-neutral. Child panels retain responsibility for their own
copy.

## 2026-07-13 — Account nav gate: cloudOnly → powerOnly (per-user)

The "Account & Subscription" nav item's `cloudOnly`/`mode==='cloud-web'` gate
became `powerOnly`/`hasPower = !!configStore.netmindToken`, so it shows for a
NetMind (Power) account on a local dual-mode install and hides for a pure-local
username user. `useRuntimeStore` import replaced by `useConfigStore`. Mirrors the
same per-user signal [[NetmindAccountPanel.tsx]] self-gates on.

## 2026-07-10 (latest) — Account section is now a SINGLE card

`QuotaPanel` was deleted; its free-tier view + prefer_system toggle were
absorbed INTO [[NetmindAccountPanel]] (plan × runway redesign). The `account`
section now mounts only `<NetmindAccountPanel/>` — one card owns every
"what are my credits / how is usage paid" concern as one runway story. Removed
the QuotaPanel import and its `mb-4` wrapper.

## 2026-07-09 — new "Model Defaults" nav section

Added a `NAV_ITEMS` entry `{ id: 'modeldefaults', label: 'Model Defaults' }`
(after `providers`) rendering [[ModelDefaultsSettings]] — the global default
agent/helper model + framework, extracted out of LLM Providers. LLM Providers is
now purely the credential wallet ([[ProviderSettings]] card grid). Panel is
gated by `{active === 'modeldefaults'}` like the others.

## 2026-07-09 — LLM Providers: ProviderSettings owns the whole flow

``ProvidersSection`` is now just the "LLM Providers" ``SectionHeader`` +
``<ProviderSettings/>``. Everything else moved INTO [[ProviderSettings]], which
renders the ordered flow ① your providers (list) → ② add a provider (one-key +
CLI sign-in + custom + sync) → ③ global default. Removed from this file: the
external "Advanced" collapse (``showAdvanced``), the separate
``ProviderSummaryCard`` (redundant with the list + global default), the top-level
``OneKeyOnboard`` (moved into ② Add a provider), the providerCount probe, and the
now-unused useEffect / api / ProviderSummaryCard / OneKeyOnboard / Chevron
imports. Rationale: per-agent model/framework moved to chat, so this page is a
credential wallet + a global default — a single top-to-bottom flow, no junk
drawer.

## 2026-07-06 — nav reorder + Account consolidates billing

Cloud IA cleanup: NAV_ITEMS now leads with the "account" entry (Account &
Subscription), and that entry is "cloudOnly" (new flag; the account/billing
panels render null locally, so the entry would otherwise open a blank pane).
Default active tab is the first VISIBLE item (items[0]), so cloud opens on
Account, local on LLM Providers. QuotaPanel (system free tier) moved OUT of
ProvidersSection INTO the Account section — all "what are my credits / how is
usage paid" concerns (platform free tier + NetMind.AI Power
balance/subscription/top-up) now live together; LLM Providers is
bring-your-own only.


## 2026-07-02 — 新增「Account & Subscription」导航项（Phase 1）

`NAV_ITEMS` 加 `account`（CreditCard 图标，位于 providers 与 bundle 之间），
`active==='account'` 渲染 [[NetmindAccountPanel]]（NetMind 订阅状态 + 沙盒声明）。
注意：这是**真正被挂载**的设置页（route `/app/settings`）——`SettingsModal` 是
死代码（无任何引用），billing 面板务必加在这里而非那里。

## 2026-06-11 — master–detail：左侧导航 + 右侧内容(取代折叠堆叠)

页面从"竖直折叠堆叠"改成 **master–detail**:左侧 `NAV_ITEMS` 导航
(LLM Providers / Bundle / Artifacts / Manage agents / App updates),
`active` 状态切换右侧内容区。复用 `SettingsModal` 的 nav 视觉(选中
`bg-[var(--accent-primary)]/10` + accent 文字,非选中 nm-ink70 + hover)。

- `CollapsibleSection` **已删除**;Bundle/Artifacts/Manage 各自抽成
  `BundleContent` / `ArtifactsContent` / `ManageAgentsContent` 内容面板
  (复用非折叠的 `SectionHeader`)。
- `App updates` 导航项 `desktopOnly`,`NAV_ITEMS.filter(isTauri())` 过滤,
  且内容仍 `active==='updates' && isTauri()` 双保险。
- **懒加载特性保留**:`ArtifactsSection` 只在 `active==='artifacts'` 时挂载
  (条件渲染),所以非该面板时不发它的 fetch——和旧版折叠时一致。
- `ProvidersSection` 内部的 "Advanced configuration" 展开**保留不变**(那是
  面板内的子披露,不是页面级折叠)。
- 布局容器从 `ScrollArea(整页)` 改成 `h-full flex flex-col`:顶部 header
  固定,下面 `flex` 横向分 nav(w-56,自身滚动)+ 内容(`ScrollArea` flex-1)。
- 用户确认要**左侧栏**(常见约定),非最初口述的右侧。

**同日续** — 试过的"providers 全展开 + Fine-tune"(commit 12d4fbf8)被回退
(用户觉得不好看)。最终方案:`ProvidersSection` **始终内嵌 `OneKeyOnboard`
作为"添加 provider"部件**(以前只有 0 provider 时才显示),所以面板同时呈现
**当前在用(`ProviderSummaryCard`)+ 添加新的(OneKeyOnboard 贴 key)**——
"加 provider"不再藏在 Advanced。短暂加过的 "+ Add provider" 按钮已被这个真正
的内嵌部件取代、移除。OneKeyOnboard 的介绍文案也精简成一行。Advanced 折叠保留
(自定义端点 + Custom OpenAI/Anthropic + CLI 登录 + 每槽模型/微调仍在里面)。

> 待办/可选:用户还提过把 "+ Custom OpenAI/Anthropic"(走 `add_provider`,带
> base_url、不重配槽位)也并进这个添加部件。语义和 OneKeyOnboard 的 `onboard`
> (重配两个槽)不同,暂留 Advanced;要并需给部件加 provider 类型含 custom +
> base_url 输入 + 分流到 add_provider。

## 2026-06-10 (later) — secondary sections collapse by default

New `CollapsibleSection` wraps Bundle / Artifacts / Manage-agents
(collapsed by default, hint text only when expanded) — the whole page
now follows the "simple surface first" logic: Providers summary +
four one-line disclosure rows. UpdatesSection (Tauri-only) stays always
visible because a ready update must not be hidden. ArtifactsSection
mounts lazily on expand, so its fetch doesn't run for a collapsed page.

## 2026-06-10 — Providers section adopts the /setup logic: simple face + Advanced disclosure

New `ProvidersSection` wrapper replaces the bare `<ProviderSettings/>`:

- zero providers → `OneKeyOnboard` card (paste one key and go)
- any provider  → read-only `ProviderSummaryCard` (agent framework +
  model, helper model, registered keys at a glance)
- the full 1400-line `ProviderSettings` now lives behind an "Advanced
  configuration" disclosure, collapsed by default

Closing the disclosure (or completing onboard) bumps refreshToken so the
summary re-fetches whatever was edited in Advanced, and remounts
ProviderSettings via a key so it re-reads fresh config. Rationale: the
Settings page was the last surface still leading with the full provider
matrix; this mirrors the first-run page's "simple surface first" logic.


## 2026-05-27 — UpdatesSection rewrite: full state-machine UI

`UpdatesSection` was rewritten to drive off [[updaterStore.ts]]
(the Zustand mirror of the unified Rust state machine
[[updater.rs]]) instead of the old single-call IPC. It now renders
every state explicitly:
- `idle` / `failed` / `up_to_date` → "Check for updates" button
- `checking` / `available` → button shows spinner + status label
- `downloading` → progress bar with `12.3 MB / 412.5 MB (3%)`
- `installing` → spinner + "Installing X.Y.Z…"
- `ready` → "Restart to apply X.Y.Z" button → `restartForUpdate()`

Removed local `busy` / `msg` state. The store IS the state; the
component is pure render. This means clicking "Check" in tray,
Settings, or having the startup auto-check fire all converge on
the same UI — the v1.7.5 issue of "Settings spinner spins forever
with no progress" is structurally impossible now (the spinner
either reflects `checking` (1–30 s) → next state, OR
`downloading` with a real percentage).

`formatBytes` helper for the progress label. Local to this file
because it has no other consumer yet; promote to a shared util
if a third caller appears.

## 2026-05-22 — desktop-only "App updates" section (initial wiring)

Original implementation of `<UpdatesSection />` — a single "Check for
updates" button calling `checkForUpdates()` (deprecated). Replaced by
the state-machine rewrite above.

# SettingsPage.tsx — LLM provider and embedding configuration

## Why it exists

Provides a persistent settings surface within the `/app/settings` route. Currently composes two existing components: `ProviderSettings` (LLM API key and model configuration) and `EmbeddingStatus` (embedding index rebuild management). Neither component is exclusive to this page — `WelcomePage` also uses `ProviderSettings`.

## Upstream / Downstream

Route: `/app/settings`, rendered inside `MainLayout` as a child route. No store reads of its own — delegates entirely to its child components.

`ProviderSettings` calls `GET/POST /api/providers`. `EmbeddingStatus` uses `useEmbeddingStore` which calls `/api/providers/embeddings/*`.

## Design decisions

**Thin wrapper.** This page is deliberately a layout shell. All logic lives in the components it composes. If a new settings category is added (e.g., notification preferences), a new `<section>` with the relevant component is added here.

**`EmbeddingStatus` is a settings concern, not a system concern.** Embedding rebuilds are triggered by the user when they add RAG documents. Placing this in Settings (rather than the RAG panel) reflects that it is a global index operation, not per-document.

## Gotchas

**`EmbeddingStatus` starts polling on mount.** If the user navigates to Settings while a rebuild is running, `EmbeddingStatus` picks up the live status. But if they navigate away before polling stops, the `useEmbeddingStore._pollTimer` continues running. The component itself calls `stopPolling` in its cleanup, so this is handled — but only if `EmbeddingStatus` properly calls `stopPolling` on unmount. Verify this if embedding polling behavior seems wrong after a settings navigation.

## 2026-08-28 补(auto-review I8) — 云端隐藏 plugins nav 项(同步 mode 判定)

plugins 是本地专属概念(云端镜像预装框架)。用 `isForcedCloud()`(`@/lib/runtimeConfig`,读 `window.__NARRANEXUS_CONFIG__`,**真同步、零持久化**)从 NAV_ITEMS 过滤掉 plugins 项。**必须首帧就对**:`active` 的 useState initializer 首帧即跑——异步 fetch 版(初版)会让云端 `?tab=plugins` 深链首帧把 active 定成 plugins→过滤后仍渲染空面板(round-5 抓到)。**不用 `useRuntimeStore.mode`**(round-6 修正):那是 useEffect 置的、且 persist 进 localStorage,某云端 origin 若曾因 config.js 未注入落过 `mode:'local'`,首帧会是脏的 truthy→窄窗口漏面板;`isForcedCloud()` 无此窗口且与 useResolveAppMode 同判据。`onManagePlugins` 云端传 **`undefined`**(round-7 从 `if(!isCloud)` 护栏内吞改来:护栏内吞会让 prop 仍 truthy→ModelDefaults 渲染成"可点但点了没反应"的死链;传 undefined 则走它 `? :` 的纯文本降级分支)。PluginsSettings 仍按后端 `cloud_managed` return null 作独立第二层兜底。删了死 key cloudManagedNote。
