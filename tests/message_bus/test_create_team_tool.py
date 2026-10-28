"""
@file_name: test_create_team_tool.py
@date: 2026-08-19
@description: `create_team` builds a REAL team, not an orphan bus channel.

The tool sits in the byte-stable instruction block, so the model calls it every
bus turn. Before this test it just renamed `bus_create_channel`: no `teams` row,
no `team_members` row, `created_by = the creator agent` (so the creator became
the always-activated channel owner), and it returned a `channel_id` the agent
had no verb to use — `message_team` looks the team up in `teams` and got None.

The load-bearing assertion is the end-to-end one: create_team -> message_team
must SUCCEED. Revert create_team to `bus.create_channel(...)` and it goes red on
"team not found for this owner".
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus.team_rooms import team_room_marker
from narranexus_plugins.message_bus_module._message_bus_mcp_tools import (
    register_message_bus_mcp_tools,
)

OWNER, OTHER_OWNER = "usr_owner", "usr_other"
ME, PEER, STRANGER = "agent_me", "agent_peer", "agent_stranger"


def _patch_db(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )


async def _agent(db, agent_id, owner):
    await db.insert("agents", {"agent_id": agent_id, "agent_name": agent_id, "created_by": owner})


def _tools(db_client):
    bus = LocalMessageBus(backend=db_client._backend)
    captured: dict = {}

    class _Stub:
        def tool(self, *_a, **_k):
            def _wrap(fn):
                captured[fn.__name__] = fn
                return fn
            return _wrap

    async def _bus():
        return bus

    register_message_bus_mcp_tools(_Stub(), _bus)
    return captured, bus


@pytest.mark.asyncio
async def test_create_team_makes_a_real_team_the_agent_can_then_message(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, ME, OWNER)
    await _agent(db_client, PEER, OWNER)
    tools, _bus = _tools(db_client)

    created = await tools["create_team"](
        agent_id=ME, name="Project Alpha Coordination", members=PEER
    )
    assert created["success"] is True, created
    team_id = created["team_id"]

    # A real `teams` row owned by the creator's user — so it shows in the UI and
    # passes message_team's owner check.
    team = await db_client.get_one("teams", {"team_id": team_id})
    assert team is not None and team["owner_user_id"] == OWNER

    # `team_members` rows for both — the membership message_team/read_history gate.
    members = {
        m["agent_id"]
        for m in await db_client.get("team_members", {"team_id": team_id})
    }
    assert members == {ME, PEER}

    # The room's created_by is the NON-AGENT marker, not the creator — otherwise
    # the creator would be the always-activated channel owner.
    room = await db_client.get_one(
        "bus_channels", {"created_by": team_room_marker(team_id), "channel_type": "group"}
    )
    assert room is not None, "no team room with the team_<id> marker as created_by"

    # THE point: the agent can actually talk in the team it just created.
    posted = await tools["message_team"](agent_id=ME, team_id=team_id, text="kickoff")
    assert posted["success"] is True, posted


@pytest.mark.asyncio
async def test_create_team_rejects_a_cross_owner_invite_without_writing_a_team(db_client, monkeypatch):
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, ME, OWNER)
    await _agent(db_client, STRANGER, OTHER_OWNER)  # different owner
    tools, _bus = _tools(db_client)

    result = await tools["create_team"](agent_id=ME, name="Cross Tenant", members=STRANGER)
    assert result["success"] is False and "cross-user" in result["error"]

    # And it left NO orphan team row behind (checked before any write).
    assert await db_client.get("teams", {"owner_user_id": OWNER}) == []


@pytest.mark.asyncio
async def test_create_team_rolls_back_all_rows_if_room_build_crashes_after_the_channel(db_client, monkeypatch):
    """A crash AFTER get_or_create_team_room has written the channel + `team_<id>`
    marker must roll back EVERYTHING: teams, team_members, AND the room channel +
    its members. The crash is injected at `get_channel_members` (which runs after
    `create_channel` + the marker UPDATE), so the channel really exists when the
    rollback fires — a rollback that skipped `bus_channels` would leave an orphan
    `team_<dead-id>` room."""
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, ME, OWNER)
    await _agent(db_client, PEER, OWNER)
    tools, bus = _tools(db_client)

    async def _boom(*_a, **_k):
        raise RuntimeError("member sync failed")

    monkeypatch.setattr(bus, "get_channel_members", _boom)

    result = await tools["create_team"](agent_id=ME, name="Doomed", members=PEER)
    assert result["success"] is False

    assert await db_client.get("teams", {"owner_user_id": OWNER}) == []
    assert await db_client.get("team_members", {"agent_id": ME}) == []
    # The room channel (created before the crash) is gone too — no orphan.
    assert await db_client.get("bus_channels", {"channel_type": "group"}) == []
    assert await db_client.get("bus_channel_members", {"agent_id": ME}) == []


@pytest.mark.asyncio
async def test_create_team_rejects_an_unknown_agent_without_writing_a_team(db_client, monkeypatch):
    """A member id with no `agents` row (a model typo / invented id) must be
    rejected, not written as a ghost member — the UI writer 404s the same input.
    `_resolve_owner_user_id` returns None for it, so the check must treat None as
    'reject', not 'allow'."""
    _patch_db(monkeypatch, db_client)
    await _agent(db_client, ME, OWNER)
    tools, _bus = _tools(db_client)

    result = await tools["create_team"](agent_id=ME, name="Ghosts", members="agent_ghost")
    assert result["success"] is False and "unknown agent" in result["error"]

    # No writes: no team, no team_members, no ghost bus_channel_members.
    assert await db_client.get("teams", {"owner_user_id": OWNER}) == []
    assert await db_client.get("team_members", {"agent_id": "agent_ghost"}) == []


@pytest.mark.asyncio
async def test_create_team_rejects_over_the_member_cap_without_writing_a_team(db_client, monkeypatch):
    """`members` is a model-supplied string; the cardinality bound is the code's,
    not the model's. Over the cap → rejected before any write."""
    from narranexus_plugins.message_bus_module._message_bus_mcp_tools import (
        CREATE_TEAM_MAX_MEMBERS,
    )

    _patch_db(monkeypatch, db_client)
    await _agent(db_client, ME, OWNER)
    tools, _bus = _tools(db_client)

    too_many = ",".join(f"agent_{i}" for i in range(CREATE_TEAM_MAX_MEMBERS + 5))
    result = await tools["create_team"](agent_id=ME, name="Huge", members=too_many)
    assert result["success"] is False and str(CREATE_TEAM_MAX_MEMBERS) in result["error"]

    assert await db_client.get("teams", {"owner_user_id": OWNER}) == []
