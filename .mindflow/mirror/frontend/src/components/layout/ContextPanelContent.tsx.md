---
code_file: frontend/src/components/layout/ContextPanelContent.tsx
last_verified: 2026-06-02
stub: false
---

## 2026-06-02 — wrapper must be `flex flex-col` (right-panel scroll fix)

The outer div is `flex-1 min-h-0 flex flex-col`. The `flex flex-col` is
load-bearing, not cosmetic: every panel renders a `Card` sized with
`h-full`, and `h-full` only resolves against a parent that establishes a
definite-height flex column. MainLayout's wrapper is already `flex flex-col`,
but this intermediate div previously sat between as a plain block, severing
the height chain — so each panel's inner `ScrollArea` got no bounded height
and the right sidebar (Awareness/Workspace etc.) clipped its overflow
instead of scrolling. The earlier `afe574e` fix only addressed the *inner*
nested ScrollAreas (discoverability + overscroll-contain); this restores the
*outer* panel scroll. Pairs with [[scroll-area]]'s `[&>div]:!block` Viewport
fix that keeps Radix from defaulting to `display: table`.

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
