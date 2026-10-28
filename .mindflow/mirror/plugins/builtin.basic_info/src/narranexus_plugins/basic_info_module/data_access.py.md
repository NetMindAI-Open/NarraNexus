---
code_file: plugins/builtin.basic_info/src/narranexus_plugins/basic_info_module/data_access.py
last_verified: 2026-09-07
stub: false
---

# basic_info_module/data_access.py — this builtin's AgentDataStore bodies

## Intent

Provides `agent.capabilities.data_access` contributions: view_narrative / view_event / switch_narrative over the shared `_narrative_reads` helpers the narrative twin route calls too. Module internals are imported inside each handler so registering the contribution (at `register_all` / manifest load) costs nothing; the parity rejects, clamps and never-raise wrapping stay in `DirectStore`.

## 2026-09-07 — docstring 去掉 register_all（round-2）

数据访问贡献只由 manifest 在 boot 注册。
