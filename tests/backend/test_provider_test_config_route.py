"""
@file_name: test_provider_test_config_route.py
@date: 2026-07-23
@description: Route test for POST /api/providers/test-config — the
stateless "verify before save" probe used by the add-provider form.

Boundaries under test:
  - The route forwards every form field to
    ``UserProviderService.test_provider_config`` and passes its
    (success, message) straight back as JSON.
  - It requires an authenticated user (X-User-Id), like every other
    provider route.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.providers as providers_mod


def _make_client(monkeypatch):
    captured: dict = {}

    class _Stub:
        async def test_provider_config(self, **kw):
            captured.update(kw)
            return False, "Authentication failed (invalid API key)"

    async def _get_service():
        return _Stub()

    monkeypatch.setattr(providers_mod, "_get_service", _get_service)

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(providers_mod.router, prefix="/api/providers")
    return TestClient(app, raise_server_exceptions=False), captured


def test_test_config_forwards_form_fields_and_result(monkeypatch):
    client, captured = _make_client(monkeypatch)
    resp = client.post(
        "/api/providers/test-config",
        json={
            "card_type": "openai",
            "api_key": "sk-bad",
            "base_url": "https://proxy.example/v1",
            "auth_type": "api_key",
            "models": ["gpt-x"],
        },
        headers={"X-User-Id": "u1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "success": False,
        "message": "Authentication failed (invalid API key)",
    }
    # Every form field reached the service verbatim.
    assert captured == {
        "card_type": "openai",
        "api_key": "sk-bad",
        "base_url": "https://proxy.example/v1",
        "auth_type": "api_key",
        "models": ["gpt-x"],
    }


def test_test_config_requires_auth(monkeypatch):
    """No X-User-Id → 401 before any service work (auth is mandatory)."""
    client, captured = _make_client(monkeypatch)
    resp = client.post(
        "/api/providers/test-config",
        json={"card_type": "openai", "api_key": "sk-bad"},
    )
    assert resp.status_code == 401
    assert captured == {}  # never reached the service


def test_test_config_rejects_oauth_auth_type(monkeypatch):
    """auth_type outside the Literal → 422 at the API boundary, not 500."""
    client, captured = _make_client(monkeypatch)
    resp = client.post(
        "/api/providers/test-config",
        json={"card_type": "anthropic", "api_key": "", "auth_type": "oauth"},
        headers={"X-User-Id": "u1"},
    )
    assert resp.status_code == 422
    assert captured == {}  # rejected before the service
