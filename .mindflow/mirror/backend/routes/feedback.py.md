---
code_file: backend/routes/feedback.py
last_verified: 2026-07-10
stub: false
---

# feedback.py — web UI 反馈中继路由

## Why it exists

前端反馈弹窗不直连团队接收端，而是 POST /api/feedback 由后端经
[[feedback_client.py]] 转发：无 CORS 面、NARRANEXUS_FEEDBACK_DISABLED 服务端
统一生效、user_id 取自会话不可伪造。

## Gotcha

- 用户手写的 text 会原样出境（用户是写给团队看的），与 agent 路径"只送
  摘要"不同——这是有意区分。
- 返回恒 ok=True；delivered=false 仅表示接收端不可达/杀开关，前端不据此报错。
- local 模式 get_current_user 返回 None → 从 query user_id 兜底（沿用后端
  local 模式惯例）。
