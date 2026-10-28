"""
@file_name: test_bus_relay_wake.py
@date: 2026-08-14
@description: The handoff gap — a reply into the room wakes the poll at once.

PRD "Team chat responsiveness" acceptance #5: a three-hop relay must never
show a zero-sign window. The gap it is
about is not inside a turn, it is BETWEEN turns. A finishes and posts; B is
mentioned in that post; B waits for the next poll tick to be noticed. At the
adaptive interval that is 3-12s of nothing happening, per hop, and it stacks
with every relay.

The fix is one `asyncio.Event`: a successful team-room post sets it, and the
poll loop's sleep waits on it as well as on stop. The room's own delivery is
what schedules the next hop, instead of a timer noticing later.

Scope, stated because it is easy to over-read: this covers every post made by
the TRIGGER process — the team-room reply and the leader patrol, both routed
through `_post_to_room`. An agent that calls the
`bus_send` MCP tool posts from the MCP server process, where an in-process
Event cannot reach; that path still waits for the poll. Team relay — the PRD's
subject — is covered; peer DM via the tool is not.

The second half of this file pins the property acceptance #5 actually states:
across a three-hop relay there is no sampling point where the room shows every
member idle while a message sits unprocessed. That is the machine-checkable
form of "no dead silence".
"""
from __future__ import annotations

import asyncio

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus import wake_signal

from ._team_turn import speak_in_room
from narranexus.platform.message_bus.message_bus_trigger import (
    TEAM_ROOM_OWNER_PREFIX,
    MessageBusTrigger,
    TurnResult,
)

CHANNEL = "ch_relay"
TEAM = "t_relay"
A, B, C = "agent_a", "agent_b", "agent_c"
USER = "usr_1"


