"""
@file_name: test_team_monologue_wiring.py
@author: Bin Liang
@date: 2026-07-30
@description: Locks the include_monologue wiring in MessageBusTrigger.

The team room is the only surface whose prompt promises the agent its
plain text is delivered (auto-posted to the room), so it is the only
branch that may opt into NexusPower monologue collection. This wiring
is a single kwarg with default False at BOTH ends — if a refactor drops
it, the room silently goes mute again (the #203 incident shape:
evt_238abc4b0b0c4dca had the full reply in final_output and zero rows
in bus_messages). These tests pin prompt promise and collection switch
together.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import (
    TEAM_ROOM_OWNER_PREFIX,
    MessageBusTrigger,
)
from xyz_agent_context.message_bus.schemas import BusMessage


def _patch_db_factory(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client", _async_db
    )


async def _seed_agent(db_client, agent_id="agent_a", owner="user_x"):
    await db_client.insert(
        "agents", {"agent_id": agent_id, "agent_name": "A", "created_by": owner}
    )


def _recording_invoke(seen: dict):
    async def _record(*args, **kwargs):
        seen.update(kwargs)
        return "", None

    return _record


@pytest.mark.asyncio
async def test_team_room_batch_opts_into_monologue(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)

    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)
    seen: dict = {}
    monkeypatch.setattr(trigger, "_invoke_runtime", _recording_invoke(seen))

    msg = BusMessage(
        message_id="m1", channel_id="ch_team", from_agent="usr_user_x",
        content="@A hello",
    )
    await trigger._handle_channel_batch(
        "agent_a", "ch_team", [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    assert seen.get("include_monologue") is True


@pytest.mark.asyncio
async def test_peer_channel_batch_keeps_monologue_private(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)

    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)
    seen: dict = {}
    monkeypatch.setattr(trigger, "_invoke_runtime", _recording_invoke(seen))

    msg = BusMessage(
        message_id="m2", channel_id="ch_peer", from_agent="peer", content="hi"
    )
    await trigger._handle_channel_batch(
        "agent_a", "ch_peer", [msg], msg, channel_owner="peer"
    )

    assert seen.get("include_monologue") is False
