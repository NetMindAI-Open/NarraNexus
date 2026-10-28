"""
@file_name: plugin_hooks.py
@author: Bin Liang
@date: 2026-09-04
@description: ``backend.hooks`` implementations the builtin.chat plugin ships.

The platform does not import ChatModule: when step 1 of a bootstrapping
agent's first turn resolves a greeting it fires ``onDidResolveBootstrapGreeting``
and whoever owns chat history — this plugin — seeds it into the head chat
instance. With builtin.chat disabled the hook has no implementation and the
turn simply carries on without a seeded greeting.
"""
from __future__ import annotations

from typing import Any

from narranexus.kernel.plugins.hooks import hookimpl


@hookimpl("onDidResolveBootstrapGreeting")
async def seed_greeting(agent_id: str, user_id: str, instance_id: str, greeting: str, turn_started_at: Any) -> bool:
    from narranexus_plugins.chat_module import seed_bootstrap_greeting
    from narranexus.platform.utils.db.db_factory import get_db_client

    db = await get_db_client()
    return bool(await seed_bootstrap_greeting(db, agent_id, user_id, instance_id, greeting, turn_started_at))


HOOKS = (seed_greeting,)

__all__ = ["HOOKS", "seed_greeting"]
