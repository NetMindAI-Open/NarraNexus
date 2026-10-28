---
code_file: frontend/src/platform/pageRoutes.tsx
last_verified: 2026-09-07
stub: false
---

## 2026-09-07（批 1 四轮复审移植）— 未知 guard fail-closed；坏条目只报一次

- manifest 在运行时是无类型的：`guard` 不在 `protected | public | open` 之内的页面此前落到 `guarded()` 的
  「否则不包」分支，等于无守卫渲染。现在 `GUARDS` 集合先判，未知值一律丢弃并报告（fail closed）。
- 路由表每次渲染重建，同一条坏条目此前每帧报一次错。`reject()` 以 owner/id/guard/layout 为键去重，
  一次页面加载只报一次；`resetPageRouteReports()` 供测试复位。
- `builtin.test.ts` 的路由 golden 改为逐行（layout/guard/gate/element 种类/lazy 页面模块名），路由接错页面在这里失败；
  `bundle-import` 与 `templates-install` 共用同一组件由测试钉住。

## 2026-09-03 — 路由表构造器：`PAGES` 条目 → `<Route>` 元素

从 `App.tsx` 抽出，唯一目的是可测：预审指出「App.tsx 消费注册表」只有静态比对、没有渲染证据。
`pageRouteElements(entries, wrappers)` 返回顶层路由（按 `guard` 套注入的 ProtectedRoute/PublicRoute）
与 `/app` 子路由两组；守卫组件由 `App.tsx` 注入，因为它们绑着壳的登录状态。
`__tests__/pageRoutes.test.tsx` 在 MemoryRouter 里渲染：守卫包裹、`open` 无包裹、首帧后注册的页面
不重挂载即出现（`useRegistryEntries` 订阅）、`null` element 占位。

## 2026-09-07 — an illegal `/app` page is dropped, not thrown (C-2)

Previously an `/app`-layout page that did not declare `guard: 'protected'` made
`pageRouteElements` throw. That function runs inside `App.tsx`'s render body, above the nearest
`ChunkErrorBoundary` — a throw here white-screens the entire shell over one bad plugin manifest,
turning a single misconfigured plugin into a platform-wide outage. It now calls
`reportUiError(..., { source: owner })` and skips the entry (drops it from `app`), so the rest of
the shell keeps rendering; `loader.registerDeclaredUi` already rejects such entries at
REGISTRATION time (loader.ts) so they never reach `PAGES` via the normal path — this check is
defense in depth for anything that reaches `PAGES` another way (a plugin's own bundle calling
`host.registries.pages.register` directly). `pageRoutes.test.tsx`'s "rejects an /app page" test
now asserts the drop (the entry is absent from `result.app`) and that `onUiError` fires exactly
once, instead of asserting a throw.
