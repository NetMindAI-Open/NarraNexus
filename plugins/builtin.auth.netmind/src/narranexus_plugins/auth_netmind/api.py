"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-04
@description: Public facade of the `builtin.auth.netmind` plugin package: the provider class.
"""
from __future__ import annotations

from narranexus_plugins.auth_netmind.provider import NetMindAuthProvider

PLUGIN_ID = "builtin.auth.netmind"
PACKAGE = "narranexus_plugins.auth_netmind"

__all__ = ["PACKAGE", "PLUGIN_ID", "NetMindAuthProvider"]
