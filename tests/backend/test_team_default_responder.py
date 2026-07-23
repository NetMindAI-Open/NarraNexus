"""
@file_name: test_team_default_responder.py
@date: 2026-07-21
@description: A team-chat message with no @mention routes to the default
responder — the team's lead_agent_id if it's a current member, else the
earliest-joined member (members list is ordered by join time).
"""

from __future__ import annotations

from backend.routes.teams import _resolve_default_responder
from xyz_agent_context.schema.team_schema import Team


def _team(lead=None):
    return Team(team_id="team_x", owner_user_id="u", name="T", lead_agent_id=lead)


def test_lead_when_set_and_member():
    assert _resolve_default_responder(_team("agent_b"), ["agent_a", "agent_b"]) == "agent_b"


def test_earliest_when_lead_unset():
    # members ordered by join time → first is earliest.
    assert _resolve_default_responder(_team(None), ["agent_a", "agent_b"]) == "agent_a"


def test_earliest_when_lead_not_a_member():
    # Lead was removed from the team → fall back, don't return a ghost.
    assert _resolve_default_responder(_team("gone"), ["agent_a", "agent_b"]) == "agent_a"


def test_single_member_team_auto_responds():
    assert _resolve_default_responder(_team(None), ["only_agent"]) == "only_agent"


def test_empty_team_returns_none():
    assert _resolve_default_responder(_team("x"), []) is None
