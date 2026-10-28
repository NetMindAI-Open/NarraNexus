"""
@file_name: test_bus_send_event_id_stamp.py
@author: NarraNexus
@date: 2026-08-14
@description: An agent's own bus send records WHICH turn produced it.

The team room's failure notice hangs on this. When the runtime declines to
deliver a turn but does not call it fatal, the trigger asks the room "did
anything from this agent land here during that turn?" — and answers it by
matching ``bus_messages.event_id`` against the turn's id. The platform's own
in-turn post stamps that id; so must the agent's own ``message_agent``, or
the question has an answer for one half of the deliveries and a guess for the
other, and the guess puts a "delivery failed" line in a room that has already
heard the agent speak.

Why this file exists SEPARATELY from the trigger-side tests: those stub
``_invoke_runtime`` and write the row themselves with ``event_id="evt_1"``,
i.e. they assume this stamping rather than verify it. The stamp rides an
identity HEADER (``caller_event_id_from_request``) whose documented behaviour
on absence is to return None silently — so every link of that chain can break
without a single test going red. This one goes through the registered tool.
"""
from __future__ import annotations

import pytest

from narranexus.platform.module_system._mcp_identity import agent_id_headers
from narranexus_plugins.message_bus_module._message_bus_mcp_tools import (
    register_message_bus_mcp_tools,
)

from ._mcp_headers import injected

ME = "agent_stamp_me"
PEER = "agent_stamp_peer"
ROOM = "ch_stamp_room"
TURN = "evt_the_turn"


class _RecordingBus:
    """Records the kwargs the tool hands the bus. Only the send methods —
    everything else on this path is out of scope for what is being asserted."""

    def __init__(self):
        self.sends: list[dict] = []

    async def send_message(self, **kwargs):
        self.sends.append(kwargs)
        return "msg_1"

    async def send_to_agent(self, **kwargs):
        self.sends.append(kwargs)
        return "msg_2"

    async def get_channel_members(self, _channel_id):
        class _M:
            def __init__(self, aid): self.agent_id = aid
        return [_M(ME), _M(PEER)]


def _tools() -> dict:
    """The registered tool functions, by name — the real entry points.

    Asserting on `caller_event_id_from_request` directly would test the helper,
    not the wiring, and the wiring is the half that was missing.
    """
    captured: dict = {}

    class _MCP:
        def tool(self, *_a, **_k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    bus = _RecordingBus()

    async def _get_bus():
        return bus

    register_message_bus_mcp_tools(_MCP(), _get_bus)
    captured["_bus"] = bus
    return captured


@pytest.mark.asyncio
async def test_message_agent_stamps_the_calling_turn():
    tools = _tools()
    with injected(agent_id_headers(ME, turn_source="message_bus", event_id=TURN)):
        result = await tools["message_agent"](
            agent_id=ME, to=PEER, text="I did the thing"
        )

    assert result["success"] is True
    assert tools["_bus"].sends[0]["event_id"] == TURN


@pytest.mark.asyncio
async def test_message_team_stamps_the_calling_turn(monkeypatch):
    """The column's meaning must not depend on which tool wrote the row.

    2026-08-17 — this used to compare the two PEER send tools
    (`message_team` / `message_agent`); they are one verb now, so the
    pair that has to agree is the peer verb and the ROOM verb. The invariant is
    unchanged: `has_message_from_turn` reads a missing id as "cannot tell", so a
    writer that forgets the stamp makes the team room's failure notice fire at a
    room that already heard the agent speak.
    """
    tools = _tools()

    class _DB:
        async def get_one(self, table, filters):
            if table == "agents":
                return {"agent_id": ME, "created_by": "usr_1", "agent_name": "Me"}
            if table == "teams":
                return {"team_id": "t_1", "owner_user_id": "usr_1"}
            if table == "team_members":
                return {"team_id": "t_1", "agent_id": ME}
            if table == "bus_channels":
                return {"channel_id": ROOM}
            return None

        async def get_by_ids(self, _t, _f, ids):
            return [{"agent_id": a, "agent_name": a} for a in ids]

        async def execute(self, *_a, **_k):
            return []

        async def update(self, *_a, **_k):
            return 1

    async def _get_db():
        return _DB()

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _get_db
    )

    with injected(agent_id_headers(ME, turn_source="message_bus", event_id=TURN)):
        result = await tools["message_team"](
            agent_id=ME, team_id="t_1", text="over to you"
        )

    assert result["success"] is True, result
    assert tools["_bus"].sends[0]["event_id"] == TURN


@pytest.mark.asyncio
async def test_a_codex_shaped_caller_still_carries_the_turn():
    """Some adapters forward only the bearer, so the id has to survive there
    too — a header-only fact was a hole this repo has already paid for."""
    full = agent_id_headers(ME, turn_source="message_bus", event_id=TURN)
    tools = _tools()
    with injected({"Authorization": full["Authorization"]}):
        await tools["message_agent"](
            agent_id=ME, to=PEER, text="hi"
        )

    assert tools["_bus"].sends[0]["event_id"] == TURN


@pytest.mark.asyncio
async def test_no_headers_stamps_no_event_id():
    """The documented degradation, pinned: absence must reach the row as None.

    This is the contract the consumer depends on — `has_message_from_turn`
    reads a missing id as "cannot tell", and a fabricated one would make it
    answer "yes" for a room that heard nothing.
    """
    tools = _tools()
    await tools["message_agent"](agent_id=ME, to=PEER, text="hi")

    assert tools["_bus"].sends[0]["event_id"] is None
