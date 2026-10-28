"""
@file_name: test_team_cascade_notice_e2e.py
@author: NarraNexus
@date: 2026-08-14
@description: The capped-cascade notice, driven through the real turn.

The notice itself and its wiring were pinned separately: the notice by its own
unit tests, the wiring by `inspect.getsource` assertions that the batch handler
mentions `post_cascade_capped`. Source assertions were the honest choice at the
time — the only test that reached this path returned a tuple where production
reads `turn.text`, so everything past that line raised, got swallowed, and never
ran. Nothing behavioural COULD reach it.

With that stub fixed, this path is reachable, so it is tested by running it: a
room already at the hop limit, an agent that answers with an @mention, and the
question of whether the room is told the mention went nowhere.

The `@all` case is here rather than only in the unit tests because it is the one
that was broken end to end while both halves passed on their own — the notice
knew how to say it, the caller filtered it out before asking.
"""

from __future__ import annotations

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus.message_bus_trigger import (
    MAX_TEAM_AGENT_HOPS,
    MessageBusTrigger,
    TurnResult,
)
from narranexus.platform.schema.team_schema import TEAM_ROOM_OWNER_PREFIX

from ._team_turn import speak_in_room

CHANNEL = "ch_cascade"
TEAM = "t_cascade"
ME = "agent_me"
PEER = "agent_peer"
USER = "usr_1"


async def _seed(db):
    await db.insert("bus_channels", {
        "channel_id": CHANNEL, "name": "room", "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    })
    for aid, name in ((ME, "Mia"), (PEER, "Pat")):
        await db.insert("bus_channel_members", {"channel_id": CHANNEL, "agent_id": aid})
        await db.insert("agents", {"agent_id": aid, "agent_name": name, "created_by": USER})
    await db.insert("teams", {
        "team_id": TEAM, "owner_user_id": USER, "name": "Desk", "lead_agent_id": ME,
    })


async def _room_at_the_cap(bus):
    """A room in the state the cap exists for: a human asked something a while
    back, and the agents have been talking to each other ever since.

    Order matters and is the whole point — depth counts BACKWARDS from the
    newest message until it meets a human one, so the user's message has to be
    the oldest here. The last agent post mentions the agent under test, which is
    what wakes it.
    """
    await bus.send_message(from_agent=USER, to_channel=CHANNEL, content="anyone?")
    for i in range(MAX_TEAM_AGENT_HOPS + 1):
        await bus.send_message(
            from_agent=PEER,
            to_channel=CHANNEL,
            content=f"hop {i}",
            mentions=[ME],
        )


def _trigger(db, reply: str):
    t = MessageBusTrigger(bus=LocalMessageBus(backend=db._backend))

    async def _invoke(**kwargs):
        # The agent speaks by calling `message_team` (2026-08-17), and
        # `team_posting.post_team_reply` is where mentions are parsed, the hop cap
        # fires AND the cap is narrated. A stub that only returns a TurnResult
        # never posts, so the cap never runs and there is nothing to assert.
        if kwargs.get("team_room") and reply.strip():
            await speak_in_room(
                db=db, bus=t._bus, agent_id=kwargs.get("agent_id") or A,
                team_id=TEAM, channel_id=CHANNEL, text=reply,
                event_id="evt_turn",
            )
        return TurnResult(text=reply, event_id="evt_turn")

    t._invoke_runtime = _invoke  # type: ignore[method-assign]
    return t


@pytest.fixture(autouse=True)
def _db_factory(db_client, monkeypatch):
    async def _get_db():
        return db_client

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", _get_db)


async def _assert_turn_survived(db):
    """No failure was recorded for this turn.

    `_handle_channel_batch` wraps the whole turn in a wide `except`, so any
    contract drift between these stubs and the real `_invoke_runtime` — a
    parameter that no longer exists, a return type that changed — is swallowed
    into `record_failure` and the assertions above stay true. That is not
    hypothetical: it happened to the sibling file's stub, whose e2e coverage was
    dead while the test stayed green.
    """
    rows = await db.execute("SELECT * FROM bus_message_failures", (), fetch=True)
    assert not rows, f"the turn raised and was swallowed: {rows}"


async def _notices(db):
    rows = await db.execute(
        "SELECT * FROM bus_messages WHERE msg_type = %s ORDER BY created_at",
        ("system_cascade",),
        fetch=True,
    )
    return rows or []


@pytest.mark.asyncio
async def test_a_named_teammate_dropped_by_the_cap_is_announced(db_client):
    trigger = _trigger(db_client, reply="@Pat can you take this?")
    await _seed(db_client)
    await _room_at_the_cap(trigger._bus)

    await trigger._process_lane(ME, CHANNEL)

    await _assert_turn_survived(db_client)
    notices = await _notices(db_client)
    assert len(notices) == 1
    assert "Pat" in notices[0]["content"]


@pytest.mark.asyncio
async def test_an_at_all_dropped_by_the_cap_is_announced(db_client):
    """The end-to-end version of the gap: both halves worked, the seam did not."""
    trigger = _trigger(db_client, reply="@all please weigh in")
    await _seed(db_client)
    await _room_at_the_cap(trigger._bus)

    await trigger._process_lane(ME, CHANNEL)

    await _assert_turn_survived(db_client)
    notices = await _notices(db_client)
    assert len(notices) == 1
    assert "@everyone" not in notices[0]["content"]


@pytest.mark.asyncio
async def test_the_notice_comes_after_the_reply(db_client):
    """Order is the readable part: the platform's caveat must not sit above the
    thing it is a caveat about."""
    trigger = _trigger(db_client, reply="@all please weigh in")
    await _seed(db_client)
    await _room_at_the_cap(trigger._bus)

    await trigger._process_lane(ME, CHANNEL)

    rows = await db_client.execute(
        "SELECT from_agent, content, msg_type, created_at FROM bus_messages "
        "WHERE channel_id = %s ORDER BY created_at",
        (CHANNEL,),
        fetch=True,
    )
    reply_at = next(
        i for i, r in enumerate(rows)
        if r["from_agent"] == ME and r["content"] == "@all please weigh in"
    )
    cascade_at = next(i for i, r in enumerate(rows) if r["msg_type"] == "system_cascade")
    assert cascade_at > reply_at


@pytest.mark.asyncio
async def test_the_notice_wakes_nobody(db_client):
    """Load-bearing: the notice exists because a chain had to STOP. Mentioning
    anyone in it would restart the loop it was posted to break."""
    trigger = _trigger(db_client, reply="@all please weigh in")
    await _seed(db_client)
    await _room_at_the_cap(trigger._bus)

    await trigger._process_lane(ME, CHANNEL)

    await _assert_turn_survived(db_client)
    assert (await _notices(db_client))[0]["mentions"] in (None, "")


@pytest.mark.asyncio
async def test_a_room_below_the_cap_says_nothing(db_client):
    """The mentions went through, so there is nothing to explain."""
    trigger = _trigger(db_client, reply="@Pat can you take this?")
    await _seed(db_client)
    await trigger._bus.send_message(
        from_agent=USER, to_channel=CHANNEL, content="anyone?", mentions=[ME]
    )

    await trigger._process_lane(ME, CHANNEL)

    await _assert_turn_survived(db_client)
    assert await _notices(db_client) == []
