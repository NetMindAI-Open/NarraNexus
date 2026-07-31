---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/tooling/policy.py
last_verified: 2026-07-29
stub: false
---
# tooling/policy — fail-closed 策略引擎

有序 layer、deny 永远赢、layer 崩溃即拒(多租户自研决策,业界无背书,Codex fail-open 是反面)。WorkspaceConfinement 只查内建工具路径参数(mcp__ 的副作用在服务端),拒绝并点名路径,不静默改写。
