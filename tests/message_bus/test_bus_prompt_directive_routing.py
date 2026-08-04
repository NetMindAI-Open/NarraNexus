"""
@file_name: test_bus_prompt_directive_routing.py
@author: NarraNexus
@date: 2026-08-01
@description: A bus-triggered turn must be told to answer the right party
(P1 2026-08-03, found by running the incident scenario live).

The bug: ``_build_prompt`` appended "## Owner Relay — REQUIRED" whenever the
agent had an owner — which is always. So an agent receiving a FRESH question
from a peer was told:

    "Your owner originally asked you to contact this peer agent.
     They are waiting in chat for the answer."

For the RECIPIENT that is simply false: its owner asked for nothing. Three
live runs confirmed the consequence — 羽书 called
``send_message_to_user_directly`` and reported the errand discharged
("未回复小雀 — 她是转发…按 Reply Discipline"), while 小雀, which had promised
its user a report, waited forever. The models were obeying the prompt; the
prompt was wrong.

So the directive is now selected by WHO STARTED the thread:
  - a reply to our own errand  → Owner Relay (unchanged)
  - a fresh inbound question   → answer the PEER on the bus
"""
from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger
from xyz_agent_context.message_bus.schemas import BusMessage


def _msg(message_id: str, from_agent: str, content: str = "你在干嘛？") -> BusMessage:
    return BusMessage(
        message_id=message_id,
        channel_id="ch_dm_1",
        from_agent=from_agent,
        content=content,
        msg_type="text",
        mentions=[],
        created_at="2026-08-03T22:48:34+00:00",
    )


class _FakeDB:
    """Answers the two channel queries the classifier can make.

    They are different questions and must not share a canned answer: the
    legacy fallback asks "which messages of mine are in this channel"
    (message_id rows), while the errand check is an existence probe pushed
    into SQL ("SELECT 1 … LIMIT 1").
    """

    placeholder = "?"

    def __init__(self, rows, errand_rows):
        self._rows = rows
        self._errand_rows = errand_rows
        self.queries: list = []

    async def execute(self, sql, params=None):
        self.queries.append((sql, params))
        return self._errand_rows if "SELECT 1" in sql else self._rows


class _FakeBus:
    def __init__(self, rows, errand_rows):
        self._db = _FakeDB(rows, errand_rows)


FOUND = [{"1": 1}]  # what the pushed-down existence probe returns
NOT_FOUND: list = []


def _trigger(rows=(), *, errand=NOT_FOUND):
    return MessageBusTrigger(bus=_FakeBus(list(rows), errand))


# ---------------------------------------------------------------------------
# Which directive gets appended
# ---------------------------------------------------------------------------


def test_fresh_inbound_question_tells_the_agent_to_answer_the_peer():
    t = _trigger([])
    prompt = t._build_prompt(
        [_msg("m1", "agent_xiaoque")],
        owner_user_id="user_tc",
        owner_name="TC",
        i_started_this_exchange=False,
    )

    assert "## Answer the peer — REQUIRED" in prompt
    assert "Owner Relay" not in prompt
    # It must name the actual reply channel...
    assert "bus_send_to_agent" in prompt
    # ...and kill the two rationalisations seen live.
    assert "your owner is NOT waiting" in prompt
    assert "never a substitute for replying to the peer" in prompt
    assert "A question is never a ping-pong" in prompt


def test_reply_to_our_own_errand_still_gets_owner_relay():
    """The 2026-06 silent-failure fix must not regress."""
    t = _trigger([])
    prompt = t._build_prompt(
        [_msg("m2", "agent_yushu", "我在做 X")],
        owner_user_id="user_tc",
        owner_name="TC",
        i_started_this_exchange=True,
    )

    assert "## Owner Relay — REQUIRED" in prompt
    assert "Answer the peer" not in prompt
    assert "send_message_to_user_directly" in prompt


def test_no_owner_means_no_directive_either_way():
    t = _trigger([])
    for started in (True, False):
        prompt = t._build_prompt(
            [_msg("m3", "agent_x")], owner_user_id="", i_started_this_exchange=started
        )
        assert "REQUIRED" not in prompt


