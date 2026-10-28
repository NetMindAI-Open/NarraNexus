"""
@file_name: data_access.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.awareness's AgentDataStore bodies: update_awareness / update_agent_profile.

Registered into ``agent.capabilities.data_access`` by the builtin.awareness manifest
Handlers take the
store's db client first; module internals are imported inside each handler so
registering the contribution stays free of the module's import cost.
"""
from __future__ import annotations

from typing import Any, Optional

from narranexus.contracts.data_access import DataAccessSpec
from narranexus.kernel.plugins.registry import Contribution


async def update_awareness(db: Any, instance_id: str, awareness: str) -> None:
    """Rewrite the awareness of the agent's AwarenessModule instance (the store resolved ``instance_id``)."""
    from narranexus_plugins.awareness_module import carry_over_platform_record
    from narranexus.platform.repository import InstanceAwarenessRepository

    repo = InstanceAwarenessRepository(db)
    # The model rewrites the WHOLE profile here, and the format it is given
    # does not include the platform's identity record — so a rewrite silently
    # deleted the rename correction. Re-attached in code, because asking the
    # model to keep it would make prompt text the mechanism (rule #15).
    current = await repo.get_by_instance(instance_id)
    awareness = carry_over_platform_record((current.awareness if current else "") or "", awareness)
    await repo.upsert(instance_id, awareness)


async def update_agent_profile(db: Any, agent_id: str, new_name: Optional[str], new_description: Optional[str]) -> str:
    # The whole rename transaction (name/description + identity-note correction
    # + same-owner clash note + discovery refresh) is the shared
    # update_agent_profile_from_args; the backend twin route calls the SAME
    # function, so the two paths return byte-identical strings.
    from narranexus_plugins.awareness_module import update_agent_profile_from_args

    return await update_agent_profile_from_args(db, agent_id, new_name=new_name, new_description=new_description)


DATA_ACCESS = (
    Contribution("update_awareness", lambda: DataAccessSpec("update_awareness", update_awareness)),
    Contribution("update_agent_profile", lambda: DataAccessSpec("update_agent_profile", update_agent_profile)),
)

__all__ = ["DATA_ACCESS", "update_agent_profile", "update_awareness"]
