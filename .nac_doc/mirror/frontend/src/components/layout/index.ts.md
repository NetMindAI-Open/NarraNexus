---
code_file: frontend/src/components/layout/index.ts
last_verified: 2026-04-10
stub: false
---

# index.ts — Barrel export for layout components

Re-exports `MainLayout`, `Sidebar`, `AgentList`, `ContextPanelContent`, `ContextPanelHeader`. Consumed by the React Router config and `MainLayout` itself. `ContextTab` type is re-exported for consumers that need to reference the tab union outside of layout files.
