"""
@file_name: test_unread_cursor.py
@author:
@date: 2026-08-11
@description: When a bus message stops counting as unread — and when it must not.

`bus_channel_members` carries two cursors and they answer different questions:
``last_processed_at`` is "the trigger drove this agent past here", ``last_read_at``
is "this agent has actually been shown it". Only the second one gates the unread
list that rides every turn's context.

Nothing advanced `last_read_at` in a team room. Its only writer keys off a bus
delivery tool appearing in the turn's trace, and a team reply is posted by the
trigger itself — server-side, no tool call — so the cursor sat at `joined_at`
forever. Every team message stayed unread for the rest of the agent's life, and
those messages ride EVERY scenario's context, owner chat included.

The fix is not "advance it wherever the trigger acks". Two of the four ack sites
run no turn at all, and the distinction is the whole point:

  * a turn ran → the room's scrollback was rendered into the prompt → the agent
    HAS seen those messages, whether or not it chose to reply;
  * no turn ran (not @mentioned, or rate-limited) → the agent has seen nothing.
    Marking those read would delete them unseen, and would take with them the
    one way an un-mentioned member ever learns what its room is up to.

Pinned here:
  * a team turn advances `last_read_at`, reply or silence
  * a stopped team turn advances it too — the prompt was still built
  * not being @mentioned does NOT advance it (the "glance at the room" path)
  * being rate-limited does NOT advance it
  * a peer DM keeps its selective mark_read semantics: no reply, still unread
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus

from ._team_turn import speak_in_room
from narranexus.platform.message_bus.message_bus_trigger import (
    TEAM_ROOM_OWNER_PREFIX,
    MessageBusTrigger,
    TurnResult,
)


CHANNEL = "ch_room"
TEAM = "t1"
ME = "agent_me"
PEER = "agent_peer"
USER = "usr_1"


async def _seed_team_room(db):
    """A two-agent team room, both members with a NULL read cursor."""
    await db.insert("bus_channels", {
        "channel_id": CHANNEL, "name": "room", "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    })
    for aid, name in ((ME, "Mia"), (PEER, "Pat")):
        await db.insert("bus_channel_members", {"channel_id": CHANNEL, "agent_id": aid})
        await db.insert("agents", {"agent_id": aid, "agent_name": name,
                                   "created_by": "usr_1"})
    await db.insert("teams", {
        "team_id": TEAM, "owner_user_id": "usr_1", "name": "Desk",
        "lead_agent_id": ME,
    })


async def _post(bus, *, mentions=None, sender=USER):
    return await bus.send_message(
        from_agent=sender, to_channel=CHANNEL, content="anyone home?",
        mentions=mentions,
    )


async def _read_cursor(db, agent_id=ME):
    row = await db.get_one(
        "bus_channel_members", {"channel_id": CHANNEL, "agent_id": agent_id}
    )
    return (row or {}).get("last_read_at")


def _trigger(db, reply: str = "on it"):
    t = MessageBusTrigger(bus=LocalMessageBus(backend=db._backend))

    async def _invoke(**kwargs):
        # `TurnResult`, not the old `(text, event_id)` tuple. Production
        # switched on 2026-08-13 (delivery is a field, not an inference from a
        # non-empty string); this stub kept returning a tuple and survived only
        # because nothing on the path these tests take read an attribute off it.
        # A stub that lies about the return type is a trap armed for whoever
        # next touches the caller.
        #
        # What the tuple form cost while it lasted: `if turn.text:` raised
        # AttributeError, the batch handler swallowed it, and every assertion in
        # this file passed on the PRE-CRASH state — the cursor is acked a few
        # lines earlier. Nothing after `turn.text` ran at all, which is why the
        # room-posting and cascade paths needed a test file of their own once
        # this was fixed.
        #
        # The team-room post also happens INSIDE the turn now, so the stub has
        # to hand the text to the deliverer the way the runtime does — these
        # tests are about the read cursor, which only moves on a turn that ran.
        # The agent speaks by calling `message_team` (2026-08-17); these tests
        # are about the READ cursor, which moves on a turn that RAN, so the stub
        # has to actually put the words in the room the way the tool does.
        if kwargs.get("team_room") and reply.strip():
            await speak_in_room(
                db=db, bus=t._bus, agent_id=kwargs.get("agent_id") or "",
                team_id=TEAM, channel_id=CHANNEL, text=reply, event_id="evt_turn",
            )
        return TurnResult(text=reply, event_id="evt_turn")

    t._invoke_runtime = _invoke  # type: ignore[method-assign]
    return t


@pytest.fixture(autouse=True)
def _db_factory(db_client, monkeypatch):
    async def _get_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _get_db
    )


# ── a turn ran → read ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_team_turn_marks_the_room_read(db_client):
    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    await _post(trigger._bus, mentions=[ME])

    await trigger._process_lane(ME, CHANNEL)

    assert await _read_cursor(db_client) is not None


@pytest.mark.asyncio
async def test_a_silent_team_turn_marks_the_room_read_too(db_client):
    """Silence is a reply-discipline decision, not "I did not look".

    The scrollback was rendered into the prompt either way. Gating the cursor on
    "did it answer" is what DM channels do, and it is exactly the semantic that
    does not transfer: in a room, being shown a message IS the delivery.
    """
    trigger = _trigger(db_client, reply="")
    await _seed_team_room(db_client)
    await _post(trigger._bus, mentions=[ME])

    await trigger._process_lane(ME, CHANNEL)

    assert await _read_cursor(db_client) is not None


@pytest.mark.asyncio
async def test_a_stopped_team_turn_still_marks_the_room_read(db_client):
    """The owner pressing stop does not un-show what was already shown."""
    from narranexus.platform.agent_runtime.cancellation import CancelledByUser

    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    await _post(trigger._bus, mentions=[ME])

    async def _invoke(**kwargs):
        raise CancelledByUser("stopped by owner")

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._process_lane(ME, CHANNEL)

    assert await _read_cursor(db_client) is not None


# ── no turn ran → NOT read ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unmentioned_member_keeps_the_room_unread(db_client):
    """The "glance at the room" path, and the reason this fix is not a one-liner.

    A member nobody @mentioned runs no turn and is shown nothing. The trigger
    still acks `last_processed_at` so it will not re-examine the batch — but
    marking it READ would drop the message unseen, and that unread entry is the
    only way this member ever finds out what its room has been doing.
    """
    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    await _post(trigger._bus, mentions=[PEER])

    await trigger._process_lane(ME, CHANNEL)

    assert await _read_cursor(db_client) is None
    # …and the message is still on offer to the next turn, whatever wakes it.
    assert [m.content for m in await trigger._bus.get_unread(ME)] == ["anyone home?"]


@pytest.mark.asyncio
async def test_a_rate_limited_turn_keeps_the_room_unread(db_client):
    """Mentioned, but the platform declined to run it. Nothing was shown."""
    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    await _post(trigger._bus, mentions=[ME])
    trigger._check_rate_limit = lambda *a, **kw: False  # type: ignore[method-assign]

    await trigger._process_lane(ME, CHANNEL)

    assert await _read_cursor(db_client) is None


# ── DM keeps its own semantics ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_peer_dm_without_a_reply_stays_unread(db_client):
    """Reply Discipline lives on this cursor.

    In a DM the unread list IS the queue — "I will get to it" depends on the
    message resurfacing. Only an actual reply clears it, and that path runs
    through the module hook, not through here.
    """
    trigger = _trigger(db_client, reply="")
    await db_client.insert("bus_channels", {
        "channel_id": "ch_dm", "name": "dm", "channel_type": "direct",
        "created_by": PEER,
    })
    for aid in (ME, PEER):
        await db_client.insert(
            "bus_channel_members", {"channel_id": "ch_dm", "agent_id": aid}
        )
        await db_client.insert(
            "agents", {"agent_id": aid, "agent_name": aid, "created_by": "usr_1"}
        )
    await trigger._bus.send_message(
        from_agent=PEER, to_channel="ch_dm", content="ping",
    )

    await trigger._process_lane(ME, CHANNEL)

    row = await db_client.get_one(
        "bus_channel_members", {"channel_id": "ch_dm", "agent_id": ME}
    )
    assert (row or {}).get("last_read_at") is None


# ── the cursor may not outrun what was rendered ─────────────────────────────

@pytest.mark.asyncio
async def test_a_backlog_deeper_than_the_scrollback_is_not_swallowed(db_client):
    """"A turn ran" is not "everything before it was shown".

    The prompt renders `TEAM_HISTORY_LIMIT` messages. Advancing the cursor to
    the trigger declares the WHOLE room read up to that point — so a member who
    accumulated 60 messages while nobody @mentioned it, then finally gets
    @mentioned, is shown 20 and silently loses 40 it never saw.

    That is the same failure this lane refuses to cause at the un-mentioned and
    rate-limited ack sites, arriving on the path that does run a turn. A single
    high-water cursor cannot say "read the window but not the gap below it", so
    when the rendered window does not reach back to the cursor, it must not move
    at all.
    """
    from narranexus.platform.message_bus.message_bus_trigger import TEAM_HISTORY_LIMIT

    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    for i in range(TEAM_HISTORY_LIMIT + 5):
        await trigger._bus.send_message(
            from_agent=PEER, to_channel=CHANNEL, content=f"backlog {i}",
        )
    await _post(trigger._bus, mentions=[ME])

    await trigger._process_lane(ME, CHANNEL)

    assert await _read_cursor(db_client) is None
    # The un-rendered older messages are still on offer.
    unread = [m.content for m in await trigger._bus.get_unread(ME)]
    assert "backlog 0" in unread


@pytest.mark.asyncio
async def test_a_backlog_the_scrollback_covers_is_cleared(db_client):
    """The ordinary case, and the one that has to keep converging.

    When the window reaches back past the cursor there is no gap: everything
    between them was rendered, so the cursor advances and the room settles.
    """
    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    for i in range(3):
        await trigger._bus.send_message(
            from_agent=PEER, to_channel=CHANNEL, content=f"chat {i}",
        )
    await _post(trigger._bus, mentions=[ME])

    await trigger._process_lane(ME, CHANNEL)

    assert await _read_cursor(db_client) is not None
    assert await trigger._bus.get_unread(ME) == []


# ── the reply is posted from inside the turn ────────────────────────────────

@pytest.mark.asyncio
async def test_the_team_reply_is_posted_exactly_once(db_client):
    """Exactly once — the hazard survives every rewrite of HOW it is posted.

    2026-08-17: the post moved from a trigger callback to the agent's own tool
    call. The double-speak hazard is unchanged and the reason is unchanged: a room
    that says everything twice is worse than any accounting bug being fixed. What
    this now guards is that the trigger, having stopped being the poster, did not
    keep a post of its own anywhere on the success path.
    """
    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    await _post(trigger._bus, mentions=[ME])

    delivered: list[str] = []

    async def _invoke(**kwargs):
        assert kwargs.get("team_room") is True, "the team lane lost its marker"
        await speak_in_room(
            db=db_client, bus=trigger._bus, agent_id=ME, team_id=TEAM,
            channel_id=CHANNEL, text="on it", event_id="evt_turn",
        )
        delivered.append("on it")
        return TurnResult(text="on it", event_id="evt_turn")

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._process_lane(ME, CHANNEL)

    mine = [
        m for m in await trigger._bus.get_recent_messages(CHANNEL, limit=10)
        if m.from_agent == ME
    ]
    assert [m.content for m in mine] == ["on it"]
    assert delivered == ["on it"]


@pytest.mark.asyncio
async def test_the_deliverer_still_parses_mentions_and_stamps_the_run(db_client):
    """What delivery MEANS travels with the post, wherever the post lives.

    Mention parsing, the agent-hop cap and the run-id stamp are properties of
    putting words in a room, so when the post moved to the agent's tool call they
    moved to `team_posting` with it. Dropping them silently is the hazard —
    @mentions are how work is handed on, and without the stamp the transcript
    cannot open the turn behind a line.
    """
    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    await _post(trigger._bus, mentions=[ME])

    async def _invoke(**kwargs):
        # Real ordering: Step 0 mints the run id and the runtime reports it
        # long before the reply is delivered at the end of the turn.
        from narranexus.platform.message_bus.message_bus_trigger import TurnResult

        await kwargs["on_event_id"]("evt_turn")
        await speak_in_room(
            db=db_client, bus=trigger._bus, agent_id=ME, team_id=TEAM,
            channel_id=CHANNEL, text="@Pat can you take the index?",
            event_id="evt_turn",
        )
        return TurnResult(text="@Pat can you take the index?", event_id="evt_turn")

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._process_lane(ME, CHANNEL)

    posted = [
        m for m in await trigger._bus.get_recent_messages(CHANNEL, limit=10)
        if m.from_agent == ME
    ][0]
    assert posted.mentions == [PEER]
    assert posted.event_id == "evt_turn"


@pytest.mark.asyncio
async def test_a_peer_dm_gets_no_deliverer(db_client):
    """Only team rooms auto-post. A DM reply goes to the inbox, and handing
    that lane a deliverer would post it into the channel as well."""
    trigger = _trigger(db_client)
    await db_client.insert("bus_channels", {
        "channel_id": "ch_dm", "name": "dm", "channel_type": "direct",
        "created_by": PEER,
    })
    for aid in (ME, PEER):
        await db_client.insert(
            "bus_channel_members", {"channel_id": "ch_dm", "agent_id": aid}
        )
        await db_client.insert(
            "agents", {"agent_id": aid, "agent_name": aid, "created_by": "usr_1"}
        )
    await trigger._bus.send_message(
        from_agent=PEER, to_channel="ch_dm", content="ping",
    )
    seen: dict = {}

    async def _invoke(**kwargs):
        seen["cb"] = kwargs.get("on_plain_text_delivery")
        return TurnResult(text="pong", event_id="evt_turn")

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._process_lane(ME, "ch_dm")

    assert seen["cb"] is None


@pytest.mark.asyncio
async def test_a_failed_team_turn_tells_the_room(db_client):
    """The room's only window onto a broken agent.

    Moving delivery into the turn took this with it: on a failed run the trigger
    gets a ⚠️ notice instead of the agent's text, and the team lane had stopped
    reading its return value. The in-turn path cannot cover it either — it is
    gated on the loop NOT having failed, so a half-streamed fragment never reads
    as an answer.

    Silence is the worst outcome here: a teammate that @mentioned this agent
    cannot tell "not interested" from "broken", and the hand-off just stops.
    """
    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    await _post(trigger._bus, mentions=[ME])

    async def _invoke(**kwargs):
        from narranexus.platform.message_bus.message_bus_trigger import TurnResult

        # What `_invoke_runtime` returns on a fatal run: the notice text, and
        # `fatal` saying it is one rather than the agent's words.
        return TurnResult(
            text="⚠️ I couldn't process your message right now (auth). key dead",
            event_id=None, fatal=True,
        )

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._process_lane(ME, CHANNEL)

    posted = await trigger._bus.get_recent_messages(CHANNEL, limit=10)
    notices = [m for m in posted if "couldn't process" in m.content]
    assert len(notices) == 1
    # As the ROOM, not as the agent: it is not a reply the agent made, and it
    # must not count as one or drag teammates in through @mentions.
    assert notices[0].from_agent.startswith("team_")
    assert notices[0].mentions is None
    # And nothing was posted under the agent's own name.
    assert [m for m in posted if m.from_agent == ME] == []


@pytest.mark.asyncio
async def test_a_transient_hiccup_does_not_announce_a_failure(db_client):
    """The dual of the test above, and the failure it guards is worse than a
    missing notice: a notice that is not true.

    "Failed" was read two different ways — the notice fired on ANY error frame,
    while the in-turn delivery gate only looked at whether the loop raised. A
    recoverable provider blip sets the first and not the second, so the room got
    the correct answer AND "I couldn't process your message". A failure surface
    that cries wolf during every provider wobble is one nobody believes when it
    finally matters.
    """
    trigger = _trigger(db_client)
    await _seed_team_room(db_client)
    await _post(trigger._bus, mentions=[ME])

    async def _invoke(**kwargs):
        # What `_invoke_runtime` returns after a recoverable hiccup: the real
        # reply, and run_failed=False.
        from narranexus.platform.message_bus.message_bus_trigger import TurnResult

        await kwargs["on_event_id"]("evt_turn")
        await speak_in_room(
            db=db_client, bus=trigger._bus, agent_id=ME, team_id=TEAM,
            channel_id=CHANNEL, text="here is your answer", event_id="evt_turn",
        )
        return TurnResult(text="here is your answer", event_id="evt_turn")

    trigger._invoke_runtime = _invoke  # type: ignore[method-assign]

    await trigger._process_lane(ME, CHANNEL)

    posted = await trigger._bus.get_recent_messages(CHANNEL, limit=10)
    assert [m.content for m in posted if m.from_agent == ME] == ["here is your answer"]
    assert [m for m in posted if "couldn't process" in m.content] == []
