---
code_file: frontend/src/stores/__tests__/chatStore.blankReply.test.ts
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 — session 侧空白回复守卫用例

钉住 chatStore stopStreaming 回复提取的空白过滤：纯空白 send_message
content 走占位文案分支，与后端落库守卫同口径，防「当场有空气泡、刷新
后消失」漂移（getUserVisibleResponse 用例已随死方法删除）。构造事件时
timestamp 必须互异——currentToolCalls 按 tool_name+timestamp 去重，同
时间戳的第二条会被当重复吞掉（本文件第一版踩过）。
