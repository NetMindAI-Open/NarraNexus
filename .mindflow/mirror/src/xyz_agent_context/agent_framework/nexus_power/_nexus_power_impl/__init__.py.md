---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/__init__.py
last_verified: 2026-07-29
stub: false
---
# _nexus_power_impl — 私有实现层入口

五个高内聚组(harness/prompts/modeling/tooling/session)+ loop.py + event_adapter.py。组间禁止互相 import,共享类型上提 contracts(铁律 #23 私有实现不 re-export)。