def test_the_directive_flag_is_required():
    """No default: the flag decides which of two contradictory directives the
    agent gets, so a caller that forgets it must fail loudly rather than
    silently inherit Owner Relay (the pre-fix, wrong-for-recipients branch)."""
    t = _trigger([])
    with pytest.raises(TypeError):
        t._build_prompt([_msg("m4", "agent_x")], owner_user_id="user_tc")


# ---------------------------------------------------------------------------
# Classifying the incoming batch (question to us vs reply to our errand)
# ---------------------------------------------------------------------------


def _msg_src(message_id, from_agent, source):
    m = _msg(message_id, from_agent)
    return m.model_copy(update={"sender_turn_source": source})


@pytest.mark.asyncio
async def test_owner_facing_send_means_we_are_being_asked():
    """The peer sent this from a chat turn → it is running an errand for its
    owner and asking US."""
    t = _trigger([])
    assert await t._incoming_is_reply_to_my_errand(
        "agent_yushu", "ch_dm_1", [_msg_src("m1", "agent_xiaoque", "chat")]
    ) is False


@pytest.mark.asyncio
async def test_bus_send_means_it_is_the_reply_to_our_errand():
    """We asked from a chat turn earlier (our errand row is in the channel),
    the peer answers from a bus turn → Owner Relay."""
    t = _trigger(errand=FOUND)
    assert await t._incoming_is_reply_to_my_errand(
        "agent_xiaoque", "ch_dm_1", [_msg_src("m2", "agent_yushu", "message_bus")]
    ) is True


@pytest.mark.asyncio
async def test_follow_up_question_still_reads_as_being_asked():
    """The regression the first heuristic had: after the recipient answers
    once, a follow-up must NOT flip it to Owner Relay."""
    t = _trigger(rows=[{"message_id": "our_earlier_reply"}], errand=FOUND)
    assert await t._incoming_is_reply_to_my_errand(
        "agent_yushu", "ch_dm_1", [_msg_src("m3", "agent_xiaoque", "chat")]
    ) is False


@pytest.mark.asyncio
async def test_reverse_direction_in_a_reused_dm_channel():
    """The review's finding: DM channels are found symmetrically and REUSED,
    so "who opened the channel" is fixed forever. Once A has DM'd B, an errand
    B later runs toward A must still classify correctly for BOTH sides —
    otherwise that direction is permanently broken.
    """
    t = _trigger(errand=FOUND)
    # B asks A on B's own errand → A is being asked, even though A opened the channel.
    assert await t._incoming_is_reply_to_my_errand(
        "agent_a", "ch_dm_1", [_msg_src("q", "agent_b", "chat")]
    ) is False
    # A answers → B must relay to ITS owner (this is the step my first fix
    # regressed). B's own chat-stamped question row is what proves the errand.
    assert await t._incoming_is_reply_to_my_errand(
        "agent_b", "ch_dm_1", [_msg_src("a", "agent_a", "message_bus")]
    ) is True


@pytest.mark.asyncio
async def test_mixed_batch_with_any_owner_facing_send_is_a_question():
    t = _trigger([])
    batch = [
        _msg_src("m1", "agent_xiaoque", "message_bus"),
        _msg_src("m2", "agent_xiaoque", "chat"),
    ]
    assert await t._incoming_is_reply_to_my_errand("a", "ch", batch) is False


# --- the turn-kind stamp is not per-message intent (2026-08-03 review) -----


@pytest.mark.asyncio
async def test_errand_continuation_follow_up_reads_as_being_asked():
    """Path A of the review: the Owner-Relay directive itself tells the asker
    to send clarifying follow-ups via bus_send_to_agent. That send happens in
    a MESSAGE_BUS turn, so with the plain stamp it looked like an ANSWER and
    the recipient relayed it to its owner — P1 recurring on the recommended
    path. The asker's errand-continuation turn now stamps
    BUS_ERRAND_TURN_SOURCE, which must read as a question even when the
    recipient has errand rows of its own that would otherwise vote Owner
    Relay (rows chosen exactly so the stamp, not the fallback, decides)."""
    from xyz_agent_context.schema import BUS_ERRAND_TURN_SOURCE

    t = _trigger(errand=FOUND)
    assert await t._incoming_is_reply_to_my_errand(
        "agent_yushu",
        "ch_dm_1",
        [_msg_src("f1", "agent_xiaoque", BUS_ERRAND_TURN_SOURCE)],
    ) is False


