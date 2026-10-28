"""
@file_name: test_funnel_capture.py
@date: 2026-06-08
@description: Each funnel capture site persists the right first-party event on
success and stays silent on the failure path.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


async def _async_return(value):
    return value


@pytest.fixture
def captured_events(monkeypatch):
    events = []
    import narranexus.platform.analytics as analytics

    async def _capture(**event):
        events.append(event)

    monkeypatch.setattr(analytics, "_persist_product_event", _capture)
    monkeypatch.setattr(analytics, "_opted_out", lambda user_id: _async_return(False))
    return events


@pytest.fixture
def auth_client(db_client, monkeypatch):
    import backend.routes.auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: False)
    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


def test_create_user_fires_signed_up(auth_client, captured_events):
    r = auth_client.post("/api/auth/create-user", json={"user_id": "alice"})
    assert r.status_code == 200 and r.json()["success"] is True
    names = [e["event"] for e in captured_events]
    assert "signed_up" in names
    evt = next(e for e in captured_events if e["event"] == "signed_up")
    assert evt["user_id"] == "alice"
    assert evt["properties"].get("method") == "create_user"


def test_duplicate_user_does_not_fire(auth_client, captured_events):
    auth_client.post("/api/auth/create-user", json={"user_id": "bob"})
    captured_events.clear()
    auth_client.post("/api/auth/create-user", json={"user_id": "bob"})  # exists
    assert [e for e in captured_events if e["event"] == "signed_up"] == []
