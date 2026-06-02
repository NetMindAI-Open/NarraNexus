"""
@file_name: test_bus_anchor.py
@author: Bin Liang
@date: 2026-06-01
@description: Message-bus retrieval-anchor builder — keeps only peer message
bodies, stripping the per-turn Owner-Relay boilerplate + Time metadata that
made bus the only real 400 source in prod.

Design: reference/self_notebook/specs/2026-06-01-embedding-anchor-redesign-design.md
"""
from __future__ import annotations

from xyz_agent_context.message_bus.message_bus_trigger import build_bus_anchor


class _Msg:
    """Duck-typed BusMessage (only from_agent / content are read)."""
    def __init__(self, from_agent, content):
        self.from_agent = from_agent
        self.content = content
        self.created_at = "2026-06-01T00:00:00Z"


def test_bus_anchor_keeps_only_peer_bodies():
    a = build_bus_anchor([_Msg("agent_a", "hello"), _Msg("agent_b", "world")])
    assert a == "[From agent agent_a] hello\n[From agent agent_b] world"


def test_bus_anchor_strips_template_and_metadata():
    a = build_bus_anchor([_Msg("agent_a", "ping")])
    assert "Owner Relay" not in a
    assert "Time:" not in a
    assert "[Message Bus" not in a


def test_bus_anchor_empty_list():
    assert build_bus_anchor([]) == ""
