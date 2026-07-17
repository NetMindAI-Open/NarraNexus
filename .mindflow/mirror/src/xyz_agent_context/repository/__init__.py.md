---
code_file: src/xyz_agent_context/repository/__init__.py
last_verified: 2026-07-13
stub: false
---
# repository/__init__.py — repository 包的集中导出门面

## 为什么存在

集中 re-export 数据访问层的所有 Repository（Event / Narrative / SocialNetwork / Job /
Inbox / Agent / AgentMessage / MCP / User / Instance / Team / SkillArchive 等,都继承
`BaseRepository`），让别处统一 `from xyz_agent_context.repository import XxxRepository`,
无需记住每个 repo 住在哪个子文件。也顺带 re-export 几个常用 schema 实体（`Agent` /
`User` / `MCPUrl` 等)方便调用方。新增 repo = 在这里加一行 import + 补进 `__all__`。

## 2026-07-13 — 导出 AgentCircuitBreakerRepository

新增 re-export `AgentCircuitBreakerRepository`（实时层 Agent 熔断器状态的数据访问,表
`instance_agent_circuit_breaker`）。纯导出改动,无行为变化。见
[`agent_circuit_breaker_repository.py`](agent_circuit_breaker_repository.py.md)。
