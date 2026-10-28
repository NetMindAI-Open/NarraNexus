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

from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus.message_bus_trigger import (
    TEAM_ROOM_OWNER_PREFIX,
    MessageBusTrigger,
    TurnResult,
)
from narranexus.platform.message_bus.schemas import BusMessage


def _patch_db_factory(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )


CHANNEL = "ch_team_mono"


async def _seed_agent(db_client, agent_id="agent_a", owner="user_x"):
    await db_client.insert(
        "agents", {"agent_id": agent_id, "agent_name": "A", "created_by": owner}
    )


def _recording_invoke(seen: dict):
    async def _record(*args, **kwargs):
        seen.update(kwargs)
        return TurnResult(text="", event_id=None, delivered=True)

    return _record


@pytest.mark.asyncio
async def test_a_team_message_batch_does_not_opt_into_monologue(db_client, monkeypatch):
    """INVERTED 2026-08-17 — see the module docstring.

    While the room auto-posted plain text, the team batch HAD to collect the
    monologue. Now the room takes a tool call, so collecting it would fold the
    agent's private deliberation into `turn.text`, which the failure-notice and
    inbox paths read as the agent's words.
    """
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)

    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)
    seen: dict = {}
    monkeypatch.setattr(trigger, "_invoke_runtime", _recording_invoke(seen))

    msg = BusMessage(
        message_id="m1", channel_id=CHANNEL, from_agent="usr_user_x",
        content="@A hi", mentions=["agent_a"],
    )
    await trigger._handle_channel_batch(
        "agent_a", CHANNEL, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    assert seen, "the batch never reached the runtime"
    assert seen.get("include_monologue") in (False, None), (
        "a team message batch must not collect the monologue: the reply is a "
        "tool call, so plain text is private again"
    )


@pytest.mark.asyncio
async def test_patrol_still_opts_into_monologue():
    """The one place plain text still becomes a message — and it breaks silently.

    Patrol asks the lead to compose the ROOM's status line and posts it under the
    room's own marker. On NexusPower an agent's plain text streams as
    AGENT_THINKING, so without this flag `turn.text` is empty and patrol goes
    mute on that framework while looking fine on claude_code — the #203 shape
    this file exists for, one surface over.

    Asserted from the source rather than by driving a sweep: the call sits behind
    a stalled-item detector, a speech cap and an activity row, and none of that
    is what would regress.
    """
    import inspect

    src = inspect.getsource(MessageBusTrigger._patrol_body)

    assert "include_monologue=True" in src, (
        "patrol stopped collecting the monologue — on NexusPower its line is "
        "the monologue, so the room would go quiet with no error anywhere"
    )


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

    # `is False` before 2026-08-17, when the message lane passed the flag
    # explicitly. It no longer passes it at all — the parameter is patrol's now —
    # so absent and False are the same fact, and the guarantee is that a peer
    # turn's monologue never reaches `turn.text`.
    assert seen.get("include_monologue") in (False, None)
