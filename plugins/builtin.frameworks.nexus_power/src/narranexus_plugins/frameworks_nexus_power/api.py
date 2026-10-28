"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-04
@description: Public facade of builtin.frameworks.nexus_power: the driver class and the extension-point registration.
"""
from __future__ import annotations

PLUGIN_ID = "builtin.frameworks.nexus_power"
PACKAGE = "narranexus_plugins.frameworks_nexus_power"


def driver_class():
    from narranexus_plugins.frameworks_nexus_power.adapter.nexus_agent import NexusAgent

    return NexusAgent


__all__ = ["PACKAGE", "PLUGIN_ID", "driver_class"]
