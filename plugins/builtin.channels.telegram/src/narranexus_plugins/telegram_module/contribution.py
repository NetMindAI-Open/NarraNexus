"""
@file_name: contribution.py
@author: Bin Liang
@date: 2026-09-07
@description: What ``builtin.channels.telegram`` contributes to the host, as the objects its manifest names. The plugin owns this table; the platform holds no list of builtins.
"""
from __future__ import annotations

from narranexus.platform.channel.contributions import contributions_from
from narranexus_plugins.telegram_module.descriptor import DESCRIPTOR

PLUGIN_ID = "builtin.channels.telegram"
# The descriptor is the one place the channel names its module and trigger classes.
MODULES, TRIGGERS = contributions_from(DESCRIPTOR, PLUGIN_ID)

__all__ = ["MODULES", "PLUGIN_ID", "TRIGGERS"]
