---
code_file: frontend/src/components/layout/AgentRowMenu.tsx
last_verified: 2026-07-23
stub: false
---

## 2026-07-23 — 新增"编辑…"菜单项

在"重命名"下方加 `onEditAgent` 驱动的"编辑…"项(SquarePen 图标),打开
[[EditAgentDialog.tsx]] 编辑名称 + 描述。重命名仍是行内快速改名;编辑项是名称 +
描述的完整编辑器(描述字段唯一的编辑入口)。对所有人可见,归属校验在保存时由后端做。

## 2026-07-10 — "Clear data…" entry (owner-only)

New `onClearData` prop + an owner-only `Eraser` menu item above Delete. It
opens [[ClearAgentDataDialog.tsx]] (hosted by [[AgentList.tsx]]) — the scoped
"clear conversations / memory" wipe. Replaces the old, ineffective footer
"clear history" button that used to live in [[Sidebar.tsx]] (DB-only, never
touched on-disk narratives, so the agent kept remembering).

# AgentRowMenu.tsx — Kebab (⋮) menu for per-agent row actions

## 2026-06-11 (v1.8.1) — onOpenChange for the stacking fix

Every agent row is its own stacking context (animate-slide-up retains
a transform via fill-mode forwards), so this panel's z-50 could not
rise above the NEXT row — Delete was unclickable. The menu now reports
open/close via `onOpenChange` and the host row lifts itself with
`relative z-30` while open (see [[AgentGroupSection]]).

## 为什么存在

The 2026-06-10 sidebar redesign moved the inline hover buttons
(Pencil rename / Trash delete / Globe-Lock public toggle) off the
agent row into a kebab menu (spec §11.2) so row 1 holds only
avatar + name + time and the name keeps its full width.

## 上下游关系

- **被谁用**: `AgentGroupSection`'s AgentRow.
- **依赖谁**: lucide icons only. All actions are callbacks; the row
  passes pre-bound `(e) => onX(agent, e)` closures.

## 设计决策

- Opens as an absolute-positioned inline panel, NOT a Radix Popover —
  a portal'd popover gets clipped/misplaced inside the sidebar's
  scroll container, and the menu is small enough that inline
  positioning is fine.
- `isOwner=false` hides delete and public-toggle (backend enforces
  real ownership; the UI just doesn't offer what will be rejected).
  Rename is always shown.
- `showPublicToggle` is threaded from AgentList's
  SHOW_AGENT_PUBLIC_TOGGLE feature flag — don't hardcode it here;
  re-enabling the public feature must be a one-line flip in AgentList.

## 新人易踩的坑

The menu stops click propagation at its wrapper in AgentRow — if you
add an action, call the callback with the original event so
`e.stopPropagation()` in AgentList handlers still works (otherwise
the row's onClick selects the agent as a side effect).
