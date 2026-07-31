"""
@file_name: test_bus_message_event_id.py
@date: 2026-07-31
@description: bus messages carry the events-row id of the turn that produced
them, so the team transcript can offer a per-message "view reasoning & tools"
disclosure (parity with single chat).

Two layers are pinned:

* LocalMessageBus round-trip: ``send_message(event_id=...)`` persists the id
  and ``get_messages`` / ``get_recent_messages`` surface it on ``BusMessage``.
* MessageBusTrigger team branch: the reply posted back into the room carries
  the turn's event_id captured by ``collect_run`` — without this the column
  stays NULL forever and the disclosure never appears.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import (
    TEAM_ROOM_OWNER_PREFIX,
    MessageBusTrigger,
)
from xyz_agent_context.message_bus.schemas import BusMessage

ROOM = "ch_evt_room"


@pytest.mark.asyncio
async def test_send_message_round_trips_event_id(db_client):
    bus = LocalMessageBus(backend=db_client._backend)
    msg_id = await bus.send_message(
        from_agent="agent_a", to_channel=ROOM, content="reply",
        event_id="evt_turn1",
    )

    row = await db_client.get_one("bus_messages", {"message_id": msg_id})
    assert row["event_id"] == "evt_turn1"

    msgs = await bus.get_messages(ROOM)
    assert msgs[0].event_id == "evt_turn1"
    recent = await bus.get_recent_messages(ROOM)
    assert recent[0].event_id == "evt_turn1"


@pytest.mark.asyncio
async def test_event_id_defaults_to_none(db_client):
    bus = LocalMessageBus(backend=db_client._backend)
    await bus.send_message(from_agent="usr_u1", to_channel=ROOM, content="hi")
    msgs = await bus.get_messages(ROOM)
    assert msgs[0].event_id is None


def _patch_db_factory(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", _async_db
    )


@pytest.mark.asyncio
async def test_team_reply_is_stamped_with_the_turn_event_id(db_client, monkeypatch):
    """The room reply row must reference the turn that produced it."""
    _patch_db_factory(monkeypatch, db_client)
    await db_client.insert(
        "agents", {"agent_id": "agent_a", "agent_name": "A", "created_by": "user_x"}
    )

    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)

    async def _fake_invoke(*_a, **_k):
        return "the reply", "evt_from_turn"

    monkeypatch.setattr(trigger, "_invoke_runtime", _fake_invoke)

    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x",
        content="@A hello",
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    reply = await db_client.get_one("bus_messages", {"from_agent": "agent_a"})
    assert reply is not None
    assert reply["event_id"] == "evt_from_turn"
