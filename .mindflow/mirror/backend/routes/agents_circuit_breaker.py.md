---
code_file: backend/routes/agents_circuit_breaker.py
last_verified: 2026-07-13
stub: false
---
# agents_circuit_breaker.py — 熔断器状态查询 + 手动"恢复"路由

## 为什么存在

实时层熔断器会在换 key 后自动恢复；这个路由是**手动**恢复的另一半——owner 的"Resume"
按钮。两个端点：`GET /{agent_id}/circuit-breaker`（查状态，无行则合成 active）、
`POST /{agent_id}/circuit-breaker/reset`（清 PAUSE 回 active，幂等）。

## 上下游关系

挂在 `/api/agents` 聚合路由下（agents.py `include_router`）。GET 读
`AgentCircuitBreakerRepository`；reset 调 `agent_circuit_breaker.reset_agent`。前端
`api.getAgentCircuitBreaker` / `resetAgentCircuitBreaker` 消费，App.tsx 的熔断横幅
"Resume agent" 按钮触发 reset。

## 设计决策

按 viewer 租户隔离，完全照搬 `agents_bus_failures.py`：viewer_id 只从 session 取
（`?user_id=` 直接 400 拒绝），非 owner 一律 404（同时掩盖"无此 agent"与"不是你的"）。