@pytest.mark.asyncio
async def test_bus_stamped_question_to_agent_that_never_asked_is_a_question():
    """Path B of the review: an agent in a peer-ANSWERING turn fans out to a
    third agent C. That send is stamped plain "message_bus", but C never
    asked anything in this channel — it cannot be owed an answer, so it must
    answer the peer, not relay to its owner."""
    t = _trigger(errand=NOT_FOUND)  # C never asked anything here
    assert await t._incoming_is_reply_to_my_errand(
        "agent_c", "ch_dm_2", [_msg_src("q1", "agent_yushu", "message_bus")]
    ) is False


@pytest.mark.asyncio
async def test_bus_stamped_question_to_agent_that_only_ever_answered():
    """Same as above, but the recipient HAS spoken here — only ever from
    peer-answering turns. Answering is not asking; still a question to us."""
    # Only bus-stamped sends of ours here, which the pushed-down predicate
    # excludes → the probe finds nothing.
    t = _trigger(errand=NOT_FOUND)
    assert await t._incoming_is_reply_to_my_errand(
        "agent_b", "ch_dm_1", [_msg_src("q2", "agent_a", "message_bus")]
    ) is False


@pytest.mark.asyncio
async def test_the_errand_check_is_an_indexed_existence_probe():
    """It runs on the most common bus path and DM channels are reused forever,
    so the predicate must live in SQL, not in a growing client-side scan."""
    t = _trigger(errand=FOUND)
    await t._incoming_is_reply_to_my_errand(
        "agent_xiaoque", "ch_dm_1", [_msg_src("m", "agent_yushu", "message_bus")]
    )
    sql, params = t._bus._db.queries[-1]
    assert "SELECT 1" in sql and "LIMIT 1" in sql
    assert "sender_turn_source IS NULL OR sender_turn_source <>" in sql
    assert params == ("ch_dm_1", "agent_xiaoque", "message_bus")


@pytest.mark.asyncio
async def test_legacy_null_stamped_own_row_keeps_owner_relay():
    """Our own send predates the stamp (NULL): the pre-stamp benefit of the
    doubt survives — an incoming bus-stamped batch still reads as the reply
    to our (unprovable but plausible) errand."""
    # Legacy NULL matches the probe's "IS NULL" arm → an errand row.
    t = _trigger(errand=FOUND)
    assert await t._incoming_is_reply_to_my_errand(
        "agent_xiaoque", "ch_dm_1", [_msg_src("a1", "agent_yushu", "message_bus")]
    ) is True


# --- degradation when the source was not recorded -------------------------


@pytest.mark.asyncio
async def test_unknown_source_and_we_never_spoke_here_means_asked():
    """Legacy rows / adapters that drop the header. Absence of our own prior
    message is still unambiguous."""
    t = _trigger(rows=[])
    assert await t._incoming_is_reply_to_my_errand(
        "agent_yushu", "ch_dm_1", [_msg("m1", "agent_xiaoque")]
    ) is False


@pytest.mark.asyncio
async def test_unknown_source_but_we_have_spoken_falls_back_to_owner_relay():
    t = _trigger(rows=[{"message_id": "older"}])
    assert await t._incoming_is_reply_to_my_errand(
        "agent_xiaoque", "ch_dm_1", [_msg("m9", "agent_yushu")]
    ) is True


@pytest.mark.asyncio
async def test_db_failure_falls_back_to_owner_relay():
    """Fail toward the OLD behaviour: a wrongly-relayed answer is an
    annoyance, but wrongly suppressing Owner Relay resurrects the
    silent-failure this directive exists to prevent."""

    class _Boom:
        placeholder = "?"

        async def execute(self, *a, **k):
            raise RuntimeError("db down")

    class _Bus:
        _db = _Boom()

    t = MessageBusTrigger(bus=_Bus())
    assert await t._incoming_is_reply_to_my_errand("a", "ch", [_msg("m", "b")]) is True


