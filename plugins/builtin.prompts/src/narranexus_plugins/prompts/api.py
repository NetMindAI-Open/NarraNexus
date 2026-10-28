"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-07
@description: Public facade of the `builtin.prompts` plugin package: the default section classes and assembler, for a distribution that wants to subclass or reuse them.
"""
from __future__ import annotations

from narranexus_plugins.prompts.assembler import DefaultPromptAssembler
from narranexus_plugins.prompts.sections import (
    BootstrapSection,
    ModulesSection,
    NarrativeSection,
    SecuritySection,
    TemporalSection,
)

PLUGIN_ID = "builtin.prompts"
PACKAGE = "narranexus_plugins.prompts"

__all__ = ["BootstrapSection", "DefaultPromptAssembler", "ModulesSection", "NarrativeSection", "PACKAGE", "PLUGIN_ID", "SecuritySection", "TemporalSection"]
