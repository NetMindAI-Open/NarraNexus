"""
@file_name: test_inbox_datetime_normalization.py
@author: Bin Liang
@date: 2026-05-19
@description: Regression test for `backend.routes.inbox.get_agent_inbox`
crashing with `TypeError: '>' not supported between instances of
'datetime.datetime' and 'str'`.

Observed on EC2 backend container 2026-05-19T04:11:11 → 05:40:35
(22 hits, still occurring at scrape time).

Root cause: MySQL DATETIME(6) is deserialised to `datetime.datetime`,
but the cursor fallback was the string literal `"1970-01-01"`. The
moment a member row had both `last_processed_at` and `last_read_at`
NULL, the comparison `m["created_at"] > cursor` mixed datetime and str.

Fix: a `_to_iso(v) -> str` helper that normalises everything to ISO
strings before comparison / sort. ISO 8601 strings sort
lexicographically in time order.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.inbox as inbox_mod


async def _async_return(value):
    return value


# ──────────────────────────────────────────────────────────────────
# Unit tests on the helper
# ──────────────────────────────────────────────────────────────────


def test_to_iso_passes_through_strings():
    assert inbox_mod._to_iso("2026-05-19T01:23:45") == "2026-05-19T01:23:45"
    assert inbox_mod._to_iso("") == ""


def test_to_iso_converts_datetime():
    dt = datetime(2026, 5, 19, 1, 23, 45, tzinfo=timezone.utc)
    assert inbox_mod._to_iso(dt) == dt.isoformat()


def test_to_iso_none_returns_empty_string():
    assert inbox_mod._to_iso(None) == ""


def test_to_iso_default_literal_sorts_before_real_isos():
    # The literal "1970-01-01" fallback used for missing cursors must
    # sort BEFORE any real ISO timestamp so the comparison correctly
    # marks everything as unread by default.
    assert "1970-01-01" < datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────────
# Integration test reproducing the production exception
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mysql_like_db(monkeypatch):
    """A fake db that returns rows with datetime.datetime created_at
    values, the way aiomysql does for DATETIME(6) columns. The
    in-memory SQLite fixture would store strings and hide the bug."""
    now = datetime(2026, 5, 19, 5, 40, 0, tzinfo=timezone.utc)

    members = [
        {
            "channel_id": "ch_test",
            "agent_id": "agent_a",
            "last_processed_at": None,
            "last_read_at": None,
        }
    ]
    channels = [
        {
            "channel_id": "ch_test",
            "name": "test channel",
            "channel_type": "group",
        }
    ]
    messages = [
        {
            "message_id": "m1",
            "channel_id": "ch_test",
            "from_agent": "agent_b",
            "content": "hi",
            "created_at": now,  # NB: datetime, not str
        }
    ]

    class _FakeDB:
        async def get(self, table, filters=None):
            if table == "bus_channel_members":
                return [r for r in members if all(r.get(k) == v for k, v in (filters or {}).items())]
            return []

        async def get_by_ids(self, table, id_field, ids):
            if table == "bus_channels":
                return [c for c in channels if c[id_field] in ids]
            if table == "agents":
                return []
            return []

        async def get_one(self, table, filters):
            return None

        async def execute(self, query, params=None, fetch=True):
            # SELECT * FROM bus_messages WHERE channel_id = %s ORDER BY ...
            cid = params[0] if params else None
            return [m for m in messages if m["channel_id"] == cid]

    fake_db = _FakeDB()
    monkeypatch.setattr(inbox_mod, "_get_db", lambda: _async_return(fake_db))
    return fake_db


def test_get_agent_inbox_handles_datetime_created_at(mysql_like_db):
    app = FastAPI()
    app.include_router(inbox_mod.router, prefix="/api/agent-inbox")
    client = TestClient(app)

    resp = client.get("/api/agent-inbox", params={"agent_id": "agent_a"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Before the fix this returned success=False with an "error" field
    # containing the TypeError; after the fix the inbox renders with 1
    # unread message.
    assert body["success"] is True, body
    assert body["total_unread"] == 1, body
    assert len(body["rooms"]) == 1
    room = body["rooms"][0]
    assert room["unread_count"] == 1
    assert len(room["messages"]) == 1
    assert room["messages"][0]["is_read"] is False