# ---------------------------------------------------------------------------
# The classifier's verdict must reach the tools as this turn's ERRAND SCOPE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_runtime_forwards_the_errand_scope(monkeypatch):
    """The verdict is useless if it dies inside the trigger: a bus send decides
    its own stamp by comparing its target against
    extra_data["bus_errand_peer"/"bus_errand_channel"], and
    trigger_extra_data is the only pipe that reaches ContextRuntime.

    A whole-turn boolean is deliberately NOT what travels: the same turn also
    answers unrelated peers, and marking their answers as questions is how the
    P1 reappeared one seat over (2026-08-03 review)."""
    from types import SimpleNamespace

    from xyz_agent_context.agent_runtime import client as rt_client

    captured: dict = {}

    class _FakeClient:
        async def run_and_collect(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                is_error=False, output_text="ok", event_id="evt_1"
            )

    monkeypatch.setattr(rt_client, "get_agent_runtime_client", lambda: _FakeClient())

    t = _trigger()
    await t._invoke_runtime(
        agent_id="agent_b", sender_agent_id="agent_a", prompt="p",
        channel_id="ch_dm_1", errand_continuation=True,
    )
    extra = captured["trigger_extra_data"]
    assert extra["bus_errand_peer"] == "agent_a"
    assert extra["bus_errand_channel"] == "ch_dm_1"

    captured.clear()
    await t._invoke_runtime(
        agent_id="agent_b", sender_agent_id="agent_a", prompt="p",
        channel_id="ch_dm_1", errand_continuation=False,
    )
    extra = captured["trigger_extra_data"]
    # Empty, not absent: a turn with no errand must not inherit one.
    assert extra["bus_errand_peer"] == ""
    assert extra["bus_errand_channel"] == ""


