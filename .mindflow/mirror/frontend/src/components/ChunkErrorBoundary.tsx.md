---
code_file: frontend/src/components/ChunkErrorBoundary.tsx
last_verified: 2026-09-03
stub: false
---

# ChunkErrorBoundary.tsx — 崩溃兜底 + stale-chunk 自愈,区分两类

## 2026-09-03 — 崩溃同时上报 `platform/errorSink`

console 输出保留；`reportUiError` 按 chunk URL 归因到插件或壳，供观察窗与工场页。

## 为什么存在

Mark item 10。路由级 boundary,包住整个 `<Suspense><Routes>`（[[App.tsx]]）。到达 render 的崩溃有两类,**必须区分**（复审 item 5）:
- **stale-chunk 404**(发版产物):`componentDidCatch` 里 `isChunkLoadError` 命中 → `reloadOncePerSession` 一次自愈([[chunkReload.ts]]);UI 显示「有新版本 / 刷新」。
- **真 render bug**:不自愈,UI 显示中性的「出错了 / 刷新重试」——**不能**一律说「有新版本」,否则用户刷新后照崩、陷死循环还不报障,bug 被永久掩盖。

## 设计要点

- class 组件(error boundary 必须)。`getDerivedStateFromError` 存 error;`componentDidCatch` 打日志 + 决定是否自愈。
- **上报**:目前只有带 tag 的 `console.warn`(chunk)/`console.error`(真 bug)。前端暂无 error sink,且鉴权版 analytics 端点承载不了登出态(/login)崩溃 → 专用 render-crash beacon 记为 follow-up。**本次的承重修复是「真 bug 不再伪装成新版本」**,不是遥测。
- **render 刻意少依赖**:inline 样式 + 纯英文,CSS/i18n 正是失败源时仍能渲染兜底。
- **`recover?` prop 可注入**(默认 `() => window.location.reload()`,生产挂载点不传)——让「chunk 才自愈、真 bug 绝不自愈」这条**承重分支**可被测试断言(复审二轮 item 2:之前测试 `beforeEach` 预置 guard 恰好绕开它,写反照样全绿)。`RELOAD_GUARD_KEY` 从 [[chunkReload.ts]] 导出,测试引用常量而非硬写字面量。

见 `components/__tests__/ChunkErrorBoundary.test.tsx`（chunk→recover 调用一次+新版本 / 真 bug→recover 不调+中性 / 每 session 最多一次 / 正常→children）。
