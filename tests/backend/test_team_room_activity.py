"""
@file_name: test_team_room_activity.py
@author: NarraNexus
@date: 2026-08-13
@description: What the sidebar is told about a room it is not looking at.

A team room is an ASYNC space by design: the user hands it work and leaves.
Without this, leaving is a one-way door — the sidebar row looks identical whether
six agents have been talking for ten minutes or nothing happened at all, so the
only way to find out is to open every room and read.

The unread mark itself is client-side (a localStorage watermark, per device, so
the server cannot know it). What the server owes the client is therefore one
timestamp: when did this room last say something WORTH coming back for. Two
exclusions decide that, and both are the difference between a useful mark and one
users learn to ignore:

  * **the user's own message doesn't count.** Otherwise sending a message badges
    the room you just sent it from.
  * **platform lines don't count.** A bulletin notice fires on the user's own
    edit; a roster notice on the user's own member change. Badging those tells
    the user someone replied when in fact they themselves acted.

And one thing this endpoint must NOT do: create the room. Listing teams is a read
— a sidebar refresh that materialised a channel for every team the user has never
opened would create rooms by looking at them.
"""

from __future__ import annotations

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus.system_messages import PLATFORM_MSG_TYPES
from narranexus.platform.schema.team_schema import TEAM_ROOM_OWNER_PREFIX

from narranexus_plugins.teams.routes import _team_room_activity

USER = "usr_1"
TEAM = "team_1"
OTHER_TEAM = "team_2"


async def _room(db, team_id: str) -> str:
    """A team's group channel, created directly — the endpoint must not."""
    bus = LocalMessageBus(backend=db._backend)
    channel_id = await bus.create_channel(name="Room", members=["agent_a"], channel_type="group")
    await db.update(
        "bus_channels",
        {"channel_id": channel_id},
        {"created_by": f"{TEAM_ROOM_OWNER_PREFIX}{team_id}"},
    )
    return channel_id


async def _say(db, channel_id: str, sender: str, content: str, msg_type: str = "text"):
    bus = LocalMessageBus(backend=db._backend)
    return await bus.send_message(
        from_agent=sender, to_channel=channel_id, content=content, msg_type=msg_type
    )


@pytest.mark.asyncio
async def test_a_team_with_no_room_reports_nothing(db_client):
    """A team whose chat was never opened has no channel at all."""
    got = await _team_room_activity(db_client, [TEAM], user_sender=USER)
    assert got.get(TEAM) is None


