"""
@file_name: run_worker_supervisor.py
@author: Bin Liang
@date: 2026-09-04
@description: Entrypoint shim (one release) for ``python -m xyz_agent_context.module.run_worker_supervisor`` — the consolidated worker supervisor now lives in ``narranexus.platform.module_system.run_worker_supervisor``.
"""
from narranexus.platform.module_system.run_worker_supervisor import main, start_channel_triggers  # noqa: F401 — the deploy gate (check_trigger_alignment.sh) greps this file for start_channel_triggers

if __name__ == "__main__":
    raise SystemExit(main())
