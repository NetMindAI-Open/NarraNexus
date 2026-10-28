"""
@file_name: test_work_board_room_resolution.py
@author:
@date: 2026-08-10
@description: How a work-board tool learns which team room it is running in.

`test_work_board_tools.py` monkeypatches `_resolve_team_room` wholesale so the
tools' own logic is what it tests. Nothing then covered the resolver itself —
which is how the patrol lane shipped with every board tool dead on it: patrol
opened no `bus_agent_activity` row, the resolver's only source at the time, so
all five tools returned "no room" / "not found" in the one turn the prompt
explicitly asks the lead to call `team_work_complete`.

Pinned here:
  * the server-injected team identity is the FIRST source, so a lane that does
    not write an activity row still resolves (the patrol case)
  * the room's channel is derived from the team, not from where the agent
    happens to be mirrored
  * no injected team and no activity row = no board, never a guess
  * a non-team channel is not a team room
"""
from __future__ import annotations

import contextlib

import pytest

from narranexus_plugins.message_bus_module import _work_board_mcp_tools as mod


class _Headers:
    def __init__(self, d):
        self._d = d

    def get(self, k, default=None):
        return self._d.get(k.lower(), default)


@contextlib.contextmanager
def injected_team(team_id: str | None):
    """Install an ambient MCP request whose header names the caller's team."""
    from mcp.server.lowlevel.server import request_ctx
    from narranexus.platform.module_system._mcp_identity import TEAM_ID_HEADER

    headers = {TEAM_ID_HEADER.lower(): team_id} if team_id else {}
    request = type("Req", (), {"headers": _Headers(headers)})()
    token = request_ctx.set(type("Ctx", (), {"request": request})())
    try:
        yield
    finally:
        request_ctx.reset(token)


async def _make_room(db, team_id: str, channel_id: str) -> None:
    await db.insert(
        "bus_channels",
        {
            "channel_id": channel_id,
            "name": f"team {team_id}",
            "channel_type": "group",
            "created_by": f"team_{team_id}",
        },
    )


@pytest.mark.asyncio
async def test_the_injected_team_resolves_without_an_activity_row(db_client):
    """The patrol case, stated as a test.

    Patrol runs the lead outside the message-dispatch lane. If the resolver
    needs an activity row, every board tool fails in exactly the turn the
    prompt tells the lead to use them.
    """
    await _make_room(db_client, "t1", "ch_1")

    with injected_team("t1"):
        team_id, channel_id = await mod._resolve_team_room(db_client, "agent_lead")

    assert (team_id, channel_id) == ("t1", "ch_1")


@pytest.mark.asyncio
async def test_the_injected_team_wins_over_a_stale_activity_row(db_client):
    """An activity row is a mirror of where the agent was last seen; the
    injected header is what this turn can PROVE. When they disagree the proof
    wins, or a tool could write into the room the agent used to be in."""
    await _make_room(db_client, "t1", "ch_1")
    await _make_room(db_client, "t2", "ch_2")
    await db_client.insert(
        "bus_agent_activity",
        {"agent_id": "agent_lead", "channel_id": "ch_2", "state": "running"},
    )

    with injected_team("t1"):
        assert await mod._resolve_team_room(db_client, "agent_lead") == ("t1", "ch_1")


@pytest.mark.asyncio
async def test_an_uninjected_turn_falls_back_to_the_activity_row(db_client):
    """The message lane still resolves the same way it always did."""
    await _make_room(db_client, "t1", "ch_1")
    await db_client.insert(
        "bus_agent_activity",
        {"agent_id": "agent_lead", "channel_id": "ch_1", "state": "running"},
    )

    with injected_team(None):
        assert await mod._resolve_team_room(db_client, "agent_lead") == ("t1", "ch_1")


@pytest.mark.asyncio
async def test_no_identity_and_no_activity_is_no_board(db_client):
    """A private turn has no board. It must not invent one — the tools decline
    with a reason instead."""
    with injected_team(None):
        assert await mod._resolve_team_room(db_client, "agent_lead") == (None, None)


@pytest.mark.asyncio
async def test_a_peer_dm_is_not_a_team_room(db_client):
    """`created_by` that is not the `team_` marker means an agent owns the
    channel — a DM, which has no board."""
    await db_client.insert(
        "bus_channels",
        {"channel_id": "ch_dm", "name": "dm", "channel_type": "direct",
         "created_by": "agent_a"},
    )
    await db_client.insert(
        "bus_agent_activity",
        {"agent_id": "agent_lead", "channel_id": "ch_dm", "state": "running"},
    )

    with injected_team(None):
        assert await mod._resolve_team_room(db_client, "agent_lead") == (None, None)


@pytest.mark.asyncio
async def test_an_injected_team_with_no_room_yet_resolves_to_nothing(db_client):
    """Header says "team t9", but the room does not exist. Better to have no
    board than to write items into a channel id nobody reads."""
    with injected_team("t9"):
        assert await mod._resolve_team_room(db_client, "agent_lead") == (None, None)
