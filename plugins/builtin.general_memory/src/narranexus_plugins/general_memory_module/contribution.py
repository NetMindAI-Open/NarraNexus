"""
@file_name: contribution.py
@author: Bin Liang
@date: 2026-09-07
@description: What ``builtin.general_memory`` contributes to the host, as the objects its manifest names. The plugin owns this table; the platform holds no list of builtins.
"""
from __future__ import annotations

from narranexus.platform.channel.contributions import module_contribution

PLUGIN_ID = "builtin.general_memory"
MODULES = (module_contribution("narranexus_plugins.general_memory_module.general_memory_module:GeneralMemoryModule", PLUGIN_ID, channel=False),)

__all__ = ["MODULES", "PLUGIN_ID"]
