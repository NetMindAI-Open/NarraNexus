---
code_file: frontend/src/stores/uiStore.ts
last_verified: 2026-06-24
stub: false
---

# stores/uiStore.ts — shared UI-chrome state with no backend

## Why it exists

A small zustand store for layout-chrome state that several sibling components
share but no backend cares about. It exists so these booleans/ids live in one
place instead of being prop-drilled across three siblings ([[TopBar]],
[[MainLayout]], [[Sidebar]]) that have no natural common parent to hold them.
It is intentionally separate from [[configStore]]/[[runtimeStore]] because this
is ephemeral view state, never persisted or synced.

## How it works / design

- Two concerns: (1) `mobileNavOpen` — the off-canvas agent-list drawer on
  small screens; [[TopBar]]'s hamburger toggles it, [[MainLayout]] renders its
  backdrop, [[Sidebar]] closes it on navigation. (2) `pendingPanel` — a context
  panel requested from [[CommandPalette]].
- Upstream/producers: [[TopBar]] (`toggleMobileNav`), [[CommandPalette]]
  (`requestPanel`). Consumers: [[MainLayout]] / [[Sidebar]] read
  `mobileNavOpen`; [[ChatView]] reads `pendingPanel`, opens the matching drawer,
  then calls `clearPendingPanel`. Re-exported via [[stores]] `index.ts`.
- `pendingPanel` is the mobile entry point for context panels (awareness / jobs
  / …) because the right bookmark strip is hidden on mobile — ⌘K is the only way
  in, and this store is the hand-off channel.
- Gotcha / design decision: `pendingPanel` is typed as a bare `string` rather
  than the real `AtomicTabId` on purpose, to keep this store free of component
  imports (it stays a pure leaf store). The consumer is responsible for
  clearing it so a request fires exactly once.
