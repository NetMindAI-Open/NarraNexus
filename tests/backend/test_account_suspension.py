"""
@file_name: test_account_suspension.py
@author: Bin Liang
@date: 2026-08-13
@description: The account-suspension mechanism outside the admin route —
the UserStatus.BANNED value, the ban_audit repository, the auth-middleware
account-state gate (with its TTL cache), and the netmind-login gate.

Neutral, policy-free: the tests only exercise a generic account-state switch.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import auth as auth_mod
from backend.auth import auth_middleware, create_token, invalidate_account_state
from backend.auth_errors import ACCOUNT_SUSPENDED, SESSION_DEAD_CODES
from narranexus.platform.repository.ban_audit_repository import (
    ACTION_REINSTATE,
    ACTION_SUSPEND,
    BanAuditRepository,
)
from narranexus.platform.schema import UserStatus


# ------------------------------- enum ------------------------------------

def test_userstatus_has_banned():
    assert UserStatus.BANNED.value == "banned"
    # Distinct from the pre-existing terminal states.
    assert UserStatus.BANNED not in (UserStatus.BLOCKED, UserStatus.DELETED)
    # An existing DB row carrying "banned" is loadable (no coercion error).
    assert UserStatus("banned") is UserStatus.BANNED


def test_all_surfaces_share_the_non_transacting_set():
    """The auth middleware, the admin suspend route, and the login route must
    gate on the SAME shared frozenset so they can never drift apart."""
    from narranexus.platform.schema import NON_TRANSACTING_USER_STATUSES
    import backend.auth as auth_module
    import backend.routes.admin.suspend as suspend_module

    assert NON_TRANSACTING_USER_STATUSES == frozenset(
        {"banned", "blocked", "deleted"}
    )
    # INACTIVE is deliberately NOT a suspension state.
    assert UserStatus.INACTIVE.value not in NON_TRANSACTING_USER_STATUSES
    # Every surface references the one constant.
    assert auth_module.NON_TRANSACTING_USER_STATUSES is NON_TRANSACTING_USER_STATUSES
    assert suspend_module._SUSPENDED_STATES is NON_TRANSACTING_USER_STATUSES


@pytest.mark.asyncio
async def test_admin_secret_helper_status_codes(db_client, monkeypatch):
    """The shared admin-secret gate: 503 unconfigured, 403 wrong/missing, and a
    PASS on the correct secret (reaches the handler, which 404s the unknown
    user — proving the gate let it through). Exercised through one admin route
    (account-state read), which imports the shared _admin_secret helper."""
    import httpx
    from httpx import ASGITransport
    from fastapi import FastAPI
    import backend.routes.admin.suspend as mod

    async def _ret(v):
        return v

    monkeypatch.setattr(mod, "get_db_client", lambda: _ret(db_client))

    async def _run(secret, header):
        monkeypatch.setattr(mod.settings, "admin_secret_key", secret)
        app = FastAPI()
        app.include_router(mod.router)
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            return await ac.get(
                "/api/admin/account-state/whoever",
                headers={"X-Admin-Secret": header} if header is not None else {},
            )

    # 503: no secret configured.
    assert (await _run("", "anything")).status_code == 503
    # 403: wrong secret.
    assert (await _run("right", "wrong")).status_code == 403
    # 403: missing header.
    assert (await _run("right", None)).status_code == 403
    # Correct secret passes the gate -> handler runs -> unknown user -> 404.
    assert (await _run("right", "right")).status_code == 404


def test_account_suspended_is_not_a_session_death_code():
    # A suspended account holds a valid JWT; the frontend must NOT log it out
    # (which would loop on the same token). So the code stays out of the
    # session-death set on purpose.
    assert ACCOUNT_SUSPENDED not in SESSION_DEAD_CODES


# --------------------------- ban_audit repo ------------------------------

@pytest.mark.asyncio
async def test_ban_audit_record_and_history(db_client):
    repo = BanAuditRepository(db_client)

    await repo.record(
        "u1", ACTION_SUSPEND, reason="opaque", evidence_ref="ref", actor="ops"
    )
    await repo.record("u1", ACTION_REINSTATE, actor="ops")

    rows = await repo.history("u1")
    assert [r["action"] for r in rows] == [ACTION_REINSTATE, ACTION_SUSPEND]
    suspend_row = rows[1]
    assert suspend_row["reason"] == "opaque"
    assert suspend_row["evidence_ref"] == "ref"
    assert suspend_row["actor"] == "ops"


@pytest.mark.asyncio
async def test_ban_audit_record_never_raises(db_client):
    """Audit is advisory — a write against a missing table degrades silently."""

    class _Boom:
        async def insert(self, *_a, **_k):
            raise RuntimeError("db gone")

    repo = BanAuditRepository(_Boom())
    # Must not raise.
    await repo.record("u1", ACTION_SUSPEND)


# --------------------- auth-middleware account gate ----------------------

@pytest.fixture
def force_cloud_mode(monkeypatch):
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)


@pytest.fixture
def clear_state_cache():
    auth_mod._account_state_cache.clear()
    yield
    auth_mod._account_state_cache.clear()


@pytest.fixture
def wire_db(monkeypatch, db_client):
    """Point the middleware's lazy get_db_client at the in-memory test DB."""
    import narranexus.platform.utils.db.db_factory as db_factory

    async def _ret():
        return db_client

    monkeypatch.setattr(db_factory, "get_db_client", lambda: _ret())
    return db_client


def _build_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.state.provider_resolver = None

    @app.get("/api/agents")
    async def list_agents():
        return {"ok": True}

    return app


async def _seed(db_client, user_id, status="active"):
    await db_client.insert(
        "users", {"user_id": user_id, "user_type": "individual", "status": status}
    )


