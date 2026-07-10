---
code_file: frontend/src/components/layout/Sidebar.tsx
last_verified: 2026-07-03
stub: false
---

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
