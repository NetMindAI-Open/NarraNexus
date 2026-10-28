---
code_file: frontend/src/components/chat/team/TeamMessageFooter.tsx
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — 只剩 chips + 时间戳

推理披露上移到气泡顶部(见 [[TeamMessageProcess]]),本组件不再 import 它。

# TeamMessageFooter — 一条消息底下挂的东西

过程展开、这一轮产出的 artifact、时间戳。

从 [[TeamChatPanel]] 原样搬出，join 逻辑和理由都没变（artifact 按 `event_id` 关联，
**不按时间戳**——时间戳会在最常见的情况下归错：一轮产出两个 artifact、两个 agent 同时回复）。

搬出来是为了让 [[TeamMessageBubble]] 只拥有「消息长什么样」，不必同时拥有「房间对这条消息还知道什么」。
