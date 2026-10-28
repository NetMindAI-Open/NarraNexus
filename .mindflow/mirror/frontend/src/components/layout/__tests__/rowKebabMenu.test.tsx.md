---
code_file: frontend/src/components/layout/__tests__/rowKebabMenu.test.tsx
last_verified: 2026-09-11
stub: false
---

# rowKebabMenu.test.tsx

Pins the shell contract of [[RowKebabMenu]], the one dropdown both sidebar row
menus (AgentRowMenu, TeamRowMenu) render: `onOpenChange` fires on open AND on
close (the host row lifts its z-index with it), an item click runs its
`onSelect` and never bubbles to the row's own click handler, a disabled item
does nothing and leaves the panel open, and Escape closes the panel and tells
the host.
