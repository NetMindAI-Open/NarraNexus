"""
@file_name: model_sync_runner.py
@author: Bin Liang
@date: 2026-09-07
@description: Entrypoint shim (one release) for ``python -m xyz_agent_context.services.model_sync_runner`` — the code now lives in ``narranexus.platform.services.model_sync_runner``.
"""
from narranexus.platform.services.model_sync_runner import main

if __name__ == "__main__":
    raise SystemExit(main())
