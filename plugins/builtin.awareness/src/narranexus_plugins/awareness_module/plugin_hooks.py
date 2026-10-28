"""
@file_name: plugin_hooks.py
@author: Bin Liang
@date: 2026-09-04
@description: ``backend.hooks`` implementations the builtin.awareness plugin ships (identity record upkeep).

The rename transaction (agent_profile) and the bundle importer used to import
awareness's identity-record writers. They now fire host events —
``onDidChangeAgentName`` (record the change) and ``onDidSettleAgentName``
(reconcile a profile that asserts another name) — and these hooks do the
writes on the caller's db client. With builtin.awareness disabled nothing
listens and the callers' "None = no awareness instance" contract holds.
"""
from __future__ import annotations

from typing import Any, Optional


from narranexus.kernel.plugins.hooks import hookimpl


async def _db_or_global(db: Any):
    if db is not None:
        return db
    from narranexus.platform.utils.db.db_factory import get_db_client

    return await get_db_client()


@hookimpl("onDidChangeAgentName")
async def record_name_change(db: Any, agent_id: str, old_name: str, new_name: str) -> Optional[bool]:
    from narranexus_plugins import awareness_module

    return await awareness_module.record_identity_change(await _db_or_global(db), agent_id, old_name, new_name)


@hookimpl("onDidSettleAgentName")
async def reconcile_identity(db: Any, agent_id: str, name: str) -> Optional[bool]:
    from narranexus_plugins import awareness_module

    return await awareness_module.reconcile_identity_record(await _db_or_global(db), agent_id, name)


HOOKS = (record_name_change, reconcile_identity)

__all__ = ["HOOKS", "record_name_change", "reconcile_identity"]
