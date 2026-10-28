"""
@file_name: test_reconnect_visibility.py
@author:
@date: 2026-07-31
@description: The observe endpoint's visibility rule — "does the
requester own the AGENT that ran this run", not "does the requester
equal events.user_id".

``events.user_id`` stores the run's TRIGGERING key: the requester on
chat runs, but the SENDER on team-bus runs (``usr_<uid>``, or a
relaying agent_id on agent→agent turns). Comparing the requester
against it verbatim forbade every observable team run — the flagship
surface of run observation (PR #219 review round 2, Critical #1).
"""
from __future__ import annotations

import asyncio

import pytest

import backend.routes.websocket as ws_module
from narranexus.platform.utils.timezone import utc_now


class _FakeWS:
    """Just enough WebSocket for _handle_reconnect's read-only paths."""

    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, frame: dict):
        self.sent.append(frame)

    async def close(self):
        self.closed = True

    async def receive(self):
        await asyncio.Event().wait()


async def _seed_agent(db, agent_id: str, owner: str):
    await db.insert("agents", {
        "agent_id": agent_id,
        "agent_name": "Ana",
        "created_by": owner,
    })


async def _seed_completed_run(db, event_id: str, *, agent_id: str, user_id: str):
    now = utc_now()
    await db.insert("events", {
        "event_id": event_id,
        "trigger": "message_bus",
        "trigger_source": "test",
        "agent_id": agent_id,
        "user_id": user_id,  # the TRIGGERING key, not ownership
        "state": "completed",
        "final_output": "done",
        "created_at": now,
        "updated_at": now,
    })


@pytest.fixture(autouse=True)
def use_test_db(monkeypatch, db_client):
    async def fake_get_db_client():
        return db_client
    monkeypatch.setattr(ws_module, "get_db_client", fake_get_db_client)


@pytest.mark.asyncio
async def test_team_run_with_user_sender_key_is_visible_to_agent_owner(db_client):
    # A human spoke in the room: events.user_id = "usr_<uid>" — the
    # prefixed sender key, NOT the owner's id.
    await _seed_agent(db_client, "ag_team", owner="binliang")
    await _seed_completed_run(
        db_client, "evt_vis1", agent_id="ag_team", user_id="usr_binliang",
    )
    ws = _FakeWS()
    await ws_module._handle_reconnect(ws, run_id="evt_vis1", requesting_user_id="binliang")

    types = [f["type"] for f in ws.sent]
    assert "run_reconnect" in types and "run_ended" in types
    assert not any(f.get("error_type") == "Forbidden" for f in ws.sent)


@pytest.mark.asyncio
async def test_relayed_run_with_agent_sender_key_is_visible_to_agent_owner(db_client):
    # Agent→agent relay: events.user_id = the sending agent's id.
    await _seed_agent(db_client, "ag_team", owner="binliang")
    await _seed_completed_run(
        db_client, "evt_vis2", agent_id="ag_team", user_id="ag_other",
    )
    ws = _FakeWS()
    await ws_module._handle_reconnect(ws, run_id="evt_vis2", requesting_user_id="binliang")
    assert not any(f.get("error_type") == "Forbidden" for f in ws.sent)
    assert any(f["type"] == "run_ended" for f in ws.sent)


@pytest.mark.asyncio
async def test_non_owner_is_forbidden(db_client):
    await _seed_agent(db_client, "ag_team", owner="binliang")
    await _seed_completed_run(
        db_client, "evt_vis3", agent_id="ag_team", user_id="usr_binliang",
    )
    ws = _FakeWS()
    await ws_module._handle_reconnect(ws, run_id="evt_vis3", requesting_user_id="mallory")
    assert any(f.get("error_type") == "Forbidden" for f in ws.sent)
    assert ws.closed
    assert not any(f["type"] == "run_reconnect" for f in ws.sent)


