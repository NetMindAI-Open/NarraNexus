"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-04
@description: Public facade of builtin.llm_clients: the one-shot CLI helpers other plugins run (the OAuth provider drivers).
"""
from __future__ import annotations

from narranexus_plugins.llm_clients.cli_oneshot import oneshot_cwd, run_codex_cli_oneshot

PLUGIN_ID = "builtin.llm_clients"
PACKAGE = "narranexus_plugins.llm_clients"

__all__ = ["PACKAGE", "PLUGIN_ID", "oneshot_cwd", "run_codex_cli_oneshot"]
