"""
@file_name: test_mcp_create_owner_gate.py
@author:
@date: 2026-08-12
@description: Route-level proof that POST /api/agents/{agent_id}/mcps enforces
agent ownership (SEC-06, Mark's IDOR batch).

`create_mcp` was the only route in mcps.py that trusted the URL's `agent_id`
without an ownership check — any logged-in user could write an MCP server
config (arbitrary URL) onto someone else's agent, invisible and undeletable
to the victim. update/delete/validate already checked ownership; this pins
create to the same gate.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
import backend.routes.agents.mcps as mcps_mod
from backend.routes.agents.mcps import router as mcps_router


@pytest.fixture
def client(monkeypatch):
    async def _own_db():
        return object()

    monkeypatch.setattr(own, "get_db_client", _own_db)

    async def _resolve(self, agent_id):
        return {"agent_mine": "u1", "agent_theirs": "u2"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    async def _uid(request):
        return request.headers.get("x-test-user") or None

    monkeypatch.setattr(mcps_mod, "resolve_current_user_id", _uid)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(mcps_router, prefix="/api/agents")
    return TestClient(app, raise_server_exceptions=False)


def test_create_mcp_denies_non_owner(client):
    r = client.post(
        "/api/agents/agent_theirs/mcps",
        headers={"x-test-user": "u1"},
        json={"name": "evil", "url": "https://evil.example.com/sse"},
    )
    assert r.status_code == 403


def test_create_mcp_allows_owner(client, monkeypatch):
    # Owner u1 on their own agent must pass the gate. Stub the db out so the
    # assertion is purely "the gate let the owner through", not the persistence.
    async def _no_db():
        raise RuntimeError("db intentionally unavailable for this assertion")

    monkeypatch.setattr(mcps_mod, "get_db_client", _no_db)
    r = client.post(
        "/api/agents/agent_mine/mcps",
        headers={"x-test-user": "u1"},
        json={"name": "ok", "url": "https://example.com/sse"},
    )
    assert r.status_code != 403
