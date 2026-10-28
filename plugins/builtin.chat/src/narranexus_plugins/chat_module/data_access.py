"""
@file_name: data_access.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.chat's AgentDataStore body: get_chat_history.

Registered into ``agent.capabilities.data_access`` by the builtin.chat manifest
Handlers take the
store's db client first; module internals are imported inside each handler so
registering the contribution stays free of the module's import cost.
"""
from __future__ import annotations

from typing import Any, Optional

from narranexus.contracts.data_access import DataAccessSpec
from narranexus.kernel.plugins.registry import Contribution


async def get_chat_history(db: Any, agent_id: str, instance_id: str, limit: int) -> dict:
    from narranexus_plugins.chat_module import fetch_chat_history

    return await fetch_chat_history(db, agent_id, instance_id, limit)


DATA_ACCESS = (Contribution("get_chat_history", lambda: DataAccessSpec("get_chat_history", get_chat_history)),)

__all__ = ["DATA_ACCESS", "get_chat_history"]
