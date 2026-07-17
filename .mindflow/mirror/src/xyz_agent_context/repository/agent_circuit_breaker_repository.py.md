---
code_file: src/xyz_agent_context/repository/agent_circuit_breaker_repository.py
last_verified: 2026-07-13
stub: false
---
# agent_circuit_breaker_repository.py — 熔断器状态数据访问

## 为什么存在

`instance_agent_circuit_breaker`（每 agent 一行）的 CRUD。熔断器服务
（`agent_framework/agent_circuit_breaker.py`）拥有全部升级逻辑，这一层只读写行。

## 上下游关系

被 `agent_circuit_breaker` 服务和 `backend/routes/agents_circuit_breaker.py`（GET 状态）
调用。继承 `BaseRepository[AgentCircuitBreaker]`，`id_field="agent_id"`。

## 设计决策

`upsert_state(agent_id, updates)` 是主力：按 agent_id 的**部分**写入（只动 updates 里的
键 + 刷新 updated_at），存在则 update，否则 insert（补 agent_id）。`find_by_status` 供
`reset_for_owner` 拉出 paused/cooling 行再按 owner 过滤。`_row_to_entity` 直接
`AgentCircuitBreaker(**row)`——pydantic 忽略多余的 `id` 列、把 ISO 字符串/枚举串强制成
模型类型。
