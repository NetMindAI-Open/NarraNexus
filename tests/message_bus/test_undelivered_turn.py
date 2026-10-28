"""
@file_name: test_undelivered_turn.py
@date: 2026-08-13
@description: A bus turn that delivered nothing must say so.

PRD 2026-08-04 「看到的必须是真的」§四. Three shapes of the same silence, all
of which used to end with an empty room and a user concluding the agent had
ignored them:

* team room — the turn produced no reply text;
* team room — the reply existed and the ROOM POST failed;
* team room — the reply existed and the RUNTIME DECLINED to post it, while
  reporting the turn as non-fatal (the two fatal gates disagreeing);
* A2A DM — the turn neither answered the peer nor said anything to its owner,
  leaving the asking agent blocked forever.

The regression guards matter as much as the new behaviour: a turn that DID
deliver must stay silent, or every ordinary reply grows a bogus "no reply"
line underneath it.
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus.delivery_notice import (
    DELIVERY_FAILED_MSG_TYPE,
    UNDELIVERED_MSG_TYPE,
)
from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus.message_bus_trigger import (
    TEAM_ROOM_OWNER_PREFIX,
    MessageBusTrigger,
    TurnResult,
)
from narranexus.platform.message_bus.schemas import BusMessage
from narranexus.platform.schema.inbox_schema import InboxMessageType

ROOM = "ch_undelivered_room"
DM = "ch_undelivered_dm"


def _patch_db_factory(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )


async def _seed_agent(db_client, agent_id="agent_a", owner="user_x"):
    await db_client.insert(
        "agents", {"agent_id": agent_id, "agent_name": "A", "created_by": owner}
    )


def _returns(monkeypatch, trigger, result: TurnResult):
    async def _fake(*_a, **_k):
        # A team room's reply is posted from INSIDE the turn now (the chat rows
        # are written before `run()` returns, so a post made afterwards cannot
        # be recorded as a reply). A stub therefore has to do what the runtime
        # does — report the run id, then hand the plain text to the deliverer —
        # or it exercises a path production no longer takes.
        on_event_id = _k.get("on_event_id")
        if on_event_id is not None:
            await on_event_id(result.event_id or "evt_stub")
        deliver = _k.get("on_plain_text_delivery")
        if deliver is not None and result.text and not result.fatal:
            await deliver(result.text)
        return result

    monkeypatch.setattr(trigger, "_invoke_runtime", _fake)


async def _notices(db_client, channel_id, msg_type):
    rows = await db_client.get("bus_messages", {"channel_id": channel_id})
    return [r for r in rows if r.get("msg_type") == msg_type]


# ── team room: the turn produced nothing ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_silent_team_turn_posts_nothing_and_marks_the_roster(db_client, monkeypatch):
    """2026-09-03: the room gets no line; the member's activity row does.

    Delete the `is_team` branch in `_announce_undelivered_turn` and this goes
    red twice — a `system_undelivered` row appears, and the step log no
    longer ends on `silent`.
    """
    from narranexus.platform.message_bus import activity as bus_activity

    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x", content="@A hello"
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    assert await _notices(db_client, ROOM, UNDELIVERED_MSG_TYPE) == []
    rows = await bus_activity.get_channel_activity(db_client, ROOM)
    row = next(r for r in rows if r["agent_id"] == "agent_a")
    assert row["state"] == "idle"
    assert bus_activity.last_turn_was_silent(row) is True


@pytest.mark.asyncio
async def test_a_team_turn_that_replied_gets_no_notice(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="here you go", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x", content="@A hello"
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    assert await _notices(db_client, ROOM, UNDELIVERED_MSG_TYPE) == []


@pytest.mark.asyncio
async def test_a_team_turn_that_posted_via_a_tool_gets_no_notice(
    db_client, monkeypatch
):
    """The team prompt forbids delivery tools, but a model that disobeys and
    posts through one HAS reached the room — claiming otherwise would be the
    same lie in the opposite direction."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(
        monkeypatch, trigger,
        TurnResult(text="", event_id="evt_1", delivered=True),
    )

    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x", content="@A hello"
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    assert await _notices(db_client, ROOM, UNDELIVERED_MSG_TYPE) == []


# ── team room: the post itself failed ───────────────────────────────────────


class _RoomPostFails(LocalMessageBus):
    """Fails the agent's reply post, lets platform notices through — the
    shape of a content-specific write failure (oversize row, encoding)."""

    async def send_message(self, *args, **kwargs):
        if kwargs.get("msg_type", "text") == "text":
            raise RuntimeError("room write rejected: sk-live-DEADBEEF0123456789")
        return await super().send_message(*args, **kwargs)


