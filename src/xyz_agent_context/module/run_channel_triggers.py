"""
@file_name: run_channel_triggers.py
@author: Bin Liang
@date: 2026-09-07
@description: Entrypoint shim (one release) for ``python -m xyz_agent_context.module.run_channel_triggers`` — the code now lives in ``narranexus.platform.module_system.run_channel_triggers``.
"""
from narranexus.platform.module_system.run_channel_triggers import main, start_channel_triggers  # noqa: F401 — the supervisor gate greps for start_channel_triggers

if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
