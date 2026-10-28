"""
@file_name: test_inbox_owner_gate.py
@author:
@date: 2026-08-12
@description: Route-level proof that the agent-inbox routes enforce agent
ownership (SEC-03 read + SEC-05 mark-read, Mark's IDOR batch).

`GET /api/agent-inbox`, `PUT /api/agent-inbox/{message_id}/read` and
`POST /api/agent-inbox/rooms/{room_id}/read` took `agent_id` straight from
the query string with no identity check — any logged-in user could read
another agent's whole inbox or tamper with its read cursors. These tests
pin the fix: every route runs `agent_id` through `assert_owned` first.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
import backend.routes.inbox as inbox_mod
from backend.routes.inbox import router as inbox_router


@pytest.fixture
def client(monkeypatch):
    async def _own_db():
        return object()

    monkeypatch.setattr(own, "get_db_client", _own_db)

    async def _resolve(self, agent_id):
        return {"agent_mine": "u1", "agent_theirs": "u2"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(inbox_router, prefix="/api/agent-inbox")
    return TestClient(app, raise_server_exceptions=False)


def test_get_inbox_denies_non_owner(client):
    r = client.get("/api/agent-inbox", params={"agent_id": "agent_theirs"}, headers={"x-test-user": "u1"})
    assert r.status_code == 403


def test_mark_message_read_denies_non_owner(client):
    r = client.put(
        "/api/agent-inbox/msg_1/read",
        params={"agent_id": "agent_theirs"},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 403


def test_mark_room_read_denies_non_owner(client):
    r = client.post(
        "/api/agent-inbox/rooms/room_1/read",
        params={"agent_id": "agent_theirs"},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 403


def test_get_inbox_allows_owner(client, monkeypatch):
    # Guard against an over-strict gate that 403s the owner too: agent_mine is
    # owned by u1, so u1 must get through. Stub the db so the route returns its
    # empty-inbox 200 rather than touching a real database.
    class _DB:
        async def get(self, _table, _filters=None, **_kw):
            return []

    async def _db():
        return _DB()

    monkeypatch.setattr(inbox_mod, "_get_db", _db)
    r = client.get(
        "/api/agent-inbox",
        params={"agent_id": "agent_mine"},
        headers={"x-test-user": "u1"},
    )
    assert r.status_code == 200
