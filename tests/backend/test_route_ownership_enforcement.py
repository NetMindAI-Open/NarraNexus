"""
@file_name: test_route_ownership_enforcement.py
@date: 2026-08-11
@description: IDOR fix (security audit P0-1) — the awareness and social-network
read/write routes now enforce agent ownership. A non-owner caller is denied
(403); local mode (no request identity) still no-ops. Ownership resolution is
faked so the test needs no DB.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.routes import _ownership
from narranexus_plugins.awareness_module import routes as aw
from narranexus_plugins.social_network_module import routes as sn


def _client(monkeypatch, router, *, caller_user, owner_user):
    class _FakeAgentRepo:
        def __init__(self, db):
            pass

        async def resolve_owner(self, agent_id):
            return owner_user

    async def _fake_db():
        return object()

    monkeypatch.setattr(_ownership, "get_db_client", _fake_db)
    monkeypatch.setattr(_ownership, "AgentRepository", _FakeAgentRepo)

    app = FastAPI()

    @app.middleware("http")
    async def _set_user(request: Request, call_next):
        if caller_user is not None:
            request.state.user_id = caller_user
        return await call_next(request)

    app.include_router(router, prefix="/api/agents")
    return TestClient(app, raise_server_exceptions=False)


# ── awareness ──────────────────────────────────────────────────────────

def test_awareness_get_denies_non_owner(monkeypatch):
    c = _client(monkeypatch, aw.router, caller_user="attacker", owner_user="real_owner")
    assert c.get("/api/agents/agent_x/awareness").status_code == 403


def test_awareness_put_denies_non_owner(monkeypatch):
    c = _client(monkeypatch, aw.router, caller_user="attacker", owner_user="real_owner")
    r = c.put("/api/agents/agent_x/awareness", json={"awareness": "pwned"})
    assert r.status_code == 403


# ── social network (the three GET endpoints the audit flagged) ─────────

@pytest.mark.parametrize(
    "path",
    [
        "/api/agents/agent_x/social-network",
        "/api/agents/agent_x/social-network/user_1",
        "/api/agents/agent_x/social-network/search?query=hi",
    ],
)
def test_social_get_denies_non_owner(monkeypatch, path):
    c = _client(monkeypatch, sn.router, caller_user="attacker", owner_user="real_owner")
    assert c.get(path).status_code == 403


# ── local mode (no request identity) still no-ops ──────────────────────

def test_local_mode_bypasses_ownership(monkeypatch):
    # No middleware user_id → _caller_user_id is None → assert_owned returns
    # without touching the (unset) db mocks, and the handler runs. awareness
    # GET on an agent with no instance returns success:false (not a 403).
    async def _no_instance(agent_id):
        return None

    monkeypatch.setattr(aw, "_find_awareness_instance", _no_instance)
    c = _client(monkeypatch, aw.router, caller_user=None, owner_user="whoever")
    r = c.get("/api/agents/agent_x/awareness")
    assert r.status_code == 200
    assert r.json()["success"] is False
