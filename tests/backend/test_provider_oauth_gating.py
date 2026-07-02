"""
@file_name: test_provider_oauth_gating.py
@author: NarraNexus
@date: 2026-06-14
@description: PR #25 review §3 — credential-riding gate on provider writes.

In cloud (one shared HOME, single ``app`` user) the CLI credential files
``~/.codex/auth.json`` / ``~/.claude/.credentials.json`` are container-global,
staged from a single staff ``codex login`` / ``claude login``. A non-staff
cloud user must not be able to wire a card or framework that resolves to
them, or they ride staff's credentials.

Gate boundary under test:
  - ``POST /api/providers`` with an OAuth card_type (codex_oauth /
    claude_oauth) → staff-only in cloud.
  - ``POST /api/providers/agent-framework`` → staff-only in cloud.
  - API-key cards and local mode stay fully open (one-key onboarding,
    bring-your-own-key self-serve must keep working).

Each gate fires BEFORE any DB / service call, so these tests need no DB —
the service is stubbed to a sentinel ValueError to prove "passed the gate".
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.providers as providers_mod

USER = {"X-User-Id": "u1"}
STAFF = {"X-User-Id": "u1", "X-Role": "staff"}


@pytest.fixture
def make_client(monkeypatch):
    """Build a TestClient with a fake auth middleware and a cloud/local env.

    ``X-Role`` header → ``request.state.role`` (mirrors auth_middleware);
    cloud is signalled by a non-sqlite DATABASE_URL (what ``_is_cloud`` reads).
    """

    def _make(*, cloud: bool):
        monkeypatch.setenv(
            "DATABASE_URL",
            "mysql://u:p@h/db" if cloud else "sqlite:///local.db",
        )
        app = FastAPI()

        @app.middleware("http")
        async def fake_auth(request: Request, call_next):
            request.state.user_id = request.headers.get("X-User-Id") or None
            role = request.headers.get("X-Role")
            if role:
                request.state.role = role
            return await call_next(request)

        app.include_router(providers_mod.router, prefix="/api/providers")
        return TestClient(app, raise_server_exceptions=False)

    return _make


def _stub_service_raising(monkeypatch, method: str):
    """Replace ``_get_service`` so reaching it raises a recognizable marker.

    A request that gets PAST the gate hits the service and surfaces the
    marker (caught and re-raised as 400), distinguishing "gate passed" from
    the gate's own 403.
    """

    class _Stub:
        async def add_provider(self, **_kw):
            raise ValueError("STUB_REACHED")

        async def set_user_agent_framework(self, *_a, **_kw):
            raise ValueError("STUB_REACHED")

    async def _get_service():
        return _Stub()

    monkeypatch.setattr(providers_mod, "_get_service", _get_service)


# ───────────── add_provider — OAuth card gate ──────────────────────────────

@pytest.mark.parametrize("card", ["codex_oauth", "claude_oauth"])
def test_oauth_card_blocked_for_cloud_non_staff(make_client, card):
    client = make_client(cloud=True)
    resp = client.post("/api/providers", json={"card_type": card}, headers=USER)
    assert resp.status_code == 403
    assert "staff-only" in resp.json()["detail"]


@pytest.mark.parametrize("card", ["codex_oauth", "claude_oauth"])
def test_oauth_card_allowed_for_cloud_staff(make_client, monkeypatch, card):
    _stub_service_raising(monkeypatch, "add_provider")
    client = make_client(cloud=True)
    resp = client.post("/api/providers", json={"card_type": card}, headers=STAFF)
    # Past the gate → service reached → 400 marker (not the 403 gate).
    assert resp.status_code == 400
    assert "STUB_REACHED" in resp.json()["detail"]


def test_oauth_card_allowed_in_local_mode(make_client, monkeypatch):
    _stub_service_raising(monkeypatch, "add_provider")
    client = make_client(cloud=False)
    resp = client.post(
        "/api/providers", json={"card_type": "codex_oauth"}, headers=USER
    )
    assert resp.status_code == 400
    assert "STUB_REACHED" in resp.json()["detail"]


def test_api_key_card_not_gated_in_cloud(make_client, monkeypatch):
    """A normal API-key card (bring-your-own-key) must stay open in cloud."""
    _stub_service_raising(monkeypatch, "add_provider")
    client = make_client(cloud=True)
    resp = client.post(
        "/api/providers", json={"card_type": "anthropic"}, headers=USER
    )
    assert resp.status_code == 400
    assert "STUB_REACHED" in resp.json()["detail"]


# ───────────── set_agent_framework gate ────────────────────────────────────

def test_set_framework_blocked_for_cloud_non_staff(make_client):
    client = make_client(cloud=True)
    resp = client.post(
        "/api/providers/agent-framework",
        json={"framework": "codex_cli"},
        headers=USER,
    )
    assert resp.status_code == 403
    assert "staff-only" in resp.json()["detail"]


def test_set_framework_allowed_for_cloud_staff(make_client):
    # Staff passes the gate; an unknown framework then 400s downstream —
    # proving the gate did not fire for staff (no DB needed).
    client = make_client(cloud=True)
    resp = client.post(
        "/api/providers/agent-framework",
        json={"framework": "bogus_framework"},
        headers=STAFF,
    )
    assert resp.status_code == 400
    assert "Unknown framework" in resp.json()["detail"]


def test_set_framework_not_gated_in_local(make_client):
    client = make_client(cloud=False)
    resp = client.post(
        "/api/providers/agent-framework",
        json={"framework": "bogus_framework"},
        headers=USER,
    )
    assert resp.status_code == 400
    assert "Unknown framework" in resp.json()["detail"]
