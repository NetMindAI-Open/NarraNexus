"""
@file_name: test_team_default_responder.py
@date: 2026-07-21
@description: A team-chat message with no @mention routes to the default
responder — the team's lead_agent_id if it's a current member, else the
earliest-joined member (members list is ordered by join time).
"""

from __future__ import annotations

from narranexus.platform.schema.team_schema import resolve_default_responder
from narranexus.platform.schema.team_schema import Team


def _lead(lead=None):
    """The rule takes the lead id, not a Team — one caller holds a model and the
    other a raw row, and making the rule sniff which put a type check inside
    logic that has nothing to do with types."""
    return lead


def test_lead_when_set_and_member():
    assert resolve_default_responder(_lead("agent_b"), ["agent_a", "agent_b"]) == "agent_b"


def test_earliest_when_lead_unset():
    # members ordered by join time → first is earliest.
    assert resolve_default_responder(_lead(None), ["agent_a", "agent_b"]) == "agent_a"


def test_earliest_when_lead_not_a_member():
    # Lead was removed from the team → fall back, don't return a ghost.
    assert resolve_default_responder(_lead("gone"), ["agent_a", "agent_b"]) == "agent_a"


def test_single_member_team_auto_responds():
    assert resolve_default_responder(_lead(None), ["only_agent"]) == "only_agent"


def test_empty_team_returns_none():
    assert resolve_default_responder(_lead("x"), []) is None
