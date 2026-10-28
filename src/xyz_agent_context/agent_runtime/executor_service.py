"""
@file_name: executor_service.py
@author: Bin Liang
@date: 2026-09-07
@description: Entrypoint shim (one release) for ``python -m xyz_agent_context.agent_runtime.executor_service`` — the code now lives in ``narranexus.platform.agent_runtime.executor_service``.
"""
from narranexus.platform.agent_runtime.executor_service import main

if __name__ == "__main__":
    main()
