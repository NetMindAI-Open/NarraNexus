---
code_file: plugins/builtin.social_network/src/narranexus_plugins/social_network_module/data_access.py
last_verified: 2026-09-07
stub: false
---

# social_network_module/data_access.py — this builtin's AgentDataStore bodies

## Intent

Provides `agent.capabilities.data_access` contributions: the six entity/search/stats bodies plus create_agent; `_resolve` is the old `DirectStore._social_module` (instance lookup + temp module), returning the seam's `message`-shaped failure dicts. Module internals are imported inside each handler so registering the contribution (at `register_all` / manifest load) costs nothing; the parity rejects, clamps and never-raise wrapping stay in `DirectStore`.

## 2026-09-07 — docstring 去掉 register_all（round-2）

数据访问贡献只由 manifest 在 boot 注册。
