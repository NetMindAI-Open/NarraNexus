"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-04
@description: Public facade of the `builtin.auth.local` plugin package: the provider class and the header it reads.
"""
from __future__ import annotations

from narranexus_plugins.auth_local.provider import HEADER, LocalAuthProvider

PLUGIN_ID = "builtin.auth.local"
PACKAGE = "narranexus_plugins.auth_local"

__all__ = ["HEADER", "PACKAGE", "PLUGIN_ID", "LocalAuthProvider"]
