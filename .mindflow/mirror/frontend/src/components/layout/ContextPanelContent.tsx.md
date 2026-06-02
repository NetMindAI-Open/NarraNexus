---
code_file: frontend/src/components/layout/ContextPanelContent.tsx
last_verified: 2026-04-10
stub: false
---

# ContextPanelContent.tsx — Lazy panel loader for the right-side tab content

## 为什么存在

All five right-panel components (`RuntimePanel`, `AwarenessPanel`, `AgentInboxPanel`, `JobsPanel`, `SkillsPanel`) are `React.lazy` here. This defers loading ReactFlow, react-markdown, and other heavy deps until the user actually clicks a tab. Without this, the initial bundle would be significantly larger.

## 上下游关系
- **被谁用**: `MainLayout.ChatView`.
- **依赖谁**: All five panel components via lazy import.

## 设计决策

`key={activeTab}` on the outer div causes React to remount the panel when the active tab changes. This ensures each panel resets its scroll position and local state when you switch away and back. It trades a remount cost for simplicity over trying to preserve scroll positions.

The `PanelFallback` spinner is shown during the lazy load — typically only on the very first activation of each tab.

## 新人易踩的坑

Adding a new tab requires changes in three places: `ContextPanelHeader.tsx` (tab definition array + `ContextTab` type), here (lazy import + render condition), and `MainLayout` if the panel needs the `onAgentComplete` callback.
