---
code_file: frontend/src/components/layout/Sidebar.tsx
last_verified: 2026-07-21
stub: false
---

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
