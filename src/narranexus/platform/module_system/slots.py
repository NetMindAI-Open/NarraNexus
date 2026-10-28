"""
@file_name: slots.py
@author: Bin Liang
@date: 2026-09-07
@description: The slot paths the module system reads (modules, triggers, data access, channels). Names only — what fills them is each plugin's manifest; the platform holds no table of plugins.
"""
from __future__ import annotations

MODULES_SLOT = "agent.capabilities.modules"
TRIGGERS_SLOT = "ingress.triggers"
DATA_ACCESS_SLOT = "agent.capabilities.data_access"
CHANNELS_SLOT = "ingress.channels"

__all__ = ["CHANNELS_SLOT", "DATA_ACCESS_SLOT", "MODULES_SLOT", "TRIGGERS_SLOT"]
