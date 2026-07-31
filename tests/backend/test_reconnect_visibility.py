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
from xyz_agent_context.utils.timezone import utc_now


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
