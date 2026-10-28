---
code_file: frontend/src/lib/chunkReload.ts
last_verified: 2026-08-12
stub: false
---

# chunkReload.ts — 部署期 stale-chunk 404 恢复原语

## 为什么存在

Mark item 10。路由用 `React.lazy` 切 chunk（[[App.tsx]]）。发版后老 tab 引用**旧 hash**的 chunk 文件名,下一次导航 dynamic import 404、React.lazy 抛错 → [[ChunkErrorBoundary.tsx]] 捕获并靠一次 reload 恢复。

## 设计要点（复审后重构）

**刻意不挂全局 `vite:preloadError` 事件。** 初版监听该事件并无条件 `preventDefault`——问题:①`preventDefault` 后 Vite 把**每一个** dynamic import 的 rejection 吞成 `undefined`(echarts/lazy renderer 等全站受害,报出无关的 `Cannot read undefined`);②该事件对**后台预取**(`void import(...)`,用户没导航)也触发,弱网抖一下就整页 reload、吞掉用户草稿(铁律 #16)。故移除事件监听,恢复改由 ErrorBoundary 驱动——只在 chunk 失败**真的到达 render**(用户已卡住)时才动。

导出两个纯函数供 boundary 用:
- `reloadOncePerSession(reload, storage)`:**会话级 guard**(`nx-chunk-reloaded`),坏构建不会无限刷;`storage` 拿不到(隐私模式)时返回 false 不刷;`reload`/`storage` 注入以便测试。
- `isChunkLoadError(error)`:按 message 正则判 chunk/dynamic-import 失败,区分「发版产物」与「真 bug」。

见 `lib/__tests__/chunkReload.test.ts`。
