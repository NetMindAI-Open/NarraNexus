"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-04
@description: Public facade of the `builtin.teams` plugin package — what another plugin or a distribution may import: the marketplace service, the template model and the seed. Everything else in the package is private; the host reaches the plugin through its manifest contributions (routes, worker, hooks).
"""
from __future__ import annotations

from narranexus_plugins.teams.marketplace_schema import TeamTemplate
from narranexus_plugins.teams.marketplace_seed import seed_team_marketplace
from narranexus_plugins.teams.marketplace_service import TeamMarketplaceService, store_key_for

PLUGIN_ID = "builtin.teams"
PACKAGE = "narranexus_plugins.teams"

__all__ = ["PACKAGE", "PLUGIN_ID", "TeamMarketplaceService", "TeamTemplate", "seed_team_marketplace", "store_key_for"]
