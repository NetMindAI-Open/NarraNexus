---
code_file: frontend/src/platform/registries/panels.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-03 — 抽屉面板注册表

本注册表只管「tab id → 面板组件」，`BookmarkPanelHost` 查表渲染而不是 `&&` 链。面板组件统一接 `{ agentId }`。

## 2026-09-07 — `PanelStripDef` (I-3: the drawer strip is derived from PANELS, not a parallel table)

`PanelDef` gains an optional `strip?: PanelStripDef` field (label/labelKey/icon, optional
stripLabel/stripLabelKey, category, order, conditional) — a panel with `strip` gets a real entry
on the bookmarks drawer strip; a panel without it is only reachable by direct tab id (e.g. a
plugin panel meant to be opened from elsewhere, not browsed). `bookmarks/tabs.ts`'s
`stripCategories()`/`allTabs()`/`builtinTabIds()` derive the strip's grouped layout from
`PANELS.list()` at call time (see that file's mirror doc for why they are functions, not consts) —
this file no longer coexists with a separate hardcoded strip table.
