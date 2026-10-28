"""
@file_name: contribution.py
@author: Bin Liang
@date: 2026-09-07
@description: What ``builtin.social_network`` contributes to the host, as the objects its manifest names. The plugin owns this table; the platform holds no list of builtins.
"""
from __future__ import annotations

from narranexus.platform.channel.contributions import module_contribution

PLUGIN_ID = "builtin.social_network"
MODULES = (module_contribution("narranexus_plugins.social_network_module.social_network_module:SocialNetworkModule", PLUGIN_ID, channel=False),)

__all__ = ["MODULES", "PLUGIN_ID"]
