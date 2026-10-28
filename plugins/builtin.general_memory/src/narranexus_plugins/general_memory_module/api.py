"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-04
@description: Public facade of the `builtin.general_memory` plugin package — what another plugin or a distribution may import.

Everything else under `narranexus_plugins.general_memory_module` is private to the plugin; the
manifest (`narranexus-plugin.json`) names the contribution constants the host
registers.
"""
from __future__ import annotations

from narranexus.platform.module_system.registry import module_class_for

PLUGIN_ID = "builtin.general_memory"
PACKAGE = "narranexus_plugins.general_memory_module"


def module_class() -> type:
    """The plugin's module class (the agent.capabilities.modules entry this plugin registered at boot)."""
    return module_class_for(PLUGIN_ID)


__all__ = ["PACKAGE", "PLUGIN_ID", "module_class"]
