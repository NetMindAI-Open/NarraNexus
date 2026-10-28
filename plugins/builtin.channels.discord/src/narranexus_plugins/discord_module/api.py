"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-04
@description: Public facade of the `builtin.channels.discord` plugin package — what another plugin or a distribution may import.

Everything else under `narranexus_plugins.discord_module` is private to the plugin; the
manifest (`narranexus-plugin.json`) names the contribution constants the host
registers.
"""
from __future__ import annotations

from narranexus.platform.module_system.registry import module_class_for

PLUGIN_ID = "builtin.channels.discord"
PACKAGE = "narranexus_plugins.discord_module"


def module_class() -> type:
    """The plugin's module class (the agent.capabilities.modules entry this plugin registered at boot)."""
    return module_class_for(PLUGIN_ID)


__all__ = ["PACKAGE", "PLUGIN_ID", "module_class"]
