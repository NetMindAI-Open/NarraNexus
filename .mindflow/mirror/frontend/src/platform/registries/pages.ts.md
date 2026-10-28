---
code_file: frontend/src/platform/registries/pages.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 页面注册表：路由表变成数据

`App.tsx` 经 `platform/pageRoutes.tsx` 从注册表条目渲染 `<Route>`：`layout: 'top'` 的在顶层、按 `guard` 套
ProtectedRoute/PublicRoute/裸；`layout: 'app'` 的作为 `/app` MainLayout 的子路由（父路由已有守卫）。
`element: null` 保留给 chat / team chat 这类由 MainLayout 自己在主槽渲染的"路由占位"。
`guard` 只对 `layout: 'top'` 起作用；`/app` 下的页面继承父路由的 ProtectedRoute，必须声明
`protected`（`platform/pageRoutes.tsx` 对其它值抛错）。曾有的 `titleKey` 字段无消费方，已删。
壳的 17 条路由由 `platform/builtin.ts` 注册，顺序即原 JSX 顺序（`platform/__tests__/golden/
routes-before.json` 是改造前抓取的表，`builtin.test.ts` 逐条比对）。
