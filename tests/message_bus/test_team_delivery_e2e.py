"""
@file_name: test_team_delivery_e2e.py
@date: 2026-08-17
@description: Acceptance for the whole delivery change — the agent's own tool call.

Every other team test stubs the delivery: it calls `post_team_reply` (or, before
2026-08-17, the trigger's deliverer) directly. That leaves the seam this change is
ABOUT untested — the MCP tool itself, with its identity headers, its three
membership gates, its room resolution and its return shape. Two bugs in this
change lived exactly there and were invisible to the rest of the suite:
`_describe_agent` did not exist, and the hop cap read `.placeholder` off a client
that has none, so every call returned `{"success": false}` while the room stayed
silent.

So this drives the REAL registered tool, on a real database, from inside a real
`_process_lane` dispatch, and asserts what a person in the room would notice:

  * the words are in the room, once, attributed to the agent
  * an @mention resolved, so the hand-off can continue
  * the turn is stamped, so the transcript can open it
  * the poll loop was told to look again (else the next hop waits 3-12s)
  * the platform did NOT add a "said nothing" notice on top of a turn that spoke
  * the read cursor advanced, so the same message is not re-answered next turn
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus import wake_signal
from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus.message_bus_trigger import (
    MessageBusTrigger,
    TurnResult,
)
from narranexus.platform.module_system._mcp_identity import agent_id_headers
from narranexus_plugins.message_bus_module._message_bus_mcp_tools import (
    register_message_bus_mcp_tools,
)
from narranexus.platform.schema.team_schema import (
    TEAM_ROOM_OWNER_PREFIX,
    USER_SENDER_PREFIX,
)
from narranexus.platform.message_bus.system_messages import PLATFORM_MSG_TYPES

from ._mcp_headers import injected

TEAM, ROOM, OWNER = "team_e2e", "ch_e2e", "usr_owner"
ANA, BO = "agent_ana", "agent_bo"
TURN = "evt_e2e_turn"


@pytest.fixture(autouse=True)
def _db_factory(db_client, monkeypatch):
    async def _get_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _get_db
    )


async def _seed(db):
    await db.insert("bus_channels", {
        "channel_id": ROOM, "name": "E2E", "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    })
    for aid, nm in ((ANA, "Ana"), (BO, "Bo")):
        await db.insert("bus_channel_members", {"channel_id": ROOM, "agent_id": aid})
        await db.insert("agents", {"agent_id": aid, "agent_name": nm, "created_by": OWNER})
        await db.insert("team_members", {"team_id": TEAM, "agent_id": aid})
    await db.insert("teams", {
        "team_id": TEAM, "owner_user_id": OWNER, "name": "E2E", "lead_agent_id": ANA,
    })


def _registered_tools(bus) -> dict:
    """The real tool functions, exactly as the MCP server registers them."""
    captured: dict = {}

    class _MCP:
        def tool(self, *_a, **_k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    async def _get_bus():
        return bus

    register_message_bus_mcp_tools(_MCP(), _get_bus)
    return captured


@pytest.mark.asyncio
async def test_a_team_turn_delivers_through_the_agents_own_tool_call(db_client):
    await _seed(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)
    tools = _registered_tools(bus)

    # The user asks Ana something, naming her.
    await bus.send_message(
        from_agent=f"{USER_SENDER_PREFIX}owner", to_channel=ROOM,
        content="@Ana who is taking the index?", mentions=[ANA],
    )
    signal_before = await wake_signal.read(db_client)

    prompts: list[str] = []

    async def _invoke(**kwargs):
        prompts.append(kwargs.get("prompt") or "")
        await kwargs["on_event_id"](TURN)
        # THE POINT: the agent calls the registered tool, headers and all —
        # the platform path is not stubbed anywhere below this line.
        with injected(agent_id_headers(
            ANA, turn_source="message_bus", event_id=TURN, team_id=TEAM,
        )):
            result = await tools["message_team"](
                agent_id=ANA, team_id=TEAM, text="@Bo can you take the index?",
            )
        assert result["success"] is True, result
        return TurnResult(text="@Bo can you take the index?", event_id=TURN)

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._process_lane(ANA, ROOM)

    # The prompt told her HOW to speak, with the room she is in.
    assert prompts, "the turn never reached the runtime"
    assert f'message_team(team_id="{TEAM}"' in prompts[0]

    rows = await bus.get_recent_messages(ROOM, limit=20)
    mine = [m for m in rows if m.from_agent == ANA]
    assert len(mine) == 1, f"expected exactly one reply, got {len(mine)}"
    assert mine[0].content == "@Bo can you take the index?"
    assert mine[0].mentions == [BO], "the hand-off did not resolve"
    assert mine[0].event_id == TURN, "the transcript cannot open this turn"

    # The next hop is scheduled, not waited for.
    assert await wake_signal.read(db_client) != signal_before

    # No platform notice on top of a turn that spoke.
    notices = [m for m in rows if (m.msg_type or "") in PLATFORM_MSG_TYPES]
    assert notices == [], f"the platform talked over a turn that delivered: {notices}"

    # And the room is not re-answered next turn.
    member = await db_client.get_one(
        "bus_channel_members", {"channel_id": ROOM, "agent_id": ANA}
    )
    assert (member or {}).get("last_read_at") is not None


@pytest.mark.asyncio
async def test_a_turn_that_says_nothing_to_the_room_is_announced(db_client):
    """The net for the briefing-squad shape, end to end.

    While plain text auto-posted this was structurally impossible. It is possible
    again, so the room has to be told — and the platform must NOT write the reply
    itself (iron rule #15).
    """
    await _seed(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)

    await bus.send_message(
        from_agent=f"{USER_SENDER_PREFIX}owner", to_channel=ROOM,
        content="@Ana status?", mentions=[ANA],
    )

    async def _invoke(**kwargs):
        await kwargs["on_event_id"](TURN)
        # Produces words, never calls the tool — the failure this net is for.
        return TurnResult(text="I looked into it and here is the answer", event_id=TURN)

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._process_lane(ANA, ROOM)

    rows = await bus.get_recent_messages(ROOM, limit=20)

    # The distinction that matters, and it is not "no row from the agent":
    # `delivery_notice` files its line ON the agent's row with a PLATFORM
    # msg_type, so the renderer shows `[system]` and the hop count skips it.
    # (`team_notices` uses the room marker instead — two conventions for platform
    # lines, pre-existing, noted rather than changed here.) What must not happen
    # is the platform writing the agent's ANSWER for it.
    spoken = [
        m for m in rows
        if m.from_agent == ANA and (m.msg_type or "") not in PLATFORM_MSG_TYPES
    ]
    assert spoken == [], (
        "the platform put the agent's words in the room for it (iron rule #15)"
    )
    assert "here is the answer" not in " ".join(m.content for m in rows), (
        "the turn's text leaked into the room without the agent sending it"
    )
    assert [m for m in rows if (m.msg_type or "") in PLATFORM_MSG_TYPES], (
        "the room was never told the turn said nothing to it"
    )


@pytest.mark.asyncio
async def test_a_delivered_reply_is_not_announced_failed_without_an_event_id(db_client):
    """The 'did I speak' judge is an event_id identity join, and event_id rides an
    MCP header that is legitimately absent sometimes (I2). When it is, the reply
    is STILL in the room — the platform must not post a 'never sent it' notice
    under it. Revert to `turn.event_id or ""` + `elif not spoke:` and a false ⚠️
    returns.
    """
    await _seed(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)
    tools = _registered_tools(bus)

    await bus.send_message(
        from_agent=f"{USER_SENDER_PREFIX}owner", to_channel=ROOM,
        content="@Ana status?", mentions=[ANA],
    )

    async def _invoke(**kwargs):
        await kwargs["on_event_id"](TURN)
        # The agent DID post to the room, with headers like production.
        with injected(agent_id_headers(
            ANA, turn_source="message_bus", event_id=TURN, team_id=TEAM,
        )):
            result = await tools["message_team"](
                agent_id=ANA, team_id=TEAM, text="@Bo done",
            )
        assert result["success"] is True, result
        # ...but the turn returns to the trigger WITHOUT an event_id — the header
        # was absent on this hop, so the trigger cannot run the identity join.
        return TurnResult(text="@Bo done", event_id="")

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._process_lane(ANA, ROOM)

    rows = await bus.get_recent_messages(ROOM, limit=20)
    # The reply is in the room.
    assert any(m.from_agent == ANA and m.content == "@Bo done" for m in rows), (
        "the agent's reply is missing from the room"
    )
    # And NO platform failure notice was posted on top of it.
    notices = [m for m in rows if (m.msg_type or "") in PLATFORM_MSG_TYPES]
    assert notices == [], (
        f"a false 'never sent it' notice was posted under a delivered reply: {notices}"
    )


@pytest.mark.asyncio
async def test_the_tools_refuse_blank_text_and_the_room_stays_notified(db_client):
    """Blank text through the REAL tools, both verbs, with the room's consequence.

    The failure this closes is not the empty bubble on its own. `message_team`
    validated `team_id` and never validated `text`, so a model calling its
    declared reply tool with empty args — the routine failure mode NexusPower's
    mute-turn nudge exists for — put a blank row in the room, after which
    `has_message_from_turn` answers True, the "said nothing" notice is suppressed,
    and the turn files as DELIVERED. The room looked answered and said nothing,
    which is worse than the silence it replaced.

    Asserted through the registered tools rather than `post_team_reply`, because
    the tool is where the model arrives and where the error has to be legible:
    a `success: true` no-op would teach it that it had replied.
    """
    await _seed(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    tools = _registered_tools(bus)

    with injected(agent_id_headers(
        ANA, turn_source="message_bus", event_id=TURN, team_id=TEAM,
    )):
        for blank in ("", "   ", "\n"):
            room = await tools["message_team"](
                agent_id=ANA, team_id=TEAM, text=blank,
            )
            assert room["success"] is False, room
            assert "text" in (room.get("error") or ""), room

            peer = await tools["message_agent"](agent_id=ANA, to=BO, text=blank)
            assert peer["success"] is False, peer
            assert "text" in (peer.get("error") or ""), peer

    assert await db_client.get("bus_messages", {"channel_id": ROOM}) == [], (
        "a blank reply reached the room"
    )
    # And the peer was not woken for nothing — an empty DM starts a full turn.
    assert await bus.count_unread(BO) == 0