@pytest.mark.asyncio
async def test_active_user_passes_the_gate(
    force_cloud_mode, clear_state_cache, wire_db
):
    await _seed(wire_db, "alice_active")
    client = TestClient(_build_app())
    headers = {"Authorization": f"Bearer {create_token('alice_active', 'user')}"}

    r = client.get("/api/agents", headers=headers)

    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_suspended_user_is_rejected_403(
    force_cloud_mode, clear_state_cache, wire_db
):
    await _seed(wire_db, "bob_banned", status="banned")
    client = TestClient(_build_app())
    headers = {"Authorization": f"Bearer {create_token('bob_banned', 'user')}"}

    r = client.get("/api/agents", headers=headers)

    assert r.status_code == 403
    body = r.json()
    assert body["code"] == ACCOUNT_SUSPENDED


@pytest.mark.asyncio
async def test_blocked_and_deleted_also_rejected(
    force_cloud_mode, clear_state_cache, wire_db
):
    client = TestClient(_build_app())
    for uid, status in (("carol_blocked", "blocked"), ("dave_deleted", "deleted")):
        await _seed(wire_db, uid, status=status)
        headers = {"Authorization": f"Bearer {create_token(uid, 'user')}"}
        r = client.get("/api/agents", headers=headers)
        assert r.status_code == 403, status
        assert r.json()["code"] == ACCOUNT_SUSPENDED


@pytest.mark.asyncio
async def test_ttl_cache_serves_stale_until_invalidated(
    force_cloud_mode, clear_state_cache, wire_db
):
    """A first request caches the state; a later DB flip is not seen until the
    cache entry is invalidated (or the TTL lapses)."""
    await _seed(wire_db, "erin", status="active")
    client = TestClient(_build_app())
    headers = {"Authorization": f"Bearer {create_token('erin', 'user')}"}

    # 1) primes the cache as active
    assert client.get("/api/agents", headers=headers).status_code == 200

    # 2) flip the DB to banned WITHOUT invalidating -> cache still says active
    await wire_db.update("users", {"user_id": "erin"}, {"status": "banned"})
    assert client.get("/api/agents", headers=headers).status_code == 200

    # 3) invalidate (what suspend/reinstate does) -> next read sees banned
    invalidate_account_state("erin")
    r = client.get("/api/agents", headers=headers)
    assert r.status_code == 403
    assert r.json()["code"] == ACCOUNT_SUSPENDED


@pytest.mark.asyncio
async def test_gate_fails_open_on_missing_user(
    force_cloud_mode, clear_state_cache, wire_db
):
    """A valid JWT whose user row doesn't exist (lazy-created accounts) must
    not be locked out — the gate only stops explicitly non-transacting states.
    """
    client = TestClient(_build_app())
    headers = {"Authorization": f"Bearer {create_token('nobody', 'user')}"}

    r = client.get("/api/agents", headers=headers)

    assert r.status_code == 200


# ----------------------------- login gate --------------------------------

def _make_login_app(db_client, monkeypatch, netmind_client, *, power_login=True):
    import backend.routes.auth as auth_route_mod
    from backend.auth_errors import install_auth_error_handler

    async def _async_return(value):
        return value

    monkeypatch.setattr(
        auth_route_mod, "get_db_client", lambda: _async_return(db_client)
    )
    monkeypatch.setattr(
        auth_route_mod, "is_power_login_enabled", lambda: power_login
    )
    monkeypatch.setattr(
        auth_route_mod, "_get_netmind_auth_client", lambda: netmind_client
    )

    app = FastAPI()
    install_auth_error_handler(app)
    app.include_router(auth_route_mod.router, prefix="/api/auth")
    return TestClient(app)


class _FakeNetmind:
    def __init__(self, user):
        self.user = user

    async def verify_token(self, token):
        return self.user


@pytest.mark.asyncio
async def test_login_refused_for_suspended_account(db_client, monkeypatch):
    from backend.integrations.netmind.netmind_auth_client import NetmindUser
    import backend.integrations.free_tier.provisioner as prov_mod

    code = "s" * 32
    await _seed(db_client, code, status="banned")

    seeded: list[str] = []

    async def _prov(user_id, **_kw):
        seeded.append(user_id)

    monkeypatch.setattr(prov_mod, "ensure_free_tier_provider", _prov)

    user = NetmindUser(user_system_code=code, email="x@y.z", nickname="X")
    client = _make_login_app(db_client, monkeypatch, _FakeNetmind(user))

    resp = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})

    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == ACCOUNT_SUSPENDED
    assert "token" not in body
    # The fire-and-forget provisioning must NOT run for a suspended account.
    assert seeded == []
    # Still suspended (login must not have flipped it).
    row = await db_client.get_one("users", {"user_id": code})
    assert row["status"] == "banned"


@pytest.mark.asyncio
async def test_login_succeeds_for_active_account(db_client, monkeypatch):
    from backend.integrations.netmind.netmind_auth_client import NetmindUser
    import backend.integrations.free_tier.provisioner as prov_mod
    import backend.integrations.netmind.netmind_provisioner as nm_prov_mod

    async def _prov(user_id, **_kw):
        pass

    # Patch BOTH background provisioners so the active-login path does not
    # leak a real network task (which would otherwise wedge process exit —
    # the known "pytest doesn't exit" pit).
    monkeypatch.setattr(prov_mod, "ensure_free_tier_provider", _prov)
    monkeypatch.setattr(nm_prov_mod, "ensure_netmind_provider", _prov)

    code = "a" * 32
    user = NetmindUser(user_system_code=code, email="a@b.c", nickname="A")
    client = _make_login_app(db_client, monkeypatch, _FakeNetmind(user))

    resp = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})

    assert resp.status_code == 200
    assert resp.json()["user_id"] == code
    assert resp.json()["token"]
