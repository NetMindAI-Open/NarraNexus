"""
@file_name: test_team_chat_paging.py
@author: NarraNexus
@date: 2026-08-14
@description: Which 200 messages a room shows, and how to reach the rest.

The room asked for `limit=200` with no cursor, and `get_messages` is
`ORDER BY created_at ASC LIMIT n` — the OLDEST n. So a room that had said more
than 200 things opened on its first day and never showed the conversation the
user came back for. Every later poll used `since`, which walks forward from the
newest message on screen, so the room stayed permanently stuck in its own
prehistory. Nothing about it looked broken; it just showed the wrong end.

That is the bug. Paging is the other half: once the newest 200 are what you
open on, the older ones need a way back, which is a `before` cursor walking the
opposite direction from `since`.

The two cursors are deliberately different shapes and the tests say why:
`since` returns the OLDEST after it (catch up in order, never skip), `before`
returns the NEWEST before it (the page immediately above what you are reading).
Getting either backwards produces a room that silently loses messages rather
than one that errors.
"""

from __future__ import annotations

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.schema.team_schema import TEAM_ROOM_OWNER_PREFIX

CHANNEL = "ch_page"


@pytest.fixture
async def bus(db_client):
    await db_client.insert(
        "bus_channels",
        {"channel_id": CHANNEL, "channel_type": "group", "created_by": "team_t1", "name": "T"},
    )
    return LocalMessageBus(backend=db_client._backend)


def _at(i: int) -> str:
    """The ISO string message i is stamped with.

    Cursors are STRINGS on the wire — the route hands the frontend
    `format_for_api(created_at)` and receives the same string back. Passing a
    datetime here instead would compare `str(datetime)` (space separator)
    against the stored ISO text (T separator) and quietly match everything.
    """
    return f"2026-08-14T00:{i // 60:02d}:{i % 60:02d}+00:00"


async def _seed(bus, db_client, n: int):
    """n messages with strictly increasing timestamps, oldest first."""
    for i in range(n):
        mid = await bus.send_message(from_agent="a1", to_channel=CHANNEL, content=f"m{i}")
        await db_client.update("bus_messages", {"message_id": mid}, {"created_at": _at(i)})


@pytest.mark.asyncio
async def test_the_newest_page_is_what_a_room_opens_on(bus, db_client):
    """The bug. Opening a long-running room showed its first day forever."""
    await _seed(bus, db_client, 10)

    page = await bus.get_recent_messages(CHANNEL, limit=3)

    assert [m.content for m in page] == ["m7", "m8", "m9"]


@pytest.mark.asyncio
async def test_the_newest_page_still_reads_oldest_first(bus, db_client):
    """Selected newest-first, handed back in chat order — a transcript that
    rendered newest at the top would be a different bug with the same cause."""
    await _seed(bus, db_client, 5)

    page = await bus.get_recent_messages(CHANNEL, limit=5)

    assert [m.content for m in page] == ["m0", "m1", "m2", "m3", "m4"]


@pytest.mark.asyncio
async def test_before_returns_the_page_immediately_above(bus, db_client):
    await _seed(bus, db_client, 10)

    older = await bus.get_messages_before(CHANNEL, before=_at(7), limit=3)

    assert [m.content for m in older] == ["m4", "m5", "m6"]


@pytest.mark.asyncio
async def test_before_is_exclusive(bus, db_client):
    """An inclusive cursor duplicates the boundary message into the transcript
    on every page load — and the merge dedups by id, so the symptom would be a
    page that silently comes back one short instead."""
    await _seed(bus, db_client, 5)

    older = await bus.get_messages_before(CHANNEL, before=_at(3), limit=5)

    assert [m.content for m in older] == ["m0", "m1", "m2"]


@pytest.mark.asyncio
async def test_the_top_of_the_history_returns_nothing(bus, db_client):
    """How the UI knows to stop offering "load more" — not by guessing from a
    short page, which is also what a sparse window looks like."""
    await _seed(bus, db_client, 3)

    assert await bus.get_messages_before(CHANNEL, before=_at(0), limit=10) == []


