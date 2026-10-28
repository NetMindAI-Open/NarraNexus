"""
@file_name: data_access.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.basic_info's AgentDataStore bodies: view_narrative / view_event / switch_narrative.

Registered into ``agent.capabilities.data_access`` by the builtin.basic_info manifest
Handlers take the
store's db client first; module internals are imported inside each handler so
registering the contribution stays free of the module's import cost.
"""
from __future__ import annotations

from typing import Any, Optional

from narranexus.contracts.data_access import DataAccessSpec
from narranexus.kernel.plugins.registry import Contribution


async def view_narrative(db: Any, agent_id: str, narrative_id: str) -> dict:
    from narranexus_plugins.basic_info_module import fetch_narrative_view

    return await fetch_narrative_view(db, agent_id, narrative_id)


async def view_event(db: Any, agent_id: str, event_id: str) -> dict:
    from narranexus_plugins.basic_info_module import fetch_event_view

    return await fetch_event_view(db, agent_id, event_id)


async def switch_narrative(db: Any, agent_id: str, narrative_id: str) -> dict:
    from narranexus_plugins.basic_info_module import check_narrative_switch

    return await check_narrative_switch(db, agent_id, narrative_id)


_HANDLERS = {"view_narrative": view_narrative, "view_event": view_event, "switch_narrative": switch_narrative}
DATA_ACCESS = tuple(Contribution(n, (lambda n=n, h=h: DataAccessSpec(n, h))) for n, h in _HANDLERS.items())

__all__ = ["DATA_ACCESS", *_HANDLERS]
