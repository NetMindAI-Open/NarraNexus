---
code_file: frontend/src/pages/SettingsPage.tsx
last_verified: 2026-07-30
stub: false
---

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

Provides a persistent settings surface within the `/app/settings` route. Currently composes two existing components: `ProviderSettings` (LLM API key and model configuration) and `EmbeddingStatus` (embedding index rebuild management). Neither component is exclusive to this page — `SetupPage` also uses `ProviderSettings`.

## Upstream / Downstream

Route: `/app/settings`, rendered inside `MainLayout` as a child route. No store reads of its own — delegates entirely to its child components.

`ProviderSettings` calls `GET/POST /api/providers`. `EmbeddingStatus` uses `useEmbeddingStore` which calls `/api/providers/embeddings/*`.

## Design decisions

**Thin wrapper.** This page is deliberately a layout shell. All logic lives in the components it composes. If a new settings category is added (e.g., notification preferences), a new `<section>` with the relevant component is added here.

**`EmbeddingStatus` is a settings concern, not a system concern.** Embedding rebuilds are triggered by the user when they add RAG documents. Placing this in Settings (rather than the RAG panel) reflects that it is a global index operation, not per-document.

## Gotchas

**`EmbeddingStatus` starts polling on mount.** If the user navigates to Settings while a rebuild is running, `EmbeddingStatus` picks up the live status. But if they navigate away before polling stops, the `useEmbeddingStore._pollTimer` continues running. The component itself calls `stopPolling` in its cleanup, so this is handled — but only if `EmbeddingStatus` properly calls `stopPolling` on unmount. Verify this if embedding polling behavior seems wrong after a settings navigation.