# ---------------------------------------------------------------------------
# The fact must actually persist and round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sender_turn_source_round_trips_through_the_db():
    """Mocks cannot prove the column exists, is written, and comes back on the
    BusMessage the trigger reads. Without all three the classifier silently
    degrades to the fallback everywhere."""
    from xyz_agent_context.message_bus.local_bus import LocalMessageBus
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient
    from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
    from xyz_agent_context.utils.db.schema_registry import auto_migrate

    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    db = await AsyncDatabaseClient.create_with_backend(backend)
    try:
        for aid in ("agent_a", "agent_b"):
            await db.insert("agents", {
                "agent_id": aid, "agent_name": aid, "created_by": "user_tc",
            })
        bus = LocalMessageBus(backend=db._backend)

        await bus.send_to_agent(
            from_agent="agent_a", to_agent="agent_b",
            content="what are you working on?", sender_turn_source="chat",
        )
        pending = await bus.get_pending_messages("agent_b")
        assert pending, "recipient must see the message"
        assert pending[0].sender_turn_source == "chat"

        trigger = MessageBusTrigger(bus=bus)
        # An errand question → the recipient must be told to answer the peer.
        assert await trigger._incoming_is_reply_to_my_errand(
            "agent_b", pending[0].channel_id, pending
        ) is False

        # Full conversation against real SQL (mocks can't catch a wrong
        # column name in _i_have_errand_in_channel):
        from xyz_agent_context.schema import BUS_ERRAND_TURN_SOURCE

        channel_id = pending[0].channel_id
        # b answers from a peer-answering turn → a (whose chat-stamped
        # question is in the channel) relays to its owner.
        await bus.send_to_agent(
            from_agent="agent_b", to_agent="agent_a",
            content="working on X", sender_turn_source="message_bus",
        )
        reply = (await bus.get_pending_messages("agent_a"))
        assert await trigger._incoming_is_reply_to_my_errand(
            "agent_a", channel_id, reply
        ) is True
        # a sends a clarifying follow-up from its errand-continuation turn →
        # b must answer the peer again, NOT relay (P1 recurrence path A).
        await bus.send_to_agent(
            from_agent="agent_a", to_agent="agent_b",
            content="which X exactly?",
            sender_turn_source=BUS_ERRAND_TURN_SOURCE,
        )
        follow_up = [
            m for m in await bus.get_recent_messages(channel_id, limit=5)
            if m.content == "which X exactly?"
        ]
        assert follow_up and follow_up[0].sender_turn_source == BUS_ERRAND_TURN_SOURCE
        assert await trigger._incoming_is_reply_to_my_errand(
            "agent_b", channel_id, follow_up
        ) is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_one_errand_turn_serving_two_peers_routes_both_correctly():
    """The 2026-08-03 review's three-step table, walked end to end.

    Every seam is the real one — the headers ContextRuntime would inject, the
    stamp the MCP tool computes from them, the column LocalMessageBus writes,
    and the classifier the trigger runs — because the regression lived in the
    JOIN between them: a whole-turn stamp made step 3 (A answering an
    unrelated peer C from inside its errand-continuation turn) look like a
    question, and C then stopped relaying to its own owner.

        1. C asks A on its owner's behalf (chat turn)   → A answers the peer
        2. B answers A's errand (bus turn)              → A relays to owner
        3a. A follows up with B, same turn               → B answers the peer
        3b. A answers C, same turn                       → C relays to owner
    """
    from xyz_agent_context.message_bus.local_bus import LocalMessageBus
    from xyz_agent_context.module._mcp_identity import agent_id_headers
    from xyz_agent_context.module.message_bus_module._message_bus_mcp_tools import (
        _send_turn_source,
    )
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient
    from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
    from xyz_agent_context.utils.db.schema_registry import auto_migrate

    from tests.message_bus.test_bus_send_stamp import injected

    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    db = await AsyncDatabaseClient.create_with_backend(backend)
    try:
        for aid in ("agent_a", "agent_b", "agent_c"):
            await db.insert("agents", {
                "agent_id": aid, "agent_name": aid, "created_by": "user_tc",
            })
        bus = LocalMessageBus(backend=db._backend)
        trigger = MessageBusTrigger(bus=bus)

        async def _classify(agent, channel, batch):
            return await trigger._incoming_is_reply_to_my_errand(agent, channel, batch)

        # 1. C asks A, on C's owner's behalf.
        await bus.send_to_agent(
            from_agent="agent_c", to_agent="agent_a",
            content="what is A working on?", sender_turn_source="chat",
        )
        asked = await bus.get_pending_messages("agent_a")
        ch_ac = asked[0].channel_id
        assert await _classify("agent_a", ch_ac, asked) is False

        # 2. A runs its own errand toward B; B answers.
        await bus.send_to_agent(
            from_agent="agent_a", to_agent="agent_b",
            content="are you free?", sender_turn_source="chat",
        )
        # B has its turn and acks, exactly as the trigger would — otherwise
        # its next batch still carries this message and the step-3 assertion
        # would not be about the follow-up at all.
        b_first = await bus.get_pending_messages("agent_b")
        await bus.ack_processed(
            "agent_b", b_first[0].channel_id, b_first[-1].created_at
        )
        await bus.send_to_agent(
            from_agent="agent_b", to_agent="agent_a",
            content="yes, on X", sender_turn_source="message_bus",
        )
        answer = [
            m for m in await bus.get_pending_messages("agent_a")
            if m.from_agent == "agent_b"
        ]
        ch_ab = answer[0].channel_id
        assert ch_ab != ch_ac
        assert await _classify("agent_a", ch_ab, answer) is True  # Owner Relay

        # 3. A's errand-continuation turn: the scope ContextRuntime injects.
        turn_headers = agent_id_headers(
            "agent_a", turn_source="message_bus",
            errand_peer="agent_b", errand_channel=ch_ab,
        )
        with injected(turn_headers):
            follow_up_stamp = _send_turn_source(to_agent="agent_b")
            answer_to_c_stamp = _send_turn_source(to_agent="agent_c")

        await bus.send_to_agent(
            from_agent="agent_a", to_agent="agent_b",
            content="which X?", sender_turn_source=follow_up_stamp,
        )
        await bus.send_to_agent(
            from_agent="agent_a", to_agent="agent_c",
            content="I am on X", sender_turn_source=answer_to_c_stamp,
        )

        # 3a. B is being ASKED again — answer the peer, do not relay.
        b_batch = await bus.get_pending_messages("agent_b")
        assert [m.content for m in b_batch] == ["which X?"]
        assert await _classify("agent_b", ch_ab, b_batch) is False

        # 3b. C is being ANSWERED — relay to C's own owner. This is the
        # assertion the whole-turn stamp failed.
        c_batch = await bus.get_pending_messages("agent_c")
        assert [m.content for m in c_batch] == ["I am on X"]
        assert await _classify("agent_c", ch_ac, c_batch) is True
    finally:
        await db.close()
