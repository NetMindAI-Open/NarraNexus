---
code_file: frontend/src/platform/builtinPanels.tsx
last_verified: 2026-09-22
stub: false
---

## 2026-09-22 - Browser is a lazy drawer panel

BrowserTab passes the agent ID to BrowserStreamPanel as its session identity.
The stream component is imported with React.lazy, like the other heavy panels,
so registering builtins does not eagerly load browser rendering dependencies.
BookmarkPanelHost supplies the existing Suspense boundary. This also keeps test
setup's builtin registration from caching the stream component before test mocks.

## 2026-09-03 — 每个条带 tab 一个可注册的小组件

原 `BookmarkPanelHost` 的 11 个分支各成一个组件（AwarenessPanel 的五个 section 变体、Jobs 带
`onJobResolved` 回调、Artifacts 列、Skills/MCP、Memory 包一层滚动容器）。重面板全部 lazy。
