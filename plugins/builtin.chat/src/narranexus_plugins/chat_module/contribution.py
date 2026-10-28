"""
@file_name: contribution.py
@author: Bin Liang
@date: 2026-09-07
@description: What ``builtin.chat`` contributes to the host, as the objects its manifest names. The plugin owns this table; the platform holds no list of builtins.
"""
from __future__ import annotations

from narranexus.platform.channel.contributions import module_contribution, trigger_contribution
from narranexus.contracts.trigger import TriggerSpec

PLUGIN_ID = "builtin.chat"
MODULES = (module_contribution("narranexus_plugins.chat_module.chat_module:ChatModule", PLUGIN_ID, channel=False),)

# The A2A protocol server (Google Agent-to-Agent) the module runner serves on demand.
TRIGGERS = (trigger_contribution(TriggerSpec("a2a", "narranexus_plugins.chat_module.chat_trigger:A2AServer", host="api")),)

__all__ = ["MODULES", "PLUGIN_ID", "TRIGGERS"]
