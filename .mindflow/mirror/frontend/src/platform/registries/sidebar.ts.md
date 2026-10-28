---
code_file: frontend/src/platform/registries/sidebar.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 侧栏行注册表

原 `Sidebar.tsx` 六个内联 `<button>` 变成六条数据。`isActive` 是谓词而不是路径相等，因为
Dashboard/Export 两行靠 `?tab=` 区分；`visible` 按运行时 feature 门（System 行只在本地）；
`order` 用 10/20/… 留缝给插件；`prefetch` 承接 dashboard chunk 的 hover 预热。
`sortedSidebarItems(features, entries?)` 是过滤+排序的唯一实现：组件传入 `useRegistryEntries(SIDEBAR)`
的快照，测试与非 React 调用方用默认的 `SIDEBAR.list()`。
