"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-04
@description: Public facade of the `builtin.memory_kinds` plugin package — what another plugin or a distribution may import.
"""
from __future__ import annotations

PLUGIN_ID = "builtin.memory_kinds"
PACKAGE = "narranexus_plugins.memory_kinds"

from narranexus_plugins.memory_kinds.specs import CONTRIBUTIONS  # noqa: E402,F401 — the six kind specs

__all__ = ["PACKAGE", "PLUGIN_ID"]
