"""
@file_name: test_funnel_capture.py
@date: 2026-06-08
@description: Each funnel capture site fires the right event on success
and stays silent on the failure path. Uses FakeSink injected via
get_analytics monkeypatch.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xyz_agent_context.analytics import _hash_distinct_id
from xyz_agent_context.analytics._impl.fake_sink import FakeSink


async def _async_return(value):
    return value


@pytest.fixture
def fake_sink(monkeypatch):
    sink = FakeSink()
    import xyz_agent_context.analytics as analytics
    monkeypatch.setattr(analytics, "_get_sink_cached", lambda: sink)
    # Bypass opt-out DB lookup so capture sites are exercised directly.
    monkeypatch.setattr(analytics, "_opted_out", lambda user_id: _async_return(False))
    return sink


@pytest.fixture
def auth_client(db_client, monkeypatch):
    import backend.routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: False)
    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


def test_create_user_fires_signed_up(auth_client, fake_sink):
    r = auth_client.post("/api/auth/create-user", json={"user_id": "alice"})
    assert r.status_code == 200 and r.json()["success"] is True
    names = [e[1] for e in fake_sink.events]
    assert "signed_up" in names
    evt = next(e for e in fake_sink.events if e[1] == "signed_up")
    assert evt[0] == _hash_distinct_id("alice")
    assert evt[2].get("method") == "create_user"


def test_duplicate_user_does_not_fire(auth_client, fake_sink):
    auth_client.post("/api/auth/create-user", json={"user_id": "bob"})
    fake_sink.events.clear()
    auth_client.post("/api/auth/create-user", json={"user_id": "bob"})  # exists
    assert [e for e in fake_sink.events if e[1] == "signed_up"] == []


@pytest.fixture
def funnel_client(db_client, monkeypatch):
    """auth router mounted with a tiny middleware that populates
    request.state.user_id from X-User-Id (production: auth_middleware does
    this). The funnel endpoint reads identity from there, never the body."""
    import backend.routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))
    app = FastAPI()

    @app.middleware("http")
    async def _set_user(request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


def test_funnel_endpoint_fires_setup_events(funnel_client, fake_sink):
    for event in ("setup_entered", "setup_skipped", "setup_completed"):
        r = funnel_client.post("/api/auth/funnel", json={"event": event},
                               headers={"X-User-Id": "ivy"})
        assert r.status_code == 200
        evt = next(e for e in fake_sink.events if e[1] == event)
        assert evt[0] == _hash_distinct_id("ivy")


def test_funnel_endpoint_rejects_unknown_event(funnel_client, fake_sink):
    r = funnel_client.post("/api/auth/funnel", json={"event": "evil_event"},
                           headers={"X-User-Id": "ivy"})
    assert r.status_code == 400
    assert fake_sink.events == []


def test_funnel_endpoint_requires_auth(funnel_client, fake_sink):
    # No X-User-Id header -> request.state.user_id is None -> 401, nothing fired.
    r = funnel_client.post("/api/auth/funnel", json={"event": "setup_entered"})
    assert r.status_code == 401
    assert fake_sink.events == []


def test_funnel_endpoint_ignores_client_properties(funnel_client, fake_sink):
    # Client-supplied properties must never reach the sink — a client could
    # otherwise override the server-derived `surface` or inject junk.
    r = funnel_client.post(
        "/api/auth/funnel",
        json={"event": "setup_entered",
              "properties": {"surface": "cloud", "evil": "payload"}},
        headers={"X-User-Id": "ivy"},
    )
    assert r.status_code == 200
    evt = next(e for e in fake_sink.events if e[1] == "setup_entered")
    props = evt[2] or {}
    assert "evil" not in props
    assert props.get("surface") != "cloud"  # server-derived, not client-set
