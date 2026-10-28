"""
@file_name: test_channel_routes_owner_gate.py
@author:
@date: 2026-08-10
@description: Route-level proof that the channel bind route consults the
canonical ownership helper (PR #258 review, round-1 minor #9 / round-3 #5).

test_ownership.py exercises the helper in isolation; the home_assistant test
breakage showed that "the route is really wired to it" is a separate fact CI
cannot see (it runs ruff only). Since batch 4d.3 every channel binds through
the ONE generic route (``/api/channels/{channel}/bind``), so one TestClient
chain — a NON-owner binding Slack — pins the wiring for all of them.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
from backend.routes.channels.generic import router as channels_router

_BODY = {"agent_id": "agent_theirs", "fields": {"bot_token": "xoxb-0000000000", "app_token": "xapp-0000000000"}}


@pytest.fixture
def client(monkeypatch):
    async def _db():
        return object()

    monkeypatch.setattr(own, "get_db_client", _db)

    async def _resolve(self, agent_id):
        return {"agent_mine": "u1", "agent_theirs": "u2"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)
    import narranexus.platform.module_system  # noqa: F401 — registers the builtin channel descriptors

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("x-test-user") or None
        return await call_next(request)

    app.include_router(channels_router, prefix="/api/channels")
    return TestClient(app)


def test_non_owner_bind_is_denied_by_the_canonical_helper(client):
    r = client.post("/api/channels/slack/bind", headers={"x-test-user": "u1"}, json=_BODY)
    assert r.status_code == 200  # channel routes wrap denials in a 200 payload
    body = r.json()
    assert body["success"] is False
    assert "Permission denied" in body["error"]


def test_unknown_agent_bind_reports_not_found(client):
    r = client.post("/api/channels/slack/bind", headers={"x-test-user": "u1"}, json={**_BODY, "agent_id": "agent_ghost"})
    assert r.json()["success"] is False
    assert "not found" in r.json()["error"]


def test_db_failure_bubbles_as_503_not_200(client, monkeypatch):
    async def _boom(self, agent_id):
        return None  # resolve_owner's lookup-failed sentinel

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _boom)
    r = client.post("/api/channels/slack/bind", headers={"x-test-user": "u1"}, json={**_BODY, "agent_id": "agent_mine"})
    assert r.status_code == 503


def test_agent_id_shape_is_enforced_before_ownership(client):
    # A path/query-looking id never reaches the owner lookup (422 from the body model).
    r = client.post("/api/channels/slack/bind", headers={"x-test-user": "u1"}, json={**_BODY, "agent_id": "../x?y"})
    assert r.status_code == 422
