---
code_file: packages/narranexus-contracts/src/narranexus/contracts/agent/agent_spec.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — `AgentSpec`：Agent = 人设 + 能力集 + profile + 模型

团队模板、bundle 导入导出、设置页、Nexus_Plugins_Module 的「给这个 Agent 加能力」都应操作这一个
值。`CapabilitySet.is_enabled(name, default)`：显式 disabled 优先，其次显式 enabled，否则按能力的
`always_load` 默认——这就是 §10.3「新装插件的 module 默认不对既有 Agent 启用」的数据形状。
批 1 只立类型（普通 frozen dataclass，位置参数可用），DB 行到 AgentSpec 的装配在批 3。
`agent.capabilities` 位的契约符号就是这里的 `CapabilitySet`。
