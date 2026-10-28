"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-04
@description: Public facade of the `builtin.providers` plugin package — what another plugin or a distribution may import.
"""
from __future__ import annotations

PLUGIN_ID = "builtin.providers"
PACKAGE = "narranexus_plugins.providers"

__all__ = ["PACKAGE", "PLUGIN_ID"]
