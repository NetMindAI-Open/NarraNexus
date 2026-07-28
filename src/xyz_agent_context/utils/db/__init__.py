"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-07-24
@description: Database layer utilities, grouped out of the utils/ grab bag.

Everything that talks to the database engine lives here: the async client
(`database`), per-dialect backends (`db_backend*`), the global client
factory (`db_factory`), the schema registry + auto_migrate
(`schema_registry`), the N+1-solving `dataloader`, and the desktop
`sqlite_proxy_server` (run.sh / Makefile entrypoint
`xyz_agent_context.utils.db.sqlite_proxy_server`).

No re-exports: consumers import modules explicitly (the historical
re-exports in `utils/__init__` keep working — they import from here).
"""
