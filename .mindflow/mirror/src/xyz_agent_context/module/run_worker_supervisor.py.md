---
code_file: src/xyz_agent_context/module/run_worker_supervisor.py
last_verified: 2026-09-07
stub: false
---

# src/xyz_agent_context/module/run_worker_supervisor.py — entrypoint shim (one release, batch 6a)

The deploy repo's compose, older desktop builds and the gate scripts launch this path / `-m` name; it delegates to `narranexus.platform.module_system.run_worker_supervisor.main()`. Removed together with the `xyz_agent_context` alias package next release (see `docs/PLUGIN_BATCH6_DEPLOY_LOCKSTEP.md`).

## 2026-09-07

Also re-exports `start_channel_triggers`: the deploy gate `check_trigger_alignment.sh` greps this file for that name (it used to be the real supervisor). `python -m` itself now runs the platform module's code through the alias finder; this file matters for by-path launchers and the gate only.