@pytest.mark.asyncio
async def test_a_failed_room_post_is_visible_and_redacted(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=_RoomPostFails(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="the answer", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x", content="@A hello"
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    notices = await _notices(db_client, ROOM, DELIVERY_FAILED_MSG_TYPE)
    assert len(notices) == 1
    assert "sk-live-DEADBEEF0123456789" not in notices[0]["content"]


@pytest.mark.asyncio
async def test_a_failed_room_post_keeps_the_reply_in_the_owners_inbox(
    db_client, monkeypatch
):
    """The room line says it broke; the inbox keeps WHAT broke. A reply the
    owner paid for should not evaporate because one write failed."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=_RoomPostFails(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="the answer", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x", content="@A hello"
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    rows = await db_client.get("inbox_table", {"user_id": "user_x"})
    assert any("the answer" in (r.get("content") or "") for r in rows)


# ── team room: the runtime declined to deliver, and said it was fine ────────


def _returns_without_delivering(monkeypatch, trigger, result: TurnResult):
    """The runtime's OTHER refusal: it produced text, it never handed it to the
    deliverer, and it did not report the turn as fatal either.

    Not hypothetical — it is what step_3 does when the loop raises after the
    agent has already spoken: `_should_deliver_team_reply` refuses (a
    half-streamed fragment would read as an answer) while the closing error
    frame is `recovered_after_reply`, which `RunCollection.is_fatal` correctly
    does NOT call fatal. The two fatal gates are asking different questions, so
    the trigger must not infer delivery from the absence of a failure.
    """
    async def _fake(*_a, **_k):
        on_event_id = _k.get("on_event_id")
        if on_event_id is not None:
            await on_event_id(result.event_id or "evt_stub")
        return result

    monkeypatch.setattr(trigger, "_invoke_runtime", _fake)


@pytest.mark.asyncio
async def test_a_turn_the_runtime_would_not_deliver_is_not_silent(
    db_client, monkeypatch
):
    """Nobody was told anything: the room must hear about it. A teammate that
    @mentioned this agent otherwise gets silence, and cannot tell "not
    interested" from "broken" — the hand-off simply stops."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns_without_delivering(
        monkeypatch, trigger,
        TurnResult(text="half an answer", event_id="evt_1", fatal=False),
    )

    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x", content="@A hello"
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    assert len(await _notices(db_client, ROOM, DELIVERY_FAILED_MSG_TYPE)) == 1
    # And the text survives: it was generated and paid for.
    rows = await db_client.get("inbox_table", {"user_id": "user_x"})
    assert any("half an answer" in (r.get("content") or "") for r in rows)


@pytest.mark.asyncio
async def test_no_notice_when_the_agent_posted_into_the_room_itself(
    db_client, monkeypatch
):
    """The other half of the same branch, and the one that makes it reachable
    at all: `recovered_after_reply` is emitted BECAUSE the agent used a
    delivery tool, and `message_team` at this room is the most natural one
    to reach for. The prompt forbids it; the platform does not police whether
    the model obeys (CLAUDE.md binding rule #15). The room has already heard the
    agent, so a failure notice on top of its reply is a false ⚠️ — the exact
    shape this lane spent two rounds removing from the DM side."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)

    async def _fake(*_a, **_k):
        on_event_id = _k.get("on_event_id")
        if on_event_id is not None:
            await on_event_id("evt_1")
        # What the disobedient tool call does, stamped the way the MCP tool
        # stamps it — the turn id is the only thing tying it to this run.
        await bus.send_message(
            from_agent="agent_a", to_channel=ROOM,
            content="I did the thing", event_id="evt_1",
        )
        return TurnResult(text="I did the thing", event_id="evt_1", fatal=False)

    monkeypatch.setattr(trigger, "_invoke_runtime", _fake)

    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x", content="@A hello"
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    assert await _notices(db_client, ROOM, DELIVERY_FAILED_MSG_TYPE) == []


@pytest.mark.asyncio
async def test_a_post_to_someone_else_still_leaves_this_room_owed_a_notice(
    db_client, monkeypatch
):
    """Third sub-case: the agent did use a delivery tool, but aimed it at a
    teammate's DM (or only at its owner). THIS room heard nothing, so the
    narrowing above must not swallow the notice — the test that would miss
    this is one that only checks "did the agent send anything at all"."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)

    async def _fake(*_a, **_k):
        on_event_id = _k.get("on_event_id")
        if on_event_id is not None:
            await on_event_id("evt_1")
        await bus.send_message(
            from_agent="agent_a", to_channel=DM,
            content="psst, over here", event_id="evt_1",
        )
        return TurnResult(text="psst, over here", event_id="evt_1", fatal=False)

    monkeypatch.setattr(trigger, "_invoke_runtime", _fake)

    msg = BusMessage(
        message_id="m1", channel_id=ROOM, from_agent="usr_user_x", content="@A hello"
    )
    await trigger._handle_channel_batch(
        "agent_a", ROOM, [msg], msg,
        channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1",
    )

    assert len(await _notices(db_client, ROOM, DELIVERY_FAILED_MSG_TYPE)) == 1


# ── A2A: the peer that asked is the one left hanging ────────────────────────


@pytest.mark.asyncio
async def test_an_a2a_turn_that_reached_nobody_wakes_the_asker(
    db_client, monkeypatch
):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=DM, from_agent="agent_b", content="can you help?"
    )
    await trigger._handle_channel_batch("agent_a", DM, [msg], msg)

    notices = await _notices(db_client, DM, UNDELIVERED_MSG_TYPE)
    assert len(notices) == 1
    # The mention is the whole point: only a message wakes a blocked peer.
    assert "agent_b" in (notices[0]["mentions"] or "")


@pytest.mark.asyncio
async def test_an_a2a_silence_also_reaches_the_owner(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=DM, from_agent="agent_b", content="can you help?"
    )
    await trigger._handle_channel_batch("agent_a", DM, [msg], msg)

    rows = await db_client.get("inbox_table", {"user_id": "user_x"})
    assert any(
        r.get("message_type") == InboxMessageType.SYSTEM_NOTICE.value for r in rows
    )


@pytest.mark.asyncio
async def test_an_a2a_turn_that_answered_the_peer_gets_no_notice(
    db_client, monkeypatch
):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(
        monkeypatch, trigger,
        TurnResult(text="", event_id="evt_1", delivered=True),
    )

    msg = BusMessage(
        message_id="m1", channel_id=DM, from_agent="agent_b", content="can you help?"
    )
    await trigger._handle_channel_batch("agent_a", DM, [msg], msg)

    assert await _notices(db_client, DM, UNDELIVERED_MSG_TYPE) == []


@pytest.mark.asyncio
async def test_an_a2a_turn_that_relayed_to_its_owner_gets_no_notice(
    db_client, monkeypatch
):
    """Owner relay reached someone. Scope is deliberately "reached NOBODY":
    a turn that answered the wrong party is a routing problem, not silence,
    and dressing it as one would fire on every legitimate relay."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="told my owner", event_id="e"))

    msg = BusMessage(
        message_id="m1", channel_id=DM, from_agent="agent_b", content="can you help?"
    )
    await trigger._handle_channel_batch("agent_a", DM, [msg], msg)

    assert await _notices(db_client, DM, UNDELIVERED_MSG_TYPE) == []


@pytest.mark.asyncio
async def test_a_notice_never_answers_a_notice(db_client, monkeypatch):
    """Ping-pong guard. B goes silent → A is woken by the notice → if A also
    goes silent, a second notice would wake B, and two quiet agents would
    volley platform lines at each other forever."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=DM, from_agent="agent_b",
        content="This turn ended without delivering a reply.",
        msg_type=UNDELIVERED_MSG_TYPE,
    )
    await trigger._handle_channel_batch("agent_a", DM, [msg], msg)

    assert await _notices(db_client, DM, UNDELIVERED_MSG_TYPE) == []


