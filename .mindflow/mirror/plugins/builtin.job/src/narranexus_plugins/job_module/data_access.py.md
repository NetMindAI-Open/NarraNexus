---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/data_access.py
last_verified: 2026-09-07
stub: false
---

# job_module/data_access.py — this builtin's AgentDataStore bodies

## Intent

Provides `agent.capabilities.data_access` contributions: the seven job read/write bodies over the shared `_job_reads` / `_job_writes` helpers the jobs twin route calls too; `job_update` fans `fields` out as kwargs. Module internals are imported inside each handler so registering the contribution (at `register_all` / manifest load) costs nothing; the parity rejects, clamps and never-raise wrapping stay in `DirectStore`.

## 2026-09-07 — docstring 去掉 register_all（round-2）

数据访问贡献只由 manifest 在 boot 注册。
