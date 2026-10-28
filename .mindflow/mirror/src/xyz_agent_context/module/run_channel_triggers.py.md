---
code_file: src/xyz_agent_context/module/run_channel_triggers.py
last_verified: 2026-09-07
stub: false
---

# src/xyz_agent_context/module/run_channel_triggers.py — entrypoint shim

Entrypoint shim (one release, batch 6a). `python -m` no longer needs this file (the alias finder forwards `get_code` to the platform module), but the deploy repo's gate scripts and older launchers reference the path, so a delegating file stays until the alias is removed (`REMOVED_AT` in the package root). Re-exports `start_channel_triggers` because `check_trigger_alignment.sh` asserts the file exists next to the supervisor shim.
