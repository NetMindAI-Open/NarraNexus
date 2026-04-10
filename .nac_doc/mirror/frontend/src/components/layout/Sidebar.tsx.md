---
code_file: frontend/src/components/layout/Sidebar.tsx
last_verified: 2026-04-10
stub: false
---

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
