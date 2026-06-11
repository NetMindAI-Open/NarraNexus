---
code_file: frontend/src/components/bookmarks/BookmarkDrawer.tsx
last_verified: 2026-06-10
stub: false
---

# BookmarkDrawer.tsx — Slide-over shell for bookmark panels

## 为什么存在

Opening a bookmark must not squeeze the chat — the redesign's promise
is "space goes back to the conversation" (spec §6). So panel content
opens in a right-anchored slide-over that floats OVER the content,
with an explicit pin escape-hatch for power users who want the old
persistent-column behavior back.

## 上下游关系

- **被谁用**: MainLayout's ChatView; children are the Activity /
  Agent-profile panels (M3b).
- **依赖谁**: nothing project-specific — a generic shell (portal,
  backdrop, header with pin/close).

## 设计决策

- **Slide-over by default, pin to become a column** — pinned state is
  controlled by the parent (persisted in localStorage there), because
  only the parent knows how to re-flow the layout around a static
  column.
- Portal + transparent backdrop in slide-over mode; `role="dialog"` +
  `aria-modal` ONLY in slide-over mode. A pinned column is part of the
  page, not a dialog — keeping aria-modal there would trap screen
  readers.
- 440px width (clamped to viewport) — wider than the old 320px column
  on purpose; together with accordions this kills the "endless
  scrolling" complaint.
- Esc / backdrop-click / re-click close only apply when unpinned.

## 新人易踩的坑

`data-drawer-backdrop` is a styling-free hook used by tests; don't
remove it when restyling.
