"""
@file_name: contribution.py
@author:
@date: 2026-09-22
@description: What ``builtin.browser`` contributes to the host, as the objects its manifest names.
"""
from __future__ import annotations

from narranexus.platform.channel.contributions import module_contribution

PLUGIN_ID = "builtin.browser"
MODULES = (
    module_contribution(
        "narranexus_plugins.browser_module.browser_module:BrowserModule",
        PLUGIN_ID,
        channel=False,
    ),
)

__all__ = ["MODULES", "PLUGIN_ID"]
