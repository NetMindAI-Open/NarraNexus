"""
@file_name: test_invite_routes.py
@author: NarraNexus
@date: 2026-05-15
@description: e2e tests for the invite-code flow (post architecture pivot).

Mounts the invite + auth routers on a fresh FastAPI app, patches
get_db_client + _is_cloud_mode so the handlers run against an in-memory
SQLite client. Sets INTERNAL_INVITE_SECRET via monkeypatch so the
server-to-server route accepts requests.

Covers:
- secret enforcement on /api/invite/internal/issue
- code returned in response (server-to-server)
- idempotent re-issue (same email -> same code)
- already_registered short-circuit
- Mode-B cap -> waitlist
- register() atomic consume + reject-reuse
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.repository.invite_code_repository import (
    InviteCodeRepository,
)


_TEST_SECRET = "test-internal-secret-do-not-use-in-prod"
_HDR = {"X-Internal-Secret": _TEST_SECRET}


async def _async_return(value):
    return value


@pytest.fixture
def client(db_client, monkeypatch):
    import backend.routes.invite as invite_mod
    import backend.routes.auth as auth_mod

    monkeypatch.setenv("INTERNAL_INVITE_SECRET", _TEST_SECRET)

    # Route handlers reach the test DB.
    monkeypatch.setattr(invite_mod, "get_db_client", lambda: _async_return(db_client))
    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))

    # /register is still cloud-only; /api/invite/internal/issue intentionally
    # is NOT cloud-gated (it's a server-to-server primitive that should work
    # against either DB backend).
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)

    app = FastAPI()
    app.include_router(invite_mod.router, prefix="/api/invite")
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


# ───────────── /api/invite/internal/issue — secret enforcement ──────────────


def test_issue_without_secret_returns_401(client):
    r = client.post("/api/invite/internal/issue", json={"email": "x@example.com"})
    assert r.status_code == 401


def test_issue_with_wrong_secret_returns_401(client):
    r = client.post(
        "/api/invite/internal/issue",
        json={"email": "x@example.com"},
        headers={"X-Internal-Secret": "nope"},
    )
    assert r.status_code == 401


def test_issue_without_env_secret_returns_503(db_client, monkeypatch):
    """If the operator hasn't set INTERNAL_INVITE_SECRET, the endpoint must
    fail closed rather than accept everything."""
    import backend.routes.invite as invite_mod

    monkeypatch.delenv("INTERNAL_INVITE_SECRET", raising=False)
    monkeypatch.setattr(invite_mod, "get_db_client", lambda: _async_return(db_client))

    app = FastAPI()
    app.include_router(invite_mod.router, prefix="/api/invite")
    c = TestClient(app)
    r = c.post(
        "/api/invite/internal/issue",
        json={"email": "x@example.com"},
        headers={"X-Internal-Secret": "anything"},
    )
    assert r.status_code == 503


# ───────────── /api/invite/internal/issue — happy paths ─────────────────────


def test_issue_returns_code_in_response(client):
    r = client.post(
        "/api/invite/internal/issue",
        json={"email": "alice@example.com"},
        headers=_HDR,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["status"] == "issued"
    # Server-to-server: code IS returned (the website needs it for the email).
    assert isinstance(body["code"], str)
    assert body["code"].startswith("NX-")


@pytest.mark.asyncio
async def test_issue_persists_exactly_one_issued_row(db_client, client):
    client.post(
        "/api/invite/internal/issue",
        json={"email": "bob@example.com"},
        headers=_HDR,
    )
    repo = InviteCodeRepository(db_client)
    rows = await repo.list_for_email("bob@example.com")
    assert len(rows) == 1
    assert rows[0].status == "issued"


@pytest.mark.asyncio
async def test_repeat_issue_returns_same_code_no_new_row(db_client, client):
    first = client.post(
        "/api/invite/internal/issue",
        json={"email": "carol@example.com"},
        headers=_HDR,
    ).json()
    second = client.post(
        "/api/invite/internal/issue",
        json={"email": "carol@example.com"},
        headers=_HDR,
    ).json()
    assert first["code"] == second["code"]
    repo = InviteCodeRepository(db_client)
    rows = await repo.list_for_email("carol@example.com")
    assert len(rows) == 1


def test_invalid_email_rejected(client):
    r = client.post(
        "/api/invite/internal/issue",
        json={"email": "not-an-email"},
        headers=_HDR,
    )
    assert r.status_code == 200
    assert r.json()["success"] is False


@pytest.mark.asyncio
async def test_cap_reached_waitlists(db_client, client, monkeypatch):
    import backend.routes.invite as invite_mod
    monkeypatch.setattr(invite_mod.settings, "invite_auto_issue_cap", 2)

    assert client.post(
        "/api/invite/internal/issue",
        json={"email": "a@example.com"},
        headers=_HDR,
    ).json()["status"] == "issued"
    assert client.post(
        "/api/invite/internal/issue",
        json={"email": "b@example.com"},
        headers=_HDR,
    ).json()["status"] == "issued"
    third = client.post(
        "/api/invite/internal/issue",
        json={"email": "c@example.com"},
        headers=_HDR,
    ).json()
    assert third["status"] == "waitlisted"
    # Waitlisted: no code returned (only issued codes carry a code).
    assert third["code"] is None

    repo = InviteCodeRepository(db_client)
    rows = await repo.list_for_email("c@example.com")
    assert rows[0].status == "waitlisted"


@pytest.mark.asyncio
async def test_already_registered_email_short_circuits(db_client, client):
    repo = InviteCodeRepository(db_client)
    used = await repo.create("done@example.com")
    await repo.consume(used.code, "user_done")

    r = client.post(
        "/api/invite/internal/issue",
        json={"email": "done@example.com"},
        headers=_HDR,
    )
    body = r.json()
    assert body["status"] == "already_registered"
    assert body["code"] is None  # never reveal the used code


# ───────────── register() — consume + reuse-reject ──────────────────────────


@pytest.mark.asyncio
async def test_register_consumes_invite_code(db_client, client):
    issued = client.post(
        "/api/invite/internal/issue",
        json={"email": "newuser@example.com"},
        headers=_HDR,
    ).json()
    code = issued["code"]

    r = client.post(
        "/api/auth/register",
        json={"user_id": "newuser", "password": "secret123", "invite_code": code},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["token"]

    repo = InviteCodeRepository(db_client)
    consumed = await repo.get_by_code(code)
    assert consumed.status == "used"
    assert consumed.used_by_user_id == "newuser"


def test_register_rejects_reused_code(client):
    issued = client.post(
        "/api/invite/internal/issue",
        json={"email": "twice@example.com"},
        headers=_HDR,
    ).json()
    code = issued["code"]

    first = client.post(
        "/api/auth/register",
        json={"user_id": "userone", "password": "secret123", "invite_code": code},
    )
    assert first.json()["success"] is True

    second = client.post(
        "/api/auth/register",
        json={"user_id": "usertwo", "password": "secret123", "invite_code": code},
    )
    assert second.json()["success"] is False
    assert "used" in second.json()["error"].lower()


def test_register_rejects_unknown_code(client):
    r = client.post(
        "/api/auth/register",
        json={"user_id": "ghost", "password": "secret123", "invite_code": "NX-FAKE0000"},
    )
    assert r.json()["success"] is False
    assert "invalid" in r.json()["error"].lower()
