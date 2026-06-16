---
code_dir: frontend/src/components/layout/
last_verified: 2026-06-10
stub: false
---

# layout/ — Shell, navigation, and the bookmark-strip edge

## 目录角色

Owns the app shell (2026-06-10 redesign):
1. `Sidebar` (left, collapsible) — team-grouped agent list, user info, nav links.
2. Chat + Artifact group (center) — the main interaction surface, now
   takes all remaining width.
3. Right edge — `BookmarkStrip` (~36px, from `components/bookmarks/`)
   replaces the old permanent 5-tab ContextPanel. Panel content opens
   in a `BookmarkDrawer` slide-over, or as a pinned static column.

`MainLayout` is the React Router layout component. Sub-pages
(`/app/settings`, `/app/system`) render via `<Outlet />` instead of the
default `ChatView`.

## 关键文件索引

| File | Role |
|------|------|
| `MainLayout.tsx` | Root shell; owns drawer state (tab / focusKey / pinned), chat↔artifact split, calls `preloadAll`; mounts `useBookmarkSignals`. |
| `Sidebar.tsx` | Collapsible sidebar; handles logout + mode-switch with hard `window.location.href` reload. |
| `AgentList.tsx` | Team-grouped agent list + CRUD; collapsed avatar rail. |
| `AgentGroupSection.tsx` | One collapsible team section (header + rows). |
| `AgentRowMenu.tsx` | Kebab menu for per-row actions. |
| `AgentsHeaderMenu.tsx` | ⋯ overflow menu (import / export / manage teams). |
| `agentGroupUtils.ts` | Pure grouping + collapse-persistence helpers. |
| `ResizableDivider.tsx` | Chat↔artifact drag handle (ghost-line commit-on-release). |

Retired 2026-06-10: `ContextPanelHeader.tsx`, `ContextPanelContent.tsx`
(replaced by the bookmark strip + drawer), `TeamFilterBar.tsx` (teams
became list sections).

## 和外部目录的协作

- All layout components read `useConfigStore` for `agentId`, `userId`, `agents`.
- `Sidebar` additionally touches `useRuntimeStore` (mode, cloud API URL) and orchestrates the multi-store clear on logout/mode-switch.
- `MainLayout` feeds [[bookmarkStore]] via `useBookmarkSignals` (jobs /
  inbox / awareness signals) and hosts `CostPopover` at the chat card's
  top-right (its old home, the ContextPanel tab bar, is gone).