@pytest.mark.asyncio
async def test_paging_backwards_reaches_every_message(bus, db_client):
    """Walking the whole transcript in pages must lose nothing and repeat
    nothing — the property that a boundary off by one would break."""
    await _seed(bus, db_client, 25)

    page = await bus.get_recent_messages(CHANNEL, limit=7)
    seen = [m.content for m in page]
    cursor = _at(int(page[0].content[1:]))
    while True:
        older = await bus.get_messages_before(CHANNEL, before=cursor, limit=7)
        if not older:
            break
        seen = [m.content for m in older] + seen
        cursor = _at(int(older[0].content[1:]))

    assert seen == [f"m{i}" for i in range(25)]


@pytest.mark.asyncio
async def test_since_still_walks_forward_from_the_cursor(bus, db_client):
    """The other direction, unchanged: catch up in order, never skip. A `since`
    that returned the NEWEST n would drop everything between."""
    await _seed(bus, db_client, 10)

    fresh = await bus.get_messages(CHANNEL, since=_at(4), limit=3)

    assert [m.content for m in fresh] == ["m5", "m6", "m7"]


@pytest.mark.asyncio
async def test_paging_does_not_cross_channels(bus, db_client):
    await db_client.insert(
        "bus_channels",
        {"channel_id": "other", "channel_type": "group", "created_by": "team_t2", "name": "O"},
    )
    await _seed(bus, db_client, 3)
    await bus.send_message(from_agent="a1", to_channel="other", content="theirs")

    page = await bus.get_recent_messages(CHANNEL, limit=10)

    assert "theirs" not in [m.content for m in page]


# ── the route ───────────────────────────────────────────────────────────────


def _client(monkeypatch, db, page_size: int):
    """The route over HTTP, with auth faked and a small page size.

    Driven for real rather than asserted against `inspect.getsource`: the bug
    this file exists for was the route calling the wrong bus method, and a
    source-text assertion for that goes red on a rename and green on a
    behavioural regression — the wrong way round on both counts.
    """
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from narranexus_plugins.teams import routes as mod

    monkeypatch.setattr(mod, "PAGE_SIZE", page_size)

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
async def test_the_route_opens_on_the_newest_page(db_client, bus, monkeypatch):
    """The bug this file is named for, end to end.

    A room with more history than one page must open on its LAST page. It used
    to open on its first and stay there forever, because every later poll walked
    forward from what was already on screen.
    """
    await db_client.insert("teams", {
        "team_id": "t1", "owner_user_id": "usr_1", "name": "Desk",
    })
    # The room marker the route looks the channel up by. Getting this wrong
    # makes `_get_or_create_team_room` create a SECOND, empty channel and the
    # test reads an empty room rather than failing.
    await db_client.update(
        "bus_channels",
        {"channel_id": CHANNEL},
        {"created_by": f"{TEAM_ROOM_OWNER_PREFIX}t1"},
    )
    await _seed(bus, db_client, 6)

    client = _client(monkeypatch, db_client, page_size=3)
    r = client.get("/api/teams/t1/chat/messages", headers={"X-User-Id": "usr_1"})

    assert r.status_code == 200
    assert [m["content"] for m in r.json()["messages"]] == ["m3", "m4", "m5"]


@pytest.mark.asyncio
async def test_the_route_pages_backwards_from_there(db_client, bus, monkeypatch):
    await db_client.insert("teams", {
        "team_id": "t1", "owner_user_id": "usr_1", "name": "Desk",
    })
    # The room marker the route looks the channel up by. Getting this wrong
    # makes `_get_or_create_team_room` create a SECOND, empty channel and the
    # test reads an empty room rather than failing.
    await db_client.update(
        "bus_channels",
        {"channel_id": CHANNEL},
        {"created_by": f"{TEAM_ROOM_OWNER_PREFIX}t1"},
    )
    await _seed(bus, db_client, 6)

    client = _client(monkeypatch, db_client, page_size=3)
    r = client.get(
        f"/api/teams/t1/chat/messages?before={_at(3)}", headers={"X-User-Id": "usr_1"}
    )

    # The client is built with `raise_server_exceptions=False`, so a 500 comes
    # back as `{"detail": …}` and indexing `["messages"]` raises a KeyError that
    # says nothing about what broke.
    assert r.status_code == 200, r.text
    assert [m["content"] for m in r.json()["messages"]] == ["m0", "m1", "m2"]
