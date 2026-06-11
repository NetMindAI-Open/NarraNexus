---
code_file: frontend/src/components/layout/Sidebar.tsx
last_verified: 2026-06-10
stub: false
---

## 2026-06-10 — TeamFilterAndAgents wrapper retired

The grouped-sidebar redesign deleted TeamFilterBar; Sidebar now renders
`<AgentList collapsed={...}/>` directly inside the ScrollArea. The
`TeamFilterAndAgents` helper (chip-filter state + filterAgentIds
derivation) is gone — grouping lives in [[agentGroupUtils]] /
[[AgentList]]. Collapsed-mode team representation (formerly ∗/∅/color
dots) is now AgentList's avatar rail; Sidebar's own collapsed duties
are unchanged (logo, user avatar, nav icons, footer).

## 2026-05-19 — Sidebar bg moved to `--nm-paper`

Outer `<aside>` background now reads `bg-[color:var(--nm-paper)]` so the sidebar sits directly on NM paper (FinChats:461 canonical). Per-row treatment moved into `AgentList` — see its mirror for the row-bg priority rewrite.

## v2.2 改动（2026-04-13）

- **G1 prefetch**：Dashboard nav button 加 `onMouseEnter` / `onFocus` 触发 `import('@/pages/DashboardPage')`，预热 Vite chunk。静态字面量 → Vite 编译期解析，无 injection 风险。配合 MainLayout 的内层 Suspense + DashboardSkeleton，hover 过的导航点击近乎瞬时。

# Sidebar.tsx — Collapsible left rail: branding, user, agents, nav, mode-switch

## 为什么存在

Single place that owns the nav actions (Settings, System), the mode-switcher (local vs cloud), and the destructive logout/clear actions. Collapsible to 72px icon-only mode.

## 上下游关系
- **被谁用**: `MainLayout`.
- **依赖谁**: `AgentList`, `ThemeToggle`, `useConfigStore`, `useChatStore`, `useRuntimeStore`, `usePreloadStore`, `api.clearHistory`.

## 设计决策

Logout and mode-switch both call `wipeAllSessionData()` which:
1. Calls `logout()`, `clearChat()`, `clearPreload()` to reset Zustand in-memory state.
2. Directly calls `localStorage.removeItem()` for every known persisted key.
3. Does `window.location.href = '/...'` (full page reload, not React Router navigate).

The hard reload is intentional. A soft `navigate()` keeps the React tree, closure-captured store snapshots, and module-level caches alive from the previous session, which caused data bleed between cloud and local modes. The direct `localStorage.removeItem` calls are the authoritative clear, not relying on Zustand persist flushing before the reload.

The System page link is feature-flagged behind `features.showSystemPage` from `useRuntimeStore`.

## Gotcha / 边界情况

The mode-switch popup is a raw `div` with manual positioning (not a Popover) — it does not close when clicking outside. Clicking the mode-switch button again toggles it.
