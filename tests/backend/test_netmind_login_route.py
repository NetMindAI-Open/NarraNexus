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
- power-login guard: unavailable (local, no opt-in) -> 404; available (cloud OR
  local opt-in) -> reachable
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


def _make_app(db_client, monkeypatch, netmind_client, *, power_login=True, quota=None):
    import backend.routes.auth as auth_mod

    async def _async_return(value):
        return value

    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))
    # netmind_login gates on is_power_login_enabled() (the power axis), not the
    # deployment/security axis. Patch that symbol as imported into auth_mod.
    monkeypatch.setattr(auth_mod, "is_power_login_enabled", lambda: power_login)
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


def test_netmind_login_404_when_power_login_disabled(db_client, monkeypatch):
    # Local install with no NARRANEXUS_ENABLE_POWER_LOGIN opt-in.
    client = _make_app(
        db_client, monkeypatch, _FakeNetmindClient(_OK_USER), power_login=False
    )

    resp = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})

    assert resp.status_code == 404


def test_netmind_login_reachable_in_local_when_power_login_enabled(db_client, monkeypatch):
    # Dual-mode: a local deployment that opted into Power login can NetMind-login
    # (power_login=True models both cloud and local-opt-in).
    client = _make_app(
        db_client, monkeypatch, _FakeNetmindClient(_OK_USER),
        power_login=True, quota=_FakeQuotaService(),
    )

    resp = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})

    assert resp.status_code == 200
    assert resp.json()["user_id"] == _CODE


def test_netmind_login_path_is_auth_exempt():
    from backend.auth import AUTH_EXEMPT_PATHS

    assert "/api/auth/netmind-login" in AUTH_EXEMPT_PATHS


def test_netmind_login_schedules_provider_provisioning_in_local(db_client, monkeypatch):
    """The auto-provisioning that mints the two Power providers is wired to fire
    on a LOCAL (power-login-enabled) deployment, not just cloud. We capture the
    fire-and-forget schedule call rather than the background task itself (the
    mint→onboard chain is unit-tested in test_netmind_provisioner.py)."""
    import xyz_agent_context.services.netmind_provisioner as prov_mod

    captured = {}

    def _capture(user_id, netmind_token):
        captured["user_id"] = user_id
        captured["token"] = netmind_token

    # netmind_login imports this symbol inside the function body, so patch it on
    # the source module (not the route module).
    monkeypatch.setattr(prov_mod, "schedule_ensure_netmind_provider", _capture)

    client = _make_app(
        db_client, monkeypatch, _FakeNetmindClient(_OK_USER),
        power_login=True, quota=_FakeQuotaService(),
    )
    resp = client.post(
        "/api/auth/netmind-login", json={"netmind_token": "tok-123"}
    )

    assert resp.status_code == 200
    assert captured == {"user_id": _CODE, "token": "tok-123"}
