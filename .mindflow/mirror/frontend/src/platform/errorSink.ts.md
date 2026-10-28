---
code_file: frontend/src/platform/errorSink.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-07（四轮复审）— 嵌套上报改为排队而非丢弃

派发中产生的上报（监听器或 poster 失败路径报的另一个错）此前只记入 `recent`、既不扇出也不 POST（复审 M4）。
现在进入 `pending`，当前派发结束后按序 `deliver`（监听器 + poster），上限 `RECENT_LIMIT` 条——每次上报都再上报的
监听器会在上限处被切断而不是死循环。测试改为「按序送达、不重入」+「有界」两条。

## 2026-09-07（批 1 三轮复审移植）— 重入保护、快照返回、`resetErrorSink` 的定位

监听器（或 poster 的失败路径）自己再 `reportUiError` 不会递归：派发中的嵌套上报只记入 `recent` 不再扇出。
`recentUiErrors()` 返回拷贝而不是活缓冲区。`resetErrorSink` 保留在本模块（重置与它重置的状态不能分家），
文档写明只供测试——`test-setup.ts` 让每个测试文件都共享这个单例，用后必须重置。

## 2026-09-03（批 2d）— 已知归因 + 后端上报

`reportUiError` 接受 `source`（加载器/宿主知道自己在服务哪个插件时直接给）与 `context`；`setErrorPoster`
装上后，非 shell 来源的错误按插件 2s 节流 POST 到工场 API（渲染死循环不能变成请求死循环）。

## 2026-09-03 — UI 崩溃的归因与订阅

之前渲染崩溃只到 `console.error`。现在 `ChunkErrorBoundary` 同时上报到 sink。归因是**尽力而为的
启发式**：`error.stack` 文本里含某个已登记的 chunk URL 前缀就归到那个插件 id（`attributeChunkUrl`
由批 2 的插件加载器登记），否则归 `shell`；浏览器栈格式不统一，所以可能漏归（不会错归到别的插件，
除非两个前缀互为前缀）。批 1 还没有订阅者——工场页健康视图与后端上报在批 2 接入；最近 50 条留在
内存供其读取。监听器自己抛错不会盖掉原错误；`onUiError` 的退订函数返回 void。