# ── delivery detection asks the registry, fail-open ─────────────────────────


def test_delivered_to_anyone_recognises_the_bus_send_tools():
    assert MessageBusTrigger._delivered_to_anyone(["message_team"]) is True
    assert MessageBusTrigger._delivered_to_anyone(["mcp__msgbus__message_agent"]) is True


def test_delivered_to_anyone_rejects_tools_that_do_not_deliver():
    assert MessageBusTrigger._delivered_to_anyone(["Read", "team_work_complete"]) is False
    assert MessageBusTrigger._delivered_to_anyone([]) is False


def test_delivered_to_anyone_fails_open_when_the_registry_downgrades(monkeypatch):
    """get() never raises — it falls back to the default handler (owner-chat
    tool only), so the bus sends stop counting as delivery. That quiet downgrade
    is the same bug as a raise and must fail open to True, or every turn that
    answered its peer correctly gets stamped "no reply"."""
    from narranexus.platform.channel.message_source_handler import (
        MessageSourceRegistry,
        _DEFAULT_HANDLER,
    )

    monkeypatch.setattr(MessageSourceRegistry, "get", lambda src: _DEFAULT_HANDLER)
    assert MessageBusTrigger._delivered_to_anyone(["message_team"]) is True


def test_delivered_to_anyone_fails_open_when_the_registry_raises(monkeypatch):
    from narranexus.platform.channel.message_source_handler import MessageSourceRegistry

    def _boom(_src: str):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(MessageSourceRegistry, "get", _boom)
    assert MessageBusTrigger._delivered_to_anyone(["message_team"]) is True


# ── the ping-pong guard covers every platform type, not only the notice ─────


@pytest.mark.asyncio
async def test_a_patrol_triggered_silence_gets_no_notice(db_client, monkeypatch):
    """A patrol line mentions the members it is chasing; one of them going quiet
    must not read as "the user asked and got nothing". Platform-initiated turns
    have no one waiting on an answer."""
    from narranexus.platform.message_bus.patrol import PATROL_MSG_TYPE

    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    _returns(monkeypatch, trigger, TurnResult(text="", event_id="evt_1"))

    msg = BusMessage(
        message_id="m1", channel_id=DM, from_agent="agent_b",
        content="chasing a stalled work item", msg_type=PATROL_MSG_TYPE,
    )
    await trigger._handle_channel_batch("agent_a", DM, [msg], msg)

    assert await _notices(db_client, DM, UNDELIVERED_MSG_TYPE) == []
