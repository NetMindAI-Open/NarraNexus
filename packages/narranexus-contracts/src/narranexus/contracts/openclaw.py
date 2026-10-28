"""
@file_name: openclaw.py
@author: Bin Liang
@date: 2026-09-07
@description: The OpenClaw ecosystem's names — one vocabulary shared by the skill format and the migration scanner.

The project was Clawdbot, then Clawdis, then Moltbot, now OpenClaw. The same
tuple is the ``metadata.<name>`` key a SKILL.md may use, the ``~/.<name>``
home dir and the ``<name>.json`` config file the migration scanner probes;
``builtin.skills`` and ``narranexus.platform.migration`` both derive from it,
so a rename is one edit here. Order = lookup precedence (current name first).
It lives in contracts because the two consumers are different subsystems and
neither should define the other's vocabulary.
"""
from __future__ import annotations

OPENCLAW_ALIASES: tuple[str, ...] = ("openclaw", "clawdbot", "clawdis", "moltbot")

__all__ = ["OPENCLAW_ALIASES"]
