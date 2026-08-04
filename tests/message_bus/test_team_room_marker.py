"""
@file_name: test_team_room_marker.py
@date: 2026-08-04
@description: Team-room turns are marked in trigger_extra_data so modules
can tell them apart from ordinary bus turns.

Both surfaces run as working_source=MESSAGE_BUS, but they have opposite
delivery contracts: a team room auto-posts the agent's plain text (its
prompt forbids delivery tools), while an ordinary bus turn delivers ONLY
through a bus tool call. MessageBusModule's expressive declaration gates
on this marker — without it, a team turn would advertise bus tools as
the reply surface and invite double-posting. Same two-end pinning shape
as test_team_monologue_wiring.py: call site passes the flag, and
_invoke_runtime stamps ``bus_team_room`` into trigger_extra_data.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

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


async def _handle(trigger, channel_owner: str):
    msg = BusMessage(
        message_id="m1", channel_id="ch_1", from_agent="usr_user_x",
        content="@A hello",
    )
    await trigger._handle_channel_batch(
        "agent_a", "ch_1", [msg], msg, channel_owner=channel_owner,
    )


@pytest.mark.asyncio
async def test_team_batch_passes_team_room_flag(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    seen: dict = {}
    monkeypatch.setattr(trigger, "_invoke_runtime", _recording_invoke(seen))

    await _handle(trigger, f"{TEAM_ROOM_OWNER_PREFIX}team_1")
    assert seen.get("team_room") is True


@pytest.mark.asyncio
async def test_peer_batch_does_not_pass_team_room_flag(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    seen: dict = {}
    monkeypatch.setattr(trigger, "_invoke_runtime", _recording_invoke(seen))

    await _handle(trigger, "agent_owner")
    assert not seen.get("team_room")


@pytest.mark.asyncio
async def test_invoke_runtime_stamps_marker_into_trigger_extra_data(monkeypatch):
    captured: dict = {}

    async def _run_and_collect(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            is_error=False, output_text="ok", event_id="evt_1", error=None
        )

    client = SimpleNamespace(run_and_collect=AsyncMock(side_effect=_run_and_collect))
    monkeypatch.setattr(
        "xyz_agent_context.agent_runtime.client.get_agent_runtime_client",
        lambda: client,
    )

    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    await trigger._invoke_runtime(
        agent_id="agent_a",
        sender_agent_id="usr_user_x",
        prompt="p",
        channel_id="ch_1",
        team_room=True,
    )
    assert captured["trigger_extra_data"]["bus_team_room"] is True

    captured.clear()
    await trigger._invoke_runtime(
        agent_id="agent_a",
        sender_agent_id="usr_user_x",
        prompt="p",
        channel_id="ch_1",
    )
    assert not captured["trigger_extra_data"].get("bus_team_room")
