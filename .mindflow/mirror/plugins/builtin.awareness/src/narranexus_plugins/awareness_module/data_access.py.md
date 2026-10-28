---
code_file: plugins/builtin.awareness/src/narranexus_plugins/awareness_module/data_access.py
last_verified: 2026-09-07
stub: false
---

# awareness_module/data_access.py — this builtin's AgentDataStore bodies

## Intent

Provides `agent.capabilities.data_access` contributions: update_awareness (the store resolves the instance id — a platform query — and the provider does the carry-over + upsert) and update_agent_profile (the shared rename transaction the twin route calls too). Module internals are imported inside each handler so registering the contribution (at `register_all` / manifest load) costs nothing; the parity rejects, clamps and never-raise wrapping stay in `DirectStore`.

## 2026-09-07 — docstring 去掉 register_all（round-2）

数据访问贡献只由 manifest 在 boot 注册。
