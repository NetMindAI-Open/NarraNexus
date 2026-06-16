"""
@file_name: test_legacy_auth_removed.py
@author: NarraNexus
@date: 2026-06-11
@description: Guards for the Phase 1 teardown of the self-built cloud auth.

Cloud login is NetMind-only now (POST /api/auth/netmind-login). These tests
pin the removal so the legacy surface can't quietly come back:

- POST /api/auth/login in cloud mode -> 404 (use netmind-login)
- POST /api/auth/login in local mode -> unchanged (user_id only, no password)
- POST /api/auth/register -> route gone entirely
- POST /api/auth/create-user in cloud -> 404 (was an unauthenticated open
  account-creation endpoint — a known hole); local mode keeps it
- invite-code route modules deleted; exempt list no longer carries them
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


async def _async_return(value):
    return value


def _make_app(db_client, monkeypatch, *, cloud: bool):
    import backend.routes.auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: cloud)

    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


def test_cloud_password_login_is_gone(db_client, monkeypatch):
    client = _make_app(db_client, monkeypatch, cloud=True)

    resp = client.post(
        "/api/auth/login", json={"user_id": "whoever", "password": "secret"}
    )

    assert resp.status_code == 404


def test_local_login_still_works(db_client, monkeypatch):
    import asyncio

    from xyz_agent_context.repository.user_repository import UserRepository

    asyncio.get_event_loop().run_until_complete(
        UserRepository(db_client).add_user(user_id="lily", user_type="local")
    )
    client = _make_app(db_client, monkeypatch, cloud=False)

    resp = client.post("/api/auth/login", json={"user_id": "lily"})

    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_register_route_is_gone(db_client, monkeypatch):
    client = _make_app(db_client, monkeypatch, cloud=True)

    resp = client.post(
        "/api/auth/register",
        json={"user_id": "u", "password": "p" * 8, "invite_code": "c"},
    )

    assert resp.status_code == 404


def test_create_user_is_local_only(db_client, monkeypatch):
    cloud = _make_app(db_client, monkeypatch, cloud=True)
    resp = cloud.post(
        "/api/auth/create-user", json={"user_id": "eve", "user_type": "individual"}
    )
    assert resp.status_code == 404

    local = _make_app(db_client, monkeypatch, cloud=False)
    resp = local.post(
        "/api/auth/create-user", json={"user_id": "eve", "user_type": "local"}
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_invite_route_modules_are_deleted():
    with pytest.raises(ModuleNotFoundError):
        import backend.routes.invite  # noqa: F401
    with pytest.raises(ModuleNotFoundError):
        import backend.routes.admin_invite  # noqa: F401


def test_exempt_list_has_no_legacy_entries():
    from backend.auth import AUTH_EXEMPT_PATHS

    assert "/api/auth/register" not in AUTH_EXEMPT_PATHS
    assert "/api/invite/internal/issue" not in AUTH_EXEMPT_PATHS
    # The two survivors that legitimately carry their own credentials:
    assert "/api/auth/login" in AUTH_EXEMPT_PATHS
    assert "/api/auth/netmind-login" in AUTH_EXEMPT_PATHS