async def _seed_room(db):
    await db.insert("bus_channels", {
        "channel_id": CHANNEL, "name": "relay", "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    })
    for aid, name in ((A, "Ana"), (B, "Bo"), (C, "Cy")):
        await db.insert("bus_channel_members", {"channel_id": CHANNEL, "agent_id": aid})
        await db.insert("agents", {"agent_id": aid, "agent_name": name,
                                   "created_by": USER})
    await db.insert("teams", {
        "team_id": TEAM, "owner_user_id": USER, "name": "Relay",
        "lead_agent_id": A,
    })


@pytest.fixture(autouse=True)
def _db_factory(db_client, monkeypatch):
    async def _get_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _get_db
    )


def _trigger(db, replies: dict[str, str]) -> MessageBusTrigger:
    t = MessageBusTrigger(bus=LocalMessageBus(backend=db._backend))

    async def _invoke(**kwargs):
        aid = kwargs.get("agent_id")
        text = replies.get(aid, "ok")
        # The agent speaks by CALLING A TOOL now (2026-08-17), so the stub goes
        # through the same path the tool does — `team_posting.post_team_reply`,
        # via the shared helper. A stub that inserted a row directly would pass
        # while @mention resolution, the hop cap and the wake all sat unrun.
        if kwargs.get("team_room") and text:
            await speak_in_room(
                db=db, bus=t._bus, agent_id=aid, team_id=TEAM,
                channel_id=CHANNEL, text=text, event_id=f"evt_{aid}",
            )
        return TurnResult(text=text, event_id=f"evt_{aid}")

    t._invoke_runtime = _invoke  # type: ignore[method-assign]
    return t


# ── the wake itself ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_team_reply_wakes_the_poll_loop(db_client):
    """The wake moved from an in-process Event to a DB signal, and had to.

    A team reply is a tool call, made on the MCP server — a process where an
    `asyncio.Event` cannot be reached. So what has to advance is the signal the
    poll loop reads while it sleeps; asserting on `_wake_event` here would now be
    asserting about the trigger's own posts (patrol, notices), not the relay this
    test is named for.
    """
    trig = _trigger(db_client, {A: "@Bo your turn"})
    await _seed_room(db_client)
    await trig._bus.send_message(
        from_agent=USER, to_channel=CHANNEL, content="@Ana start", mentions=[A]
    )
    before = await wake_signal.read(db_client)

    await trig._process_lane(A, CHANNEL)

    assert await wake_signal.read(db_client) != before, (
        "A posted into the room and nothing asked the loop to look again — "
        "B waits out a full poll interval for a message already delivered"
    )


@pytest.mark.asyncio
async def test_a_failed_room_post_does_not_wake(db_client, monkeypatch):
    """Waking is for work that landed. A post that threw created nothing."""
    trig = _trigger(db_client, {A: "@Bo your turn"})
    await _seed_room(db_client)
    await trig._bus.send_message(
        from_agent=USER, to_channel=CHANNEL, content="@Ana start", mentions=[A]
    )

    async def _explode(**_k):
        raise RuntimeError("room post failed")

    monkeypatch.setattr(trig._bus, "send_message", _explode)
    monkeypatch.setattr(trig, "_announce_failed_room_post",
                        lambda *a, **k: asyncio.sleep(0))

    await trig._process_lane(A, CHANNEL)

    assert not trig._wake_event.is_set()


def test_every_in_process_post_goes_through_the_waking_helper():
    """Structural: nobody may call `_bus.send_message` directly from here.

    Reviewed and found the hard way — this file originally pinned the team-reply
    site only, and the leader-patrol post (which @-mentions members under the
    room's own marker) sat right next to it with no wake. A per-call-site test
    can only ever catch the sites someone remembered to test; this catches the
    NEXT one too.
    """
    import inspect

    from narranexus.platform.message_bus import message_bus_trigger as mod

    import re

    src = inspect.getsource(mod)
    head, _, rest = src.partition("    async def _post_to_room")
    assert rest, "_post_to_room is gone — the invariant has no owner"

    # Cut at the helper's OWN end, not at some later landmark: excluding
    # everything up to `_wake` would also excuse any method added in between —
    # and "another posting helper" is the most natural thing to put there.
    nxt = re.search(r"\n    (?:async )?def ", rest)
    helper, after = (rest[: nxt.start()], rest[nxt.start():]) if nxt else (rest, "")

    outside = head + after
    assert "self._bus.send_message(" not in outside, (
        "a direct self._bus.send_message() bypasses _post_to_room and will not "
        "wake the poll loop — route it through the helper"
    )
    assert "self._bus.send_message(" in helper, (
        "the helper stopped posting — this test would now pass vacuously"
    )


@pytest.mark.asyncio
async def test_a_room_marker_post_wakes_the_poll_loop(db_client):
    """A post under the ROOM's marker — the shape the patrol lane uses.

    Named for what it exercises: this drives `_post_to_room` with patrol-shaped
    arguments, NOT `_patrol_body`. What actually guarantees the patrol lane
    wakes the loop is the structural test above; this one documents why that
    shape matters — the line is posted as `team_owner:<team_id>`, so it is not
    self-sent for any member and an @-mentioned teammate becomes a candidate
    immediately.
    """
    trig = _trigger(db_client, {})
    await _seed_room(db_client)
    assert not trig._wake_event.is_set()

    await trig._post_to_room(
        from_agent=f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
        to_channel=CHANNEL,
        content="@Bo the board says this is stuck",
        mentions=[B],
    )

    assert trig._wake_event.is_set(), (
        "the platform spoke and then nobody looked — a teammate it @-mentioned "
        "waits out a full poll interval"
    )


@pytest.mark.asyncio
async def test_the_sleep_returns_early_when_woken():
    """The Event is only useful if the loop's sleep actually watches it."""
    trig = MessageBusTrigger(bus=object())
    trig._current_interval = 30  # far longer than this test may take

    async def _wake_soon():
        await asyncio.sleep(0.05)
        trig._wake()

    asyncio.get_running_loop().create_task(_wake_soon())

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await trig._sleep_until_due()
    elapsed = loop.time() - t0

    assert elapsed < 5.0, f"the wake did not shorten the sleep ({elapsed:.1f}s)"
    assert not trig._wake_event.is_set(), "the wake flag was not cleared"


@pytest.mark.asyncio
async def test_stop_still_ends_the_sleep():
    """The wake must not have displaced the reason the sleep was interruptible."""
    trig = MessageBusTrigger(bus=object())
    trig._current_interval = 30

    async def _stop_soon():
        await asyncio.sleep(0.05)
        trig._stop_event.set()

    asyncio.get_running_loop().create_task(_stop_soon())

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await trig._sleep_until_due()
    assert loop.time() - t0 < 5.0


# ── acceptance #5: no zero-sign window across three hops ────────────────────

async def _room_shows_a_sign_of_life(db, bus, members) -> tuple[bool, dict]:
    """Would the team console show ANY member as non-idle right now?

    Uses the backend's own `_member_activity`, not a reimplementation — the
    question is what the room actually renders, and a private copy of the rule
    here would pass while the UI stayed silent.
    """
    from narranexus_plugins.teams.routes import _member_activity

    rows = await _member_activity(db, bus, CHANNEL, members)
    states = {r["agent_id"]: r["status"] for r in rows}
    return any(s != "idle" for s in states.values()), states


@pytest.mark.asyncio
async def test_a_three_hop_relay_never_goes_completely_silent(db_client):
    """A -> B -> C, sampled at every handoff.

    The failure this guards against is the one the 2026-08-01 room saw: a
    message is in flight, and the room shows nobody doing anything.
    """
    trig = _trigger(db_client, {
        A: "@Bo picking this up",
        B: "@Cy over to you",
        C: "done",
    })
    await _seed_room(db_client)
    members = [A, B, C]

    await trig._bus.send_message(
        from_agent=USER, to_channel=CHANNEL, content="@Ana start", mentions=[A]
    )

    samples: list[dict] = []
    for hop_agent in (A, B, C):
        # Sample BEFORE the hop runs: this is the handoff moment, the window
        # where the previous agent has finished and this one has not started.
        alive, states = await _room_shows_a_sign_of_life(db_client, trig._bus, members)
        samples.append(states)
        assert alive, (
            f"dead silence before {hop_agent}'s hop — a message is pending and "
            f"the room shows every member idle: {states}"
        )
        await trig._process_lane(hop_agent, CHANNEL)

    # And once more after the last hop, where the room SHOULD settle to idle:
    # nothing is pending any more, so silence is honest here.
    alive, states = await _room_shows_a_sign_of_life(db_client, trig._bus, members)
    assert not alive, f"the relay finished but the room still claims work: {states}"

    # Every handoff sample named a specific member as the one to look at.
    for states in samples:
        assert any(s in ("queued", "running") for s in states.values()), states
