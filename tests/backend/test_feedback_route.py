"""
@file_name: test_feedback_route.py
@author: Bin Liang
@date: 2026-07-10
@description: Route tests for POST /api/feedback (web_ui relay to feedback intake).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.feedback as feedback_mod


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    calls = []

    async def fake_send(**kw):
        calls.append(kw)
        return True

    monkeypatch.setattr(feedback_mod, "send_feedback", fake_send)
    app = FastAPI()
    app.include_router(feedback_mod.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False), calls


def test_forwards_to_client(client):
    c, calls = client
    r = c.post("/api/feedback?user_id=alice",
               json={"category": "feature_gap", "text": "want dark mode"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert len(calls) == 1
    assert calls[0]["category"] == "feature_gap"
    assert calls[0]["summary"] == "want dark mode"
    assert calls[0]["source"] == "web_ui"
    assert calls[0]["user_id"] == "alice"


def test_unknown_category_coerced_to_other(client):
    c, calls = client
    c.post("/api/feedback", json={"category": "banana", "text": "hi there"})
    assert calls[0]["category"] == "other"


def test_empty_text_rejected(client):
    c, calls = client
    r = c.post("/api/feedback", json={"category": "other", "text": ""})
    assert r.status_code == 422
    assert calls == []


def test_overlong_text_rejected(client):
    c, calls = client
    r = c.post("/api/feedback", json={"category": "other", "text": "x" * 501})
    assert r.status_code == 422
    assert calls == []


def test_ok_even_if_intake_down(client, monkeypatch):
    c, calls = client

    async def fake_send_down(**kw):
        return False

    monkeypatch.setattr(feedback_mod, "send_feedback", fake_send_down)
    r = c.post("/api/feedback", json={"category": "error", "text": "still fine"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "delivered": False}