@pytest.mark.asyncio
async def test_unresolvable_owner_is_forbidden(db_client):
    # No agents row at all → ownership cannot be proven → invisible
    # (event_stream carries the full thinking/tool trace).
    await _seed_completed_run(
        db_client, "evt_vis4", agent_id="ag_ghost", user_id="usr_binliang",
    )
    ws = _FakeWS()
    await ws_module._handle_reconnect(ws, run_id="evt_vis4", requesting_user_id="binliang")
    assert any(f.get("error_type") == "Forbidden" for f in ws.sent)


@pytest.mark.asyncio
async def test_chat_run_fast_path_needs_no_agents_row(db_client):
    # Chat-style runs store the requester directly — the equality fast
    # path must keep working even if the agents row is gone.
    await _seed_completed_run(
        db_client, "evt_vis5", agent_id="ag_gone", user_id="binliang",
    )
    ws = _FakeWS()
    await ws_module._handle_reconnect(ws, run_id="evt_vis5", requesting_user_id="binliang")
    assert not any(f.get("error_type") == "Forbidden" for f in ws.sent)
    assert any(f["type"] == "run_ended" for f in ws.sent)


def test_format_dt_attaches_utc_to_naive_datetimes():
    """review #349 I1: MySQL's DATETIME(6) strips tzinfo, so the driver hands
    back NAIVE datetimes — an offset-less isoformat() string is then read as
    LOCAL time by the browser's Date.parse, skewing run_reconnect's
    started_at/input_timestamp by the viewer's UTC offset. Naive values must
    gain an explicit UTC offset, with microseconds preserved (format_for_api
    truncates to seconds and would break the exact-millisecond dedup)."""
    from datetime import datetime, timezone

    from backend.routes.websocket import _format_dt

    naive = datetime(2026, 8, 14, 16, 0, 41, 109740)
    s = _format_dt(naive)
    assert s is not None and s.endswith("+00:00")
    assert "16:00:41.109740" in s  # microseconds survive

    aware = datetime(2026, 8, 14, 16, 0, 41, tzinfo=timezone.utc)
    assert _format_dt(aware) == aware.isoformat()  # offset trusted as-is

    assert _format_dt(None) is None
    assert _format_dt("2026-08-14 16:00:41") == "2026-08-14 16:00:41"


@pytest.mark.asyncio
async def test_reconnect_frame_timestamps_carry_utc_offset(db_client, monkeypatch):
    """review #349 r2 M4, the WIRING pin: the unit test above proves
    _format_dt converts; this proves the frame fields actually go THROUGH it.
    The events row is doctored to carry naive datetime OBJECTS (what a MySQL
    driver returns) — going through a real SQLite write would serialize them
    to '+00:00' strings and test the permanently-green side again."""
    from datetime import datetime as _dt

    await _seed_agent(db_client, "ag_tz", owner="binliang")
    await _seed_completed_run(
        db_client, "evt_tz1", agent_id="ag_tz", user_id="usr_binliang",
    )

    naive = _dt(2026, 8, 14, 16, 0, 41, 109740)  # no tzinfo
    real_get_one = db_client.get_one

    async def naive_events_get_one(table, *a, **k):
        row = await real_get_one(table, *a, **k)
        if table == "events" and row:
            row = dict(row)
            row["started_at"] = naive
            row["created_at"] = naive
        return row

    monkeypatch.setattr(db_client, "get_one", naive_events_get_one)
    ws = _FakeWS()
    await ws_module._handle_reconnect(ws, run_id="evt_tz1", requesting_user_id="binliang")

    frame = next(f for f in ws.sent if f["type"] == "run_reconnect")
    # both datetime fields must leave with an explicit UTC offset, microseconds intact
    assert frame["started_at"].endswith("+00:00"), frame["started_at"]
    assert "16:00:41.109740" in frame["started_at"]
    assert frame["input_timestamp"].endswith("+00:00"), frame["input_timestamp"]
    # microseconds matter MOST here: input_timestamp must match the persisted
    # chat row by exact millisecond (the frame comment's contract) — a
    # field-specific formatter that truncates would break dedup silently
    assert "16:00:41.109740" in frame["input_timestamp"]
