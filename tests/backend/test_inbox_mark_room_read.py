"""
@file_name: test_inbox_mark_room_read.py
@author: Bin Liang
@date: 2026-05-28
@description: Tests for the room-level `POST /api/agent-inbox/rooms/{room_id}/read`
endpoint.

Why this endpoint exists
------------------------
The legacy `PUT /{message_id}/read` advances `last_read_at` to a
specific message's timestamp. The inbox response caps each channel's
`messages[]` at 50 entries, but `unread_count` is computed against all
messages, so a channel with 100 unread + 50 loaded → marking the
latest VISIBLE message leaves a 50-message unread tail behind.

The room endpoint advances the cursor to NOW (server time), which
guarantees zero residual unread for the channel.

Test coverage
- happy path: member exists with last_read_at older than NOW →
  cursor advances, response shape correct
- agent not a member → 200 with success=False, no DB write attempted
- guard: cursor that's already in the future (clock skew) is not moved
  backwards
- mark twice in a row → second is a no-op (idempotent)
- DB exception → returns success=False with error string (no crash)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.inbox as inbox_mod


def _build_client(db_mock):
    """Build a FastAPI TestClient with the inbox router + mocked DB."""
    app = FastAPI()
    app.include_router(inbox_mod.router, prefix="/api/agent-inbox")

    async def _get_db_override():
        return db_mock
    inbox_mod._get_db = _get_db_override  # type: ignore[assignment]
    return TestClient(app)


# ── Happy path ──────────────────────────────────────────────────────────


def test_mark_room_read_happy_path_advances_cursor():
    db = MagicMock()
    db.get_one = AsyncMock(return_value={
        "channel_id": "ch_lark_room42",
        "agent_id": "agent_alice",
        "last_read_at": "2026-05-20T10:00:00+00:00",
    })
    db.execute = AsyncMock(return_value=None)
    client = _build_client(db)

    r = client.post(
        "/api/agent-inbox/rooms/ch_lark_room42/read?agent_id=agent_alice"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["channel_id"] == "ch_lark_room42"
    assert body["last_read_at"]  # ISO timestamp, non-empty
    # Parsable as ISO 8601
    datetime.fromisoformat(body["last_read_at"])

    # The UPDATE was called with the right WHERE clause + the only-advance guard
    assert db.execute.await_count == 1
    sql, params = db.execute.await_args.args
    assert "UPDATE bus_channel_members SET last_read_at" in sql
    assert "last_read_at < %s" in sql, "guard clause must prevent backwards motion"
    # params: (now_iso, channel_id, agent_id, now_iso_guard)
    assert params[1] == "ch_lark_room42"
    assert params[2] == "agent_alice"
    assert params[0] == params[3], "cursor target and guard threshold are the same NOW"


# ── Agent not a member ──────────────────────────────────────────────────


def test_mark_room_read_rejects_non_member():
    """If the agent isn't a member of the channel, we MUST return an
    explicit failure rather than silently passing the UPDATE that
    matches zero rows. Otherwise the frontend would think their click
    "succeeded" and the badge would magically reappear on next poll."""
    db = MagicMock()
    db.get_one = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    client = _build_client(db)

    r = client.post(
        "/api/agent-inbox/rooms/ch_phantom/read?agent_id=agent_alice"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "agent_alice" in body["error"]
    assert "ch_phantom" in body["error"]
    assert db.execute.await_count == 0, "no UPDATE should be issued"


# ── Guard against backwards motion ──────────────────────────────────────


def test_mark_room_read_guard_clause_in_update_sql():
    """The UPDATE includes `OR last_read_at < %s` so a cursor that is
    already in the future (e.g. extreme clock skew, or a parallel
    request that advanced past us) is not pulled backwards."""
    db = MagicMock()
    db.get_one = AsyncMock(return_value={
        "channel_id": "ch1",
        "agent_id": "ag1",
        "last_read_at": "2099-12-31T23:59:59+00:00",
    })
    db.execute = AsyncMock(return_value=None)
    client = _build_client(db)

    client.post("/api/agent-inbox/rooms/ch1/read?agent_id=ag1")
    sql, _params = db.execute.await_args.args
    assert "last_read_at IS NULL OR last_read_at < %s" in sql


# ── Idempotent ──────────────────────────────────────────────────────────


def test_mark_room_read_twice_is_idempotent():
    """Two back-to-back calls: both return success. The DB guard takes
    care of the no-op; the endpoint just always reports success."""
    db = MagicMock()
    db.get_one = AsyncMock(return_value={
        "channel_id": "ch1",
        "agent_id": "ag1",
        "last_read_at": "2026-05-20T10:00:00+00:00",
    })
    db.execute = AsyncMock(return_value=None)
    client = _build_client(db)

    r1 = client.post("/api/agent-inbox/rooms/ch1/read?agent_id=ag1")
    r2 = client.post("/api/agent-inbox/rooms/ch1/read?agent_id=ag1")
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["success"] is True
    assert r2.json()["success"] is True
    assert db.execute.await_count == 2


# ── DB failure ──────────────────────────────────────────────────────────


def test_mark_room_read_db_error_returns_failure_not_500():
    db = MagicMock()
    db.get_one = AsyncMock(return_value={
        "channel_id": "ch1",
        "agent_id": "ag1",
        "last_read_at": None,
    })
    db.execute = AsyncMock(side_effect=RuntimeError("conn reset"))
    client = _build_client(db)

    r = client.post("/api/agent-inbox/rooms/ch1/read?agent_id=ag1")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "conn reset" in body["error"]


# ── Server-time format ─────────────────────────────────────────────────


def test_mark_room_read_uses_iso_with_tz():
    """`last_read_at` cursor comparison is lexicographic (per `_to_iso`),
    so the timestamp we write MUST be ISO 8601 with timezone offset to
    sort correctly against ALL backend types (str / datetime / NULL)."""
    db = MagicMock()
    db.get_one = AsyncMock(return_value={
        "channel_id": "c", "agent_id": "a", "last_read_at": None,
    })
    db.execute = AsyncMock(return_value=None)
    client = _build_client(db)

    r = client.post("/api/agent-inbox/rooms/c/read?agent_id=a")
    ts = r.json()["last_read_at"]
    # Must be ISO 8601 with timezone info
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None
    # Must be very recent (within last 60s)
    delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert delta < 60.0


# ── Pytest-asyncio noop (the routes themselves are async) ──────────────


@pytest.fixture(autouse=True)
def _restore_get_db():
    original = inbox_mod._get_db
    yield
    inbox_mod._get_db = original  # type: ignore[assignment]
