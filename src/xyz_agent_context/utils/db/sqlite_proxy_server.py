"""
@file_name: sqlite_proxy_server.py
@author: Bin Liang
@date: 2026-09-04
@description: Entrypoint shim (one release) for ``python -m xyz_agent_context.utils.db.sqlite_proxy_server`` — the proxy now lives in ``narranexus.platform.utils.db.sqlite_proxy_server``.
"""
from narranexus.platform.utils.db.sqlite_proxy_server import app, main  # noqa: F401 — ``app`` for uvicorn import strings

if __name__ == "__main__":
    raise SystemExit(main())
