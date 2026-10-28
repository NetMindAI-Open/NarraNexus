"""
@file_name: id_schema.py
@author: NetMind.AI
@date: 2026-05-08
@description: ID type registry — single source of truth (PRD §8.11 Layer 1)

Each entry: { kind_name: regex_pattern_string }.
Used by Layer 4 free-text rewrite to detect any ID-shaped substring.
"""

import re
from typing import Pattern


ID_KINDS: dict[str, str] = {
    "agent":     r"agent_[0-9a-f]{8,16}",
    "event":     r"evt_[0-9a-f]{8,16}",
    "narrative": r"nar_[0-9a-f]{8,16}",
    "instance":  r"inst_[0-9a-f]{8,16}",
    "message":   r"msg_[0-9a-f]{8,16}",
    "job":       r"job_[0-9a-f]{8,16}",
    "team":      r"team_[0-9a-f]{8,16}",
    "channel":   r"ch_[0-9a-f]{8,16}",
    "mcp":       r"mcp_[0-9a-f]{8,16}",
    # Artifact registration mints art_<8-hex> via secrets.token_hex(4); the
    # bundle's gen_new_id mints <12-hex>. Range 8..16 covers both.
    "artifact":  r"art_[0-9a-f]{8,16}",
}


def build_all_id_regex() -> Pattern[str]:
    """Compose all kinds into one alternation pattern."""
    return re.compile("|".join(f"(?:{p})" for p in ID_KINDS.values()))
