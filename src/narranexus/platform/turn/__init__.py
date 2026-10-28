"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-07
@description: The turn pipeline seams the platform owns: profile selection and the bound pipeline lookup.

The pipeline implementation itself is a plugin contribution
(``builtin.turn`` → ``narranexus_plugins.turn.pipeline``); the platform never
imports it, it asks the binding (``turn_pipeline_for``). Importing this package
registers nothing.
"""
from narranexus.platform.turn.pipeline import (
    PIPELINE_SLOT,
    PROFILES_SLOT,
    resolve_profile,
    turn_pipeline_for,
    turn_when_context,
)

__all__ = [
    "PIPELINE_SLOT",
    "PROFILES_SLOT",
    "resolve_profile",
    "turn_pipeline_for",
    "turn_when_context",
]
