"""
@file_name: test_netmind_login_route.py
@author: NarraNexus
@date: 2026-06-11
@description: e2e tests for POST /api/auth/netmind-login (Phase 1 user-system
unification — "passport for visa" exchange).

Mounts the auth router on a fresh FastAPI app with an in-memory SQLite
db_client; the NetmindAuthClient is monkeypatched so no network is involved.

Covers:
- happy path: verify -> upsert -> own JWT issued (decodable, right claims)
- first login seeds the free-tier quota; second login does not re-seed
- quota seeding failure does NOT fail the login
- second login: is_new_user=False, no duplicate row
- invalid NetMind token -> HTTP 401; NetMind upstream trouble -> HTTP 502
- cloud-only guard: local mode -> 404
- /api/auth/netmind-login is in AUTH_EXEMPT_PATHS (middleware lets it through)
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.services.netmind_auth_client import (
    NetmindAuthError,
    NetmindUpstreamError,
    NetmindUser,
)


_CODE = "c" * 32


class _FakeNetmindClient:
    """Programmable stand-in for NetmindAuthClient."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    async def verify_token(self, token: str) -> NetmindUser:
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FakeQuotaService:
    def __init__(self, fail=False):
        self.fail = fail
        self.seeded = []

    async def init_for_user(self, user_id: str):
        if self.fail:
            raise RuntimeError("quota backend down")
        self.seeded.append(user_id)

        class _Row:
            initial_input_tokens = 1000
            initial_output_tokens = 1000

        return _Row()


def _make_app(db_client, monkeypatch, netmind_client, *, cloud=True, quota=None):
    import backend.routes.auth as auth_mod

    async def _async_return(value):
        return value

    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: cloud)
    monkeypatch.setattr(
        auth_mod, "_get_netmind_auth_client", lambda: netmind_client
    )

    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")
    app.state.quota_service = quota
    return TestClient(app)


_OK_USER = NetmindUser(
    user_system_code=_CODE,
    email="alice@example.com",
    nickname="Alice",
)


def test_netmind_login_happy_path_issues_own_jwt(db_client, monkeypatch):
    from backend.auth import decode_token

    fake = _FakeNetmindClient(_OK_USER)
    client = _make_app(db_client, monkeypatch, fake, quota=_FakeQuotaService())

    resp = client.post(
        "/api/auth/netmind-login", json={"netmind_token": "jwt-from-netmind"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["user_id"] == _CODE
    assert body["is_new_user"] is True
    assert body["display_name"] == "Alice"
    assert body["email"] == "alice@example.com"
    assert fake.calls == 1

    claims = decode_token(body["token"])
    assert claims["user_id"] == _CODE
    assert claims["role"] == "user"


def test_netmind_login_seeds_quota_once(db_client, monkeypatch):
    quota = _FakeQuotaService()
    client = _make_app(
        db_client, monkeypatch, _FakeNetmindClient(_OK_USER), quota=quota
    )

    first = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})
    second = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})

    assert first.json()["is_new_user"] is True
    assert first.json()["has_system_quota"] is True
    assert second.json()["is_new_user"] is False
    assert quota.seeded == [_CODE]  # exactly once


def test_netmind_login_quota_failure_does_not_block_login(db_client, monkeypatch):
    quota = _FakeQuotaService(fail=True)
    client = _make_app(
        db_client, monkeypatch, _FakeNetmindClient(_OK_USER), quota=quota
    )

    resp = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert resp.json()["has_system_quota"] is False


def test_netmind_login_invalid_token_is_401(db_client, monkeypatch):
    fake = _FakeNetmindClient(NetmindAuthError("bad token"))
    client = _make_app(db_client, monkeypatch, fake)

    resp = client.post("/api/auth/netmind-login", json={"netmind_token": "x"})

    assert resp.status_code == 401


def test_netmind_login_upstream_trouble_is_502(db_client, monkeypatch):
    fake = _FakeNetmindClient(NetmindUpstreamError("netmind down"))
    client = _make_app(db_client, monkeypatch, fake)

    resp = client.post("/api/auth/netmind-login", json={"netmind_token": "x"})

    assert resp.status_code == 502


def test_netmind_login_is_cloud_only(db_client, monkeypatch):
    client = _make_app(
        db_client, monkeypatch, _FakeNetmindClient(_OK_USER), cloud=False
    )

    resp = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})

    assert resp.status_code == 404


def test_netmind_login_path_is_auth_exempt():
    from backend.auth import AUTH_EXEMPT_PATHS

    assert "/api/auth/netmind-login" in AUTH_EXEMPT_PATHS
