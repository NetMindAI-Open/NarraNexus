"""
@file_name: test_team_clear_data.py
@date: 2026-07-22
@description: Clearing a team's data wipes the group-chat messages and/or the
shared files, but keeps the team, members, and the bus channel + membership.
Team counterpart to wipe_agent_data.
"""

from __future__ import annotations

import pytest

from backend.routes.teams import _wipe_team_data
from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.schema.team_schema import Team
from xyz_agent_context.utils.workspace_paths import team_shared_dir

OWNER = "user_t"
TID = "team_abc"


async def _seed_team_room(db):
    bus = LocalMessageBus(db._backend)
    for aid in ("agent_a", "agent_b"):
        await db.insert("agents", {"agent_id": aid, "agent_name": aid, "created_by": OWNER})
    channel_id = await bus.create_channel(name="Team", members=["agent_a", "agent_b"], channel_type="group")
    # Team rooms mark the channel with a non-agent owner marker.
    await db.update("bus_channels", {"channel_id": channel_id}, {"created_by": f"team_{TID}"})
    await bus.send_message(from_agent="agent_a", to_channel=channel_id, content="hi")
    await bus.send_message(from_agent="agent_b", to_channel=channel_id, content="yo")
    return channel_id


@pytest.mark.asyncio
async def test_clear_chat_deletes_messages_keeps_channel(db_client):
    channel_id = await _seed_team_room(db_client)
    team = Team(team_id=TID, owner_user_id=OWNER, name="T")

    # A recorded delivery failure for a room message must be wiped with it
    # (the IN-subquery delete), while failures of OTHER channels' messages stay.
    room_msg = (await db_client.get("bus_messages", {"channel_id": channel_id}))[0]
    await db_client.insert("bus_message_failures", {
        "message_id": room_msg["message_id"], "agent_id": "agent_b", "last_error": "boom",
    })
    await db_client.insert("bus_message_failures", {
        "message_id": "msg_other_room", "agent_id": "agent_b", "last_error": "keep me",
    })

    res = await _wipe_team_data(db_client, team, clear_chat=True, clear_files=False)

    assert res["chat_messages"] == 2
    assert res["chat_failures"] == 1
    # Messages gone…
    assert await db_client.get("bus_messages", {"channel_id": channel_id}) == []
    # …their failures too, but not another channel's…
    assert await db_client.get("bus_message_failures", {"message_id": room_msg["message_id"]}) == []
    assert len(await db_client.get("bus_message_failures", {"message_id": "msg_other_room"})) == 1
    # …and the channel + membership survive (room keeps working).
    assert await db_client.get_one("bus_channels", {"channel_id": channel_id}) is not None
    assert len(await db_client.get("bus_channel_members", {"channel_id": channel_id})) == 2


@pytest.mark.asyncio
async def test_clear_files_removes_shared_dir(db_client, tmp_path, monkeypatch):
    from xyz_agent_context import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, "base_working_path", str(tmp_path))

    d = team_shared_dir(OWNER, TID)
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.md").write_text("shared")

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    res = await _wipe_team_data(db_client, team, clear_chat=False, clear_files=True)

    assert res["files_removed"] is True
    assert not d.exists()


@pytest.mark.asyncio
async def test_idempotent_no_room(db_client):
    # No channel / no files → zeros, no error.
    team = Team(team_id="team_ghost", owner_user_id=OWNER, name="T")
    res = await _wipe_team_data(db_client, team, clear_chat=True, clear_files=True)
    assert res["chat_messages"] == 0
    assert res["files_removed"] is False
    assert res["errors"] == []
