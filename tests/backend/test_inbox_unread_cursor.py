"""
@file_name: test_inbox_unread_cursor.py
@author:
@date: 2026-08-11
@description: Which cursor the inbox counts against — and why there is now only one.

**The original bug.** `bus_channel_members` carried TWO cursors.
`last_processed_at` was the trigger's bookmark ("I drove this agent past here");
`last_read_at` was what the unread list in every turn's context was measured
against, and what the inbox's "mark room read" button wrote. The inbox counted
against `last_processed_at or last_read_at`, and since the trigger advances the
former on every poll, the count read 0 forever — while the frontend only fires
mark-read when the count is above zero. The single control that could reset
`last_read_at` was therefore unreachable, for exactly the rooms whose backlog
was growing.

**What changed 2026-08-17.** The inbox moved to its own tables and
`inbox_threads` has ONE cursor. The trigger's bookmark is a bus concept and the
inbox is no longer on the bus, so the two can no longer be confused — the bug
class is gone rather than guarded against. `test_a_thread_has_exactly_one_cursor`
is what keeps it gone; it replaces "a current trigger cursor must not hide the
backlog", whose premise no longer exists.

Still pinned, because both are still reachable:
  * the mark-read endpoint clears the count — the escape hatch works
  * the agent's own posts are not its own backlog
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.platform.utils import utc_now
from narranexus.platform.utils.db.schema_registry import get_registered_tables

AGENT = "agent_mia"
USER = "usr_owner"
PEER = "agent_peer"
# An agent-to-agent DM — one of the two conversation kinds the inbox now holds
# (decision ②). A team room would be wrong here: the user is a live participant
# there and it has its own panel.
THREAD = "nx_dm_agent_mia_agent_peer"


@pytest.fixture
def client(db_client, monkeypatch):
    from backend.routes import inbox as inbox_mod

    app = FastAPI()
    app.include_router(inbox_mod.router, prefix="/api/agent-inbox")

    async def _get_db_override():
        return db_client

    monkeypatch.setattr(inbox_mod, "_get_db", _get_db_override)

    async def _allow(*_a, **_k):
        return None

    monkeypatch.setattr(inbox_mod, "assert_owned", _allow)
    return TestClient(app)


def _now() -> str:
    return utc_now().isoformat()


async def _seed(db, *, read_at=None):
    await db.insert("agents", {
        "agent_id": AGENT, "agent_name": "Mia", "created_by": USER,
    })
    thread = {
        "thread_id": THREAD, "owner_user_id": USER, "agent_id": AGENT,
        "source": "agent_dm", "title": "Peer", "counterpart_id": PEER,
        "counterpart_name": "Peer",
    }
    if read_at:
        thread["last_read_at"] = read_at
    await db.insert("inbox_threads", thread)

    # Timestamps must sit in the PAST relative to utc_now(): mark-read advances
    # the cursor to "now", so a future-dated message would survive it and the
    # test would be measuring its own fixture.
    base = utc_now() - timedelta(hours=2)
    for i, (direction, text) in enumerate(
        [("in", "did you see this"), ("out", "my own post"), ("in", "and this")]
    ):
        await db.insert("inbox_thread_messages", {
            "message_id": f"m{i}", "thread_id": THREAD, "direction": direction,
            "sender_id": PEER if direction == "in" else AGENT,
            "sender_name": "Peer" if direction == "in" else "Mia",
            "content": text,
            "created_at": (base + timedelta(minutes=i)).isoformat(),
        })


def test_a_thread_has_exactly_one_cursor():
    """The two-cursor confusion is now structurally impossible.

    Replaces "a current trigger cursor must not hide the backlog": that bug
    needed two cursors on one row to exist at all. `inbox_threads` has one, and
    the trigger's bookmark stays on the bus where it belongs. If a second
    cursor ever appears here, the 2026-08-11 incident can happen again — a
    count that reads 0 forever and a mark-read button nobody can reach.
    """
    table = {t.name: t for t in get_registered_tables()}["inbox_threads"]
    cursor_cols = [
        c.name for c in table.columns
        if c.name.startswith("last_") and c.name.endswith("_at")
        and c.name != "last_message_at"
    ]
    assert cursor_cols == ["last_read_at"], cursor_cols


@pytest.mark.asyncio
async def test_an_unread_backlog_is_reported(db_client, client):
    await _seed(db_client, read_at=None)

    r = client.get(f"/api/agent-inbox?agent_id={AGENT}")

    assert r.status_code == 200
    rooms = r.json()["rooms"]
    assert len(rooms) == 1
    # Two inbound messages unread; the agent's own post is not its own backlog.
    assert rooms[0]["unread_count"] == 2


@pytest.mark.asyncio
async def test_the_read_cursor_is_what_clears_the_count(db_client, client):
    await _seed(db_client, read_at=_now())

    r = client.get(f"/api/agent-inbox?agent_id={AGENT}")

    assert r.json()["rooms"][0]["unread_count"] == 0


@pytest.mark.asyncio
async def test_the_escape_hatch_is_reachable(db_client, client):
    """Mark-read must actually clear a backlog — the control the 2026-08-11
    incident made unreachable."""
    await _seed(db_client, read_at=None)
    before = client.get(f"/api/agent-inbox?agent_id={AGENT}").json()
    assert before["rooms"][0]["unread_count"] == 2

    r = client.post(f"/api/agent-inbox/rooms/{THREAD}/read?agent_id={AGENT}")
    assert r.status_code == 200 and r.json()["success"] is True

    after = client.get(f"/api/agent-inbox?agent_id={AGENT}").json()
    assert after["rooms"][0]["unread_count"] == 0
    assert after["total_unread"] == 0


@pytest.mark.asyncio
async def test_mark_room_read_writes_an_offset_free_cursor(db_client, client):
    """The room cursor must be written offset-FREE (naive UTC).

    `last_read_at` is a `DATETIME(6)` column on MySQL: an offset-bearing literal
    (`…+00:00`) is shifted by the session `time_zone` there while a naive one is
    not, so an offset room cursor and the naive-reading message cursor land on
    different wall clocks under any non-UTC session — silently wedging the
    only-advances guard or leaving new messages unread. On SQLite the reads
    re-normalise to UTC-aware, so it is consistent either way; the fix is for
    MySQL. This asserts on the literal handed to the driver — the property the
    bug lives in — so reverting to `...isoformat()` puts the `+00:00` back and
    turns this red.
    """
    captured: dict = {}
    real_execute = db_client.execute

    async def _spy(query, params=None, *a, **k):
        if "inbox_threads" in query and "last_read_at" in query:
            captured["params"] = params
        return await real_execute(query, params, *a, **k)

    db_client.execute = _spy
    try:
        await _seed(db_client, read_at=None)
        r = client.post(f"/api/agent-inbox/rooms/{THREAD}/read?agent_id={AGENT}")
        assert r.status_code == 200 and r.json()["success"] is True
    finally:
        db_client.execute = real_execute

    # The literal handed to the driver — the property the bug lives in, so it is
    # asserted on the raw value rather than on anything a backend read-back may
    # normalise away.
    written = captured["params"][0]
    assert isinstance(written, str), written
    assert "+" not in written and not written.endswith("Z"), (
        f"mark_room_read wrote a tz-offset cursor: {written!r}"
    )
