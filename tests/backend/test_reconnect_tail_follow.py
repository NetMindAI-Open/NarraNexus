"""
@file_name: test_reconnect_tail_follow.py
@author:
@date: 2026-07-31
@description: Cross-process live observation — the observe endpoint's
DB tail-follow branch.

When a run is alive in ANOTHER process (trigger container / other
backend instance) there is no in-memory Broadcaster; the endpoint
follows the recorder's event_stream rows instead. These tests drive
``_follow_run_from_db`` directly with a fake WebSocket: new rows arrive
as ``replay`` frames, a terminal events row ends with ``run_ended``,
a dead heartbeat ends with ``run_ended(failed)``, and a client
disconnect stops the follow without touching the run.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

import backend.routes.websocket as ws_module
from xyz_agent_context.utils.timezone import utc_now


class _FakeWS:
    """Only what the tail-follow touches: receive() (disconnect signal)
    and send_json (frame sink)."""

    def __init__(self, disconnect: bool = False):
        self.sent: list[dict] = []
        self._disconnect = disconnect

    async def receive(self):
        if self._disconnect:
            return {"type": "websocket.disconnect"}
        await asyncio.Event().wait()  # never resolves — client stays put

    async def send_json(self, frame: dict):
        self.sent.append(frame)


async def _seed_running(db, event_id: str, *, last_event_at=None):
    now = utc_now()
    await db.insert("events", {
        "event_id": event_id,
        "trigger": "message_bus",
        "trigger_source": "test",
        "agent_id": "agent_test",
        "user_id": "u_test",
        "state": "running",
        "started_at": now,
        "last_event_at": last_event_at or now,
        "created_at": now,
        "updated_at": now,
    })


async def _seed_stream_row(db, event_id: str, seq: int, kind: str, payload: str):
    await db.insert("event_stream", {
        "event_id": event_id, "seq": seq, "kind": kind,
        "payload": payload, "created_at": utc_now(),
    })


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(ws_module, "_TAIL_FOLLOW_POLL_S", 0.02)


@pytest.mark.asyncio
async def test_new_rows_stream_then_terminal_ends(db_client):
    await _seed_running(db_client, "evt_tf1")
    await _seed_stream_row(db_client, "evt_tf1", 1, "thinking_segment", "already replayed")

    ws = _FakeWS()
    follow = asyncio.create_task(ws_module._follow_run_from_db(
        ws, db=db_client, run_id="evt_tf1", last_seq=1,
    ))

    # New rows written by the (remote) recorder while we follow.
    await _seed_stream_row(db_client, "evt_tf1", 2, "tool_call", '{"tool_name": "Bash"}')
    await _seed_stream_row(db_client, "evt_tf1", 3, "text_delta", "hi")
    await asyncio.sleep(0.1)

    # The run finishes remotely.
    await db_client.update("events", {"event_id": "evt_tf1"}, {
        "state": "completed", "final_output": "hi",
    })
    await asyncio.wait_for(follow, timeout=2)

    replay = [f for f in ws.sent if f["type"] == "replay"]
    assert [(f["seq"], f["kind"]) for f in replay] == [(2, "tool_call"), (3, "text_delta")]
    ended = [f for f in ws.sent if f["type"] == "run_ended"]
    assert len(ended) == 1
    assert ended[0]["state"] == "completed"
    assert ended[0]["final_output"] == "hi"


@pytest.mark.asyncio
async def test_dead_heartbeat_reports_failed(db_client):
    stale = utc_now() - timedelta(seconds=600)
    await _seed_running(db_client, "evt_tf2", last_event_at=stale)

    ws = _FakeWS()
    await asyncio.wait_for(ws_module._follow_run_from_db(
        ws, db=db_client, run_id="evt_tf2", last_seq=0,
    ), timeout=2)

    ended = [f for f in ws.sent if f["type"] == "run_ended"]
    assert len(ended) == 1
    assert ended[0]["state"] == "failed"
    assert "lost" in (ended[0]["error_message"] or "")
    # Read-only observer: the events row itself is NOT mutated here
    # (the periodic sweep owns that).
    row = await db_client.get_one("events", {"event_id": "evt_tf2"})
    assert row["state"] == "running"


@pytest.mark.asyncio
async def test_client_disconnect_stops_follow_quietly(db_client):
    await _seed_running(db_client, "evt_tf3")
    ws = _FakeWS(disconnect=True)
    await asyncio.wait_for(ws_module._follow_run_from_db(
        ws, db=db_client, run_id="evt_tf3", last_seq=0,
    ), timeout=2)
    assert ws.sent == []  # left before any frame; run untouched
    row = await db_client.get_one("events", {"event_id": "evt_tf3"})
    assert row["state"] == "running"
