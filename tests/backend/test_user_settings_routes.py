"""
@file_name: test_user_settings_routes.py
@date: 2026-06-08
@description: GET/PUT analytics opt-out round-trips via the API.

Identity comes from request.state.user_id (populated by auth_middleware in
production; a tiny test middleware here) — never from the query string or
body — so one user cannot read or flip another user's privacy preference.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


async def _async_return(v):
    return v


@pytest.fixture
def client(db_client, monkeypatch):
    import backend.routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: False)
    app = FastAPI()

    @app.middleware("http")
    async def _set_user(request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


def test_opt_out_defaults_false_then_toggles(client):
    h = {"X-User-Id": "u1"}
    g = client.get("/api/auth/settings/analytics", headers=h)
    assert g.status_code == 200 and g.json()["opted_out"] is False
    p = client.put("/api/auth/settings/analytics",
                   json={"opted_out": True}, headers=h)
    assert p.status_code == 200
    g2 = client.get("/api/auth/settings/analytics", headers=h)
    assert g2.json()["opted_out"] is True


def test_opt_out_is_per_user(client):
    client.put("/api/auth/settings/analytics",
               json={"opted_out": True}, headers={"X-User-Id": "u1"})
    g = client.get("/api/auth/settings/analytics", headers={"X-User-Id": "u2"})
    assert g.json()["opted_out"] is False


def test_opt_out_requires_identity(client):
    # No X-User-Id -> request.state.user_id is None -> 401. The body cannot
    # name a target user, so cross-user writes are impossible by shape.
    g = client.get("/api/auth/settings/analytics")
    assert g.status_code == 401
    p = client.put("/api/auth/settings/analytics", json={"opted_out": True})
    assert p.status_code == 401