@pytest.mark.asyncio
async def test_listing_does_not_create_the_room(db_client):
    """Reading the sidebar must not materialise a channel for every team the
    user has never opened."""
    await _team_room_activity(db_client, [TEAM], user_sender=USER)

    rows = await db_client.execute(
        "SELECT channel_id FROM bus_channels WHERE created_by = %s",
        (f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",),
        fetch=True,
    )
    assert not rows


@pytest.mark.asyncio
async def test_the_newest_agent_message_is_reported(db_client):
    channel = await _room(db_client, TEAM)
    await _say(db_client, channel, "agent_a", "first")
    await _say(db_client, channel, "agent_a", "second")

    got = await _team_room_activity(db_client, [TEAM], user_sender=USER)
    assert got[TEAM]["preview"] == "second"
    assert got[TEAM]["last_message_at"]
    assert got[TEAM]["from_agent"] == "agent_a"


@pytest.mark.asyncio
async def test_the_users_own_message_is_not_activity(db_client):
    """Sending a message must not badge the room it was sent from."""
    channel = await _room(db_client, TEAM)
    await _say(db_client, channel, USER, "please look into this")

    got = await _team_room_activity(db_client, [TEAM], user_sender=USER)
    assert got.get(TEAM) is None


@pytest.mark.asyncio
async def test_a_users_message_does_not_hide_an_earlier_reply(db_client):
    """The mark is the newest QUALIFYING message, not the newest message with a
    filter on the tail — a user who replies after an agent has still not read
    everything the agent said."""
    channel = await _room(db_client, TEAM)
    await _say(db_client, channel, "agent_a", "here is the answer")
    await _say(db_client, channel, USER, "thanks")

    got = await _team_room_activity(db_client, [TEAM], user_sender=USER)
    assert got[TEAM]["preview"] == "here is the answer"


@pytest.mark.parametrize("msg_type", PLATFORM_MSG_TYPES)
@pytest.mark.asyncio
async def test_a_platform_line_is_not_activity(db_client, msg_type):
    """Parametrised over the registry rather than a hand-written list: a sixth
    platform type added later must be excluded here without anyone remembering
    this file exists."""
    channel = await _room(db_client, TEAM)
    await _say(db_client, channel, "agent_a", "the platform did something", msg_type=msg_type)

    got = await _team_room_activity(db_client, [TEAM], user_sender=USER)
    assert got.get(TEAM) is None


@pytest.mark.asyncio
async def test_a_platform_line_does_not_hide_a_real_reply(db_client):
    channel = await _room(db_client, TEAM)
    await _say(db_client, channel, "agent_a", "the real answer")
    await _say(db_client, channel, "agent_a", "swept the board", msg_type=PLATFORM_MSG_TYPES[0])

    got = await _team_room_activity(db_client, [TEAM], user_sender=USER)
    assert got[TEAM]["preview"] == "the real answer"


@pytest.mark.asyncio
async def test_the_filter_names_platform_types_rather_than_allowing_known_ones(db_client):
    """An unrecognised msg_type counts as a real message.

    The filter EXCLUDES the platform's own types instead of admitting a list of
    known-good ones, and that direction is the decision. A new ordinary type
    (say a voice message) would be invisible under an allowlist — the room would
    look silent while it was talking — whereas a new platform type shows up one
    time too many, which is noticed and fixed. The failure modes are not
    symmetric, so the safe default is "unknown means real".
    """
    channel = await _room(db_client, TEAM)
    await _say(db_client, channel, "agent_a", "a kind of message that did not exist yet",
               msg_type="something_new")

    got = await _team_room_activity(db_client, [TEAM], user_sender=USER)
    assert got[TEAM]["preview"] == "a kind of message that did not exist yet"


@pytest.mark.asyncio
async def test_rooms_do_not_leak_between_teams(db_client):
    ours = await _room(db_client, TEAM)
    theirs = await _room(db_client, OTHER_TEAM)
    await _say(db_client, ours, "agent_a", "ours")
    await _say(db_client, theirs, "agent_a", "theirs")

    got = await _team_room_activity(db_client, [TEAM, OTHER_TEAM], user_sender=USER)
    assert got[TEAM]["preview"] == "ours"
    assert got[OTHER_TEAM]["preview"] == "theirs"


@pytest.mark.asyncio
async def test_the_preview_is_flattened_and_bounded(db_client):
    """It rides in a list response for every team on every sidebar refresh, and
    a row is one line: newlines would break the layout, length would bloat the
    payload for text nobody can see."""
    channel = await _room(db_client, TEAM)
    await _say(db_client, channel, "agent_a", "line one\n\nline  two " + "x" * 400)

    preview = (await _team_room_activity(db_client, [TEAM], user_sender=USER))[TEAM]["preview"]
    assert "\n" not in preview
    assert "  " not in preview
    assert len(preview) <= 201  # 200 + the ellipsis
    assert preview.startswith("line one line two ")


@pytest.mark.asyncio
async def test_no_teams_is_no_queries_worth_of_work(db_client):
    """The empty case has to be explicit: an `IN ()` with no values is a syntax
    error in both dialects, so an empty list must not reach the query."""
    assert await _team_room_activity(db_client, [], user_sender=USER) == {}


@pytest.mark.asyncio
async def test_the_cost_does_not_grow_with_the_number_of_rooms(db_client):
    """One query for every room, not one per room.

    This endpoint is polled every 30s by each open tab, so a per-room query
    multiplies by teams AND by tabs — and it sits on top of an existing
    per-team member query, which is the shape not to add to.
    """
    calls: list[str] = []
    real = db_client.execute

    async def _counting(sql, *a, **kw):
        calls.append(sql)
        return await real(sql, *a, **kw)

    db_client.execute = _counting  # type: ignore[method-assign]
    try:
        for i in range(5):
            channel = await _room(db_client, f"team_{i}")
            await _say(db_client, channel, "agent_a", f"hello {i}")
        calls.clear()

        got = await _team_room_activity(
            db_client, [f"team_{i}" for i in range(5)], user_sender=USER
        )
    finally:
        db_client.execute = real  # type: ignore[method-assign]

    assert len(got) == 5
    assert len(calls) == 2, f"expected channels + messages, got {len(calls)}: {calls}"


@pytest.mark.asyncio
async def test_two_messages_at_the_same_instant_report_one_room_once(db_client):
    """The MAX + self-join returns BOTH rows when two share the newest instant.

    What must hold is that the room is reported ONCE and with a usable preview.
    Which of the two wins is deliberately unspecified: the timestamp is the same
    either way, so the watermark is identical, and neither of two things said in
    the same microsecond is the more correct one to show.
    """
    channel = await _room(db_client, TEAM)
    first = await _say(db_client, channel, "agent_a", "one")
    second = await _say(db_client, channel, "agent_a", "two")
    same = "2026-08-14T10:00:00+00:00"
    for mid in (first, second):
        await db_client.update("bus_messages", {"message_id": mid}, {"created_at": same})

    got = await _team_room_activity(db_client, [TEAM], user_sender=USER)

    assert list(got) == [TEAM]
    assert got[TEAM]["preview"] in ("one", "two")


@pytest.mark.asyncio
async def test_a_team_with_two_rooms_reports_the_newer_one(db_client):
    """The channel→team map is many-to-one and nothing enforces otherwise.

    One room per team is true today, so this is about which way the code fails
    if that stops being true: taking whichever row the result set ended on would
    move the watermark backwards on some refreshes, and a mark that will not
    stay cleared reads as a bug in the counting.
    """
    old_room = await _room(db_client, TEAM)
    new_room = await _room(db_client, TEAM)
    older = await _say(db_client, old_room, "agent_a", "older")
    newer = await _say(db_client, new_room, "agent_a", "newer")
    # Stamped explicitly. Two inserts a few microseconds apart landed in the
    # same second often enough that this test passed alone and failed in a full
    # run — and it was asserting the wall clock, not the rule.
    await db_client.update(
        "bus_messages", {"message_id": older}, {"created_at": "2026-08-14T10:00:00+00:00"}
    )
    await db_client.update(
        "bus_messages", {"message_id": newer}, {"created_at": "2026-08-14T10:05:00+00:00"}
    )

    got = await _team_room_activity(db_client, [TEAM], user_sender=USER)

    assert got[TEAM]["preview"] == "newer"


def _chat_client(monkeypatch, db):
    """The team-chat route over HTTP, auth faked. Same shape as the sibling
    paging tests, because this one has to ask the OTHER side of the invariant."""
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from narranexus_plugins.teams import routes as mod

    async def _get_db():
        return db

    monkeypatch.setattr(mod, "get_db_client", _get_db)

    app = FastAPI()

    @app.middleware("http")
    async def _fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(mod.router, prefix="/api/teams")
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.asyncio
async def test_the_mark_and_the_transcript_agree_on_precision(db_client, monkeypatch):
    """`last_message_at` and the transcript's `created_at` are the SAME string.

    `format_for_api` truncates to whole seconds. The client marks a room read up
    to the newest `created_at` it has RENDERED, and compares that watermark to
    this `last_message_at`. If one side kept sub-second precision and the other
    did not, a reply at :00.800 would compare as :00.000 against a watermark of
    :00.500 — the dot would not appear, for one message, sometimes, with no
    error anywhere.

    So this asks BOTH sides. An earlier version called `format_for_api` itself
    and compared that to the activity value, which only ever asserted "the
    activity side uses format_for_api" — the transcript could switch formatter
    and this stayed green while the dot started flickering. The test was named
    `agree` and interrogated one party.
    """
    await db_client.insert("teams", {
        "team_id": TEAM, "owner_user_id": "usr_1", "name": "Desk",
    })
    # `_room` already stamps the room marker; re-stamping it here would teach
    # the next test to copy four lines it does not need.
    channel = await _room(db_client, TEAM)
    mid = await _say(db_client, channel, "agent_a", "hello")
    await db_client.update(
        "bus_messages", {"message_id": mid}, {"created_at": "2026-08-14T10:00:00.800000+00:00"}
    )

    activity = await _team_room_activity(db_client, [TEAM], user_sender=USER)
    r = _chat_client(monkeypatch, db_client).get(
        f"/api/teams/{TEAM}/chat/messages", headers={"X-User-Id": "usr_1"}
    )

    # Without this, a 500 turns into a KeyError below and the failure says
    # nothing about what broke.
    assert r.status_code == 200, r.text
    rendered = r.json()["messages"][-1]["created_at"]
    assert activity[TEAM]["last_message_at"] == rendered
