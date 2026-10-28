"""
@file_name: plugin_hooks.py
@author: Bin Liang
@date: 2026-09-04
@description: ``backend.hooks`` implementations the builtin.teams plugin ships: on ``onDidStartBackend`` a registry host seeds the team marketplace catalog (templates + store), off the startup critical path. With builtin.teams disabled the hook has no implementation and the backend simply has no team catalog.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from narranexus.kernel.plugins.hooks import hookimpl


@hookimpl("onDidStartBackend")
async def seed_team_marketplace_on_start(db: Any) -> int:
    from narranexus.platform.marketplace.skill_marketplace_service import is_registry_host

    if not is_registry_host():
        return 0  # a pure desktop client proxies to the cloud registry
    from narranexus_plugins.teams.marketplace_seed import seed_team_marketplace

    seeded = await seed_team_marketplace(db)
    logger.info(f"Team Marketplace seed: {seeded} templates present")
    return int(seeded)


HOOKS = (seed_team_marketplace_on_start,)

__all__ = ["HOOKS", "seed_team_marketplace_on_start"]
