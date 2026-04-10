---
code_dir: frontend/src/components/layout/
last_verified: 2026-04-10
stub: false
---

# layout/ — Shell, navigation, and right-panel tab system

## 目录角色

Owns the three-column app shell:
1. `Sidebar` (left, collapsible) — agent list, user info, nav links.
2. `ChatPanel` (center) — the main interaction surface.
3. Right `ContextPanel` — tabs rendered by `ContextPanelHeader` + `ContextPanelContent`.

`MainLayout` is the React Router layout component. Sub-pages (`/app/settings`, `/app/system`) render via `<Outlet />` instead of the default `ChatView`.

## 关键文件索引

| File | Role |
|------|------|
| `MainLayout.tsx` | Root shell; owns `ContextTab` state; calls `preloadAll` when agent/user changes. |
| `Sidebar.tsx` | Collapsible sidebar; handles logout + mode-switch with hard `window.location.href` reload. |
| `AgentList.tsx` | Agent CRUD (create, rename, delete, toggle public), streaming indicator, completion badge. |
| `ContextPanelHeader.tsx` | Defines `ContextTab` type; renders tab strip with notification badges; contains `CostPopover`. |
| `ContextPanelContent.tsx` | Lazy-loads all five panel components. The single place where `React.lazy` is used for panels. |

## 和外部目录的协作

- All layout components read `useConfigStore` for `agentId`, `userId`, and `agents`.
- `Sidebar` additionally touches `useRuntimeStore` (mode, cloud API URL) and orchestrates the multi-store clear on logout/mode-switch.
- `ContextPanelHeader` reads `usePreloadStore.agentInboxUnreadCount` for the inbox badge and `useConfigStore.awarenessUpdatedAgents` for the awareness dot.
