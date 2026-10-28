---
code_file: src/xyz_agent_context/utils/db/sqlite_proxy_server.py
last_verified: 2026-09-04
stub: false
---

# src/xyz_agent_context/utils/db/sqlite_proxy_server.py — entrypoint shim (one release, batch 6a)

The deploy repo's compose, older desktop builds and the gate scripts launch this path / `-m` name; it delegates to `narranexus.platform.utils.db.sqlite_proxy_server.main()`. Removed together with the `xyz_agent_context` alias package next release (see `docs/PLUGIN_BATCH6_DEPLOY_LOCKSTEP.md`).
