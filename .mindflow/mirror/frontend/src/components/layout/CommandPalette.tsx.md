---
code_file: frontend/src/components/layout/CommandPalette.tsx
last_verified: 2026-06-24
stub: false
---

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
  agent is selected — the context panels from `ALL_TABS`. A simple
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
