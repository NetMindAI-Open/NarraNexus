"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-04
@description: Public facade of builtin.frameworks.codex_cli.
"""
from __future__ import annotations

PLUGIN_ID = "builtin.frameworks.codex_cli"
PACKAGE = "narranexus_plugins.frameworks_codex_cli"


def driver_class():
    from narranexus_plugins.frameworks_codex_cli.official_sdk import CodexSDKv2

    return CodexSDKv2


__all__ = ["PACKAGE", "PLUGIN_ID", "driver_class"]
