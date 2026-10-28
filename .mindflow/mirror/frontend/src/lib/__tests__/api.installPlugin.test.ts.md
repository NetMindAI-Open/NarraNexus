---
code_file: frontend/src/lib/__tests__/api.installPlugin.test.ts
last_verified: 2026-08-28
stub: false
---

# api.installPlugin.test.ts

钉住 `api.installPlugin` 的 ndjson 手动流解析：喂一个假 `ReadableStream`，
断言 `onEvent` 按行顺序逐个收到事件、返回值等于最后一帧；额外覆盖一行被拆成
两个网络 chunk 到达时仍拼成一个事件（TCP 不保证消息边界）；非 2xx 响应直接
抛 `ApiError` 而不进入流式解析分支。全程 mock `globalThis.fetch`，无真实网络。
