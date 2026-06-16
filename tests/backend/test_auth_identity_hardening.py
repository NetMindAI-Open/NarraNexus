"""
@file_name: test_auth_identity_hardening.py
@author: NarraNexus
@date: 2026-06-11
@description: Identity hardening for the last three auth routes that still
trusted a client-supplied user id (body/query) instead of the middleware-
verified identity: POST /agents (created_by), POST /timezone (user_id),
GET+POST /onboarding (user_id). Forging another user's id in the payload
must be impossible; missing identity must 401.

Same fixture pattern as test_user_settings_routes.py: a mini middleware
maps X-User-Id -> request.state.user_id (what auth_middleware does in prod).
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from xyz_agent_context.repository.user_repository import UserRepository


async def _async_return(value):
    return value


@pytest.fixture
def client(db_client, monkeypatch):
    import backend.routes.auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


@pytest.fixture
def seeded(db_client):
    async def _seed():
        repo = UserRepository(db_client)
        await repo.add_user(user_id="alice", user_type="individual")
        await repo.add_user(user_id="bob", user_type="individual")

    asyncio.get_event_loop().run_until_complete(_seed())


def test_create_agent_uses_authenticated_identity(client, seeded, db_client):
    resp = client.post(
        "/api/auth/agents",
        headers={"X-User-Id": "alice"},
        # a forged created_by for another user must be ignored
        json={"agent_name": "A1", "created_by": "bob"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["agent"]["created_by"] == "alice"


def test_create_agent_requires_identity(client, seeded):
    resp = client.post("/api/auth/agents", json={"agent_name": "A1"})
    assert resp.status_code == 401


def test_timezone_uses_authenticated_identity(client, seeded, db_client):
    resp = client.post(
        "/api/auth/timezone",
        headers={"X-User-Id": "alice"},
        json={"timezone": "Asia/Shanghai", "user_id": "bob"},  # forged id ignored
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is True

    async def _check():
        repo = UserRepository(db_client)
        assert (await repo.get_user_timezone("alice")) == "Asia/Shanghai"
        assert (await repo.get_user_timezone("bob")) == "UTC"

    asyncio.get_event_loop().run_until_complete(_check())


def test_onboarding_get_uses_authenticated_identity(client, seeded):
    resp = client.get("/api/auth/onboarding", headers={"X-User-Id": "alice"})

    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_onboarding_post_uses_authenticated_identity(client, seeded):
    resp = client.post(
        "/api/auth/onboarding",
        headers={"X-User-Id": "alice"},
        json={"first_agent_created": True, "user_id": "bob"},  # forged id ignored
    )
    assert resp.status_code == 200

    mine = client.get("/api/auth/onboarding", headers={"X-User-Id": "alice"})
    theirs = client.get("/api/auth/onboarding", headers={"X-User-Id": "bob"})
    assert mine.json()["progress"]["first_agent_created"] is True
    assert theirs.json()["progress"]["first_agent_created"] is False


def test_onboarding_requires_identity(client, seeded):
    assert client.get("/api/auth/onboarding").status_code == 401
    assert (
        client.post("/api/auth/onboarding", json={"dismissed": True}).status_code
        == 401
    )
