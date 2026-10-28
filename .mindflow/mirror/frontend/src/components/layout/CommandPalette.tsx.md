---
code_file: frontend/src/components/layout/CommandPalette.tsx
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — a throwing plugin `visible()` no longer crashes the palette (M-3)

Plugin-contributed commands from `ui.commands` are filtered through `CommandDef.visible()`
(renamed from `when` — see `registries/commands.ts`'s mirror doc) directly inside the `useMemo`
that builds the command list. That predicate is now called inside a `try/catch`: a throw is
reported via `reportUiError` (source = the command's owning plugin) and the command is treated
as hidden, instead of the exception propagating out of `useMemo` and crashing the whole ⌘K
palette for every command, not just the misbehaving plugin's.

## 2026-09-04 — 面板列表也认「可恢复」

`visibleTabs({ studioOpen, studioResumable })`：收起后的 studio 仍能从 ⌘K 选 Builder 回去，
选中后由 [[../../hooks/useStudioLifecycle.ts]] 恢复。

## 2026-09-03 — 面板列表改走 `visibleTabs`

原来 `ALL_TABS.map(...)` 对任意 agent 都列出创建工作室的 Builder 面板，绕过了
MainLayout / ChatHeader 刻意做的隐藏 —— 而 palette 恰是移动端的面板主入口。现在
与抽屉切换器共用 [[../bookmarks/tabs.ts]] 的 `visibleTabs({ studioOpen })`，
`studioOpen` 订阅自 [[../../stores/studioStore.ts]]。

# layout/CommandPalette.tsx — ⌘K quick-jump navigator

## Why it exists

As the app grew (many agents + several top-level pages + per-agent context
panels) clicking through the sidebar and bookmark strip became the slow path.
This gives a keyboard-first jump target: type, arrow, Enter. It is deliberately
a *navigator*, not a kitchen-sink command runner — every entry only selects an
agent and/or routes, so there is nothing with side effects to confirm or undo.
On mobile it doubles as the **entry point for context panels**, because the
right bookmark strip is hidden there.

## How it works / design

- Builds one flat command list each render: every agent (jump into its chat),
  the four pages (Chat / Dashboard / Settings / System), and — only when an
  agent is selected — the context panels from `visibleTabs({ studioOpen })` (see 09-03 entry). A simple
  case-insensitive substring filter over label + hint; arrow keys move the
  highlight, Enter runs, Esc / backdrop closes.
- Upstream: opened by [[TopBar]] (which owns the `paletteOpen` boolean and the
  global ⌘K key handler). Downstream: reads agents + `setAgentId` from
  [[useConfigStore]], `requestPanel` from [[uiStore]]; routes via
  `react-router`; renders [[RingAvatar]] for agent rows and lucide icons for
  pages.
- Panel commands route to `/app/chat` then call `requestPanel(tab.id)`, which
  parks the tab id in [[uiStore]] for [[ChatView]] to open and clear — this is
  the only way to reach a context panel on mobile.
- Gotcha: `active` is clamped against `filtered.length` on every filter change
  so the highlight never points past the end as results narrow; focus is
  deferred with `requestAnimationFrame` so the input exists and the overlay has
  painted before focusing.

Merged with the plugin platform (2026-09-06): pages, drawer panels, sidebar items, commands and agent-row badges come from the frontend registries (`platform/registries`, registered in `platform/builtin.ts`); this file keeps dev's behaviour on top of that.
