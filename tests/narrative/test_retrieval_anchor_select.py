"""
@file_name: test_retrieval_anchor_select.py
@author: Bin Liang
@date: 2026-06-01
@description: narrative retrieval embeds the clean anchor when a trigger
provided one, else falls back to raw input_content (capped by the token guard).

Design: reference/self_notebook/specs/2026-06-01-embedding-anchor-redesign-design.md
"""
from __future__ import annotations

from xyz_agent_context.narrative.narrative_service import resolve_retrieval_text


def test_anchor_used_when_present():
    assert resolve_retrieval_text("[From Bob] hi", "full execution prompt …") == "[From Bob] hi"


def test_falls_back_to_input_when_anchor_none():
    assert resolve_retrieval_text(None, "the raw input") == "the raw input"


def test_falls_back_when_anchor_blank():
    assert resolve_retrieval_text("", "the raw input") == "the raw input"
    assert resolve_retrieval_text("   ", "the raw input") == "the raw input"
