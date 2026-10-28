"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-04
@description: Public facade of builtin.turn: the pipeline class, the builtin profiles and the default strategies by name.

Names match what the manifest provides (``docs/API_POLICY.md`` §8) rather than
inventing a third vocabulary: ``PROFILES`` / ``STRATEGIES`` / ``CONTRIBUTION``.
"""
from __future__ import annotations

from narranexus_plugins.turn.pipeline import CONTRIBUTION, TurnPipeline
from narranexus_plugins.turn.profiles import PROFILES
from narranexus_plugins.turn.stages import STRATEGIES

PLUGIN_ID = "builtin.turn"
PACKAGE = "narranexus_plugins.turn"

__all__ = ["CONTRIBUTION", "PACKAGE", "PLUGIN_ID", "PROFILES", "STRATEGIES", "TurnPipeline"]
