"""
@file_name: test_inbox_self_sender_name.py
@author:
@date: 2026-08-21
@description: The inbox message card must show the CURRENT agent's display
name, not its raw ``agent_<hex>`` id.

The agent's own (OUTBOUND) reply is stored with an empty ``sender_name`` (the
writer has no display name at record time); the counterpart (INBOUND) row
carries a real name. The route resolves the agent's display name once for the
member strip — this pins that the SAME name is used for the outbound message's
``sender_name`` instead of falling through to ``sender_id`` (the id).
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.inbox as inbox_mod
from backend.routes.inbox import router as inbox_router
from narranexus.platform.channel.inbox_recorder import INBOUND, OUTBOUND

AGENT_ID = "agent_5588fd1f17e4"
AGENT_NAME = "Daolai"


class _DB:
    """Minimal db double covering exactly the calls the route makes."""

    async def get(self, table, filters=None, **_kw):
        if table == "inbox_threads":
            return [{
                "thread_id": "thr_1",
                "title": "chat with Bob",
                "counterpart_id": "agent_bob",
                "counterpart_name": "Bob",
                "last_read_at": "1970-01-01",
                "last_message_at": "2026-08-21T06:00:00",
            }]
        if table == "inbox_thread_messages":
            return [
                {"message_id": "m_in", "direction": INBOUND, "sender_id": "agent_bob",
                 "sender_name": "Bob", "content": "hi", "created_at": "2026-08-21T06:00:00"},
                {"message_id": "m_out", "direction": OUTBOUND, "sender_id": AGENT_ID,
                 "sender_name": "", "content": "hello back", "created_at": "2026-08-21T06:00:01"},
            ]
        return []

    async def get_one(self, table, filters=None, **_kw):
        if table == "agents":
            return {"agent_id": AGENT_ID, "agent_name": AGENT_NAME}
        return None


def _client(monkeypatch):
    async def _db():
        return _DB()

    monkeypatch.setattr(inbox_mod, "_get_db", _db)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        # No x-test-user -> local mode -> assert_owned no-ops.
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(inbox_router, prefix="/api/agent-inbox")
    return TestClient(app, raise_server_exceptions=False)


def _messages(monkeypatch):
    client = _client(monkeypatch)
    r = client.get("/api/agent-inbox", params={"agent_id": AGENT_ID})
    assert r.status_code == 200, r.text
    rooms = r.json()["rooms"]
    assert rooms, r.text
    return {m["message_id"]: m for m in rooms[0]["messages"]}


def test_own_message_shows_agent_name_not_id(monkeypatch):
    msgs = _messages(monkeypatch)
    # The bug: outbound sender_name was "" -> API fell back to sender_id (the
    # raw agent id). Non-vacuous: without the fix this equals AGENT_ID.
    assert msgs["m_out"]["sender_name"] == AGENT_NAME
    assert msgs["m_out"]["sender_name"] != AGENT_ID


def test_counterpart_name_is_unchanged(monkeypatch):
    msgs = _messages(monkeypatch)
    assert msgs["m_in"]["sender_name"] == "Bob"
