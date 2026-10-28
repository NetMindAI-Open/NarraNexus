"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-03
@description: Nexus_Plugins_Module — the builtin module through which an Agent extends its own instance with plugins (spec §11).
"""
from narranexus_plugins.nexus_plugins_module.nexus_plugins_module import NexusPluginsModule

__all__ = ["NexusPluginsModule"]
