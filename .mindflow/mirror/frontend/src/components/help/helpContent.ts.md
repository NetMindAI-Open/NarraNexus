---
code_file: frontend/src/components/help/helpContent.ts
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — bookmarks.* 锚点殉葬修复

BookmarkStrip 退役后 `bookmarks.{awareness,workspace,channels,skills,mcp,social}`
六个锚点再无 DOM 宿主,对应标注被 overlay 静默跳过——setup 页 7 条只剩 2 条,
「?」引导页大面积空白。六条合并成两条,挂在常驻的 `chat.detail-menu` 触发器上
(菜单**内部**的项在菜单关闭时不存在,挂上去等于没挂)。教训:「overlay 对缺失
锚点静默跳过」是防崩机制,不是「文案可以不管」;删渲染方时要 grep 它产的
`data-help-id`。

## 2026-08-06 — Chat UI v4 锚点跟随

侧栏改版后更新三条注释:`sidebar.create-agent` 现挂在 New 菜单上(文案改为
create or import);`sidebar.manage-agents` 挂在 Dashboard 导航行(manage
页并入 Dashboard);`sidebar.agents-menu` 注释改为 `sidebar.export`(⋯ 菜单
已删除,导出是一级导航行)。overlay 对缺失锚点静默跳过,无需强同步。

## 2026-06-11 (PM) — pages

Manifest restructured to HelpPage[] (3 Owner-specified topics). New
anchors: sidebar.manage-agents, sidebar.team-section, chat.messages,
layout.artifacts. `side` → `rail` (left/right/top note placement).

## 2026-06-11

Strip anchors re-pointed after the atomic-IA revision:
bookmarks.activity/bookmarks.agent → bookmarks.strip + bookmarks.jobs.



# helpContent.ts — Annotation manifests (pure data)

One exported array per view; entries reference `data-help-id` anchors.
Density discipline (spec §12.5): **≤ 8 per view**, enforced by a test —
a view that needs more annotations needs less UI, and this list doubles
as a complexity audit. Settings-page manifest deliberately deferred
until the parallel Settings redesign lands (spec §14.7). Used by
[[HelpButton]] / [[HelpOverlay]].
