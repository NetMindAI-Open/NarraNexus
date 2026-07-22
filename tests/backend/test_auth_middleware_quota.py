"""
@file_name: test_auth_middleware_quota.py
@author: Bin Liang
@date: 2026-04-23
@description: auth_middleware's quota gate must (a) leave config-class
paths reachable so a quota-exhausted user can still add a provider or
flip the Settings toggle, (b) map the three ProviderResolver errors to
distinct 402 error_codes the frontend can switch on, and (c) never let
the quota gate block safe/read-only HTTP methods (GET/HEAD) on ANY
path -- an exhausted account must still be able to read its own data
(GH #61: exhausted quota locked the whole dashboard, not just LLM
calls).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import auth as auth_mod
from backend.auth import auth_middleware, create_token
from xyz_agent_context.agent_framework.provider_resolver import (
    NoProviderConfiguredError,
    QuotaExceededError,
)


def _build_app(resolver) -> FastAPI:
    """Minimal app with auth_middleware + a handful of stub routes covering
    both bypass and non-bypass paths."""
    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.state.provider_resolver = resolver

    @app.post("/api/providers")
    async def add_provider():
        return {"ok": True, "route": "add_provider"}

    @app.get("/api/quota/me")
    async def get_quota():
        return {"ok": True, "route": "get_quota"}

    @app.post("/api/chat")
    async def chat():
        return {"ok": True, "route": "chat"}

    @app.get("/api/providers/slots/validate")
    async def validate_slots():
        return {"ok": True, "route": "validate_slots"}

    # Plain read-only endpoint, NOT in QUOTA_BYPASS_PREFIXES -- represents
    # GET /api/agents, GET /api/dashboard, etc. from the real router.
    @app.get("/api/agents")
    async def list_agents():
        return {"ok": True, "route": "list_agents"}

    @app.head("/api/agents")
    async def head_agents():
        return {"ok": True, "route": "head_agents"}

    # Same path, but a write method -- must still be quota-gated.
    @app.post("/api/agents")
    async def create_agent():
        return {"ok": True, "route": "create_agent"}

    return app


@pytest.fixture
def force_cloud_mode(monkeypatch):
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)


@pytest.fixture
def jwt_headers():
    token = create_token(user_id="alice", role="user")
    return {"Authorization": f"Bearer {token}"}


# --------- Bypass: config-class paths reachable despite quota state ------

def test_add_provider_reachable_when_resolver_would_raise_quota_exceeded(
    force_cloud_mode, jwt_headers,
):
    """Core regression: a user with quota=0 and no own provider must still
    be able to POST /api/providers — otherwise they're permanently locked
    out."""
    resolver = MagicMock()
    resolver.resolve_and_set = AsyncMock(side_effect=QuotaExceededError("alice"))

    client = TestClient(_build_app(resolver))
    r = client.post("/api/providers", json={}, headers=jwt_headers)

    assert r.status_code == 200
    assert r.json()["route"] == "add_provider"
    # Resolver must NOT have been invoked for the bypassed path.
    resolver.resolve_and_set.assert_not_called()


def test_quota_me_reachable_when_resolver_would_raise(force_cloud_mode, jwt_headers):
    resolver = MagicMock()
    resolver.resolve_and_set = AsyncMock(side_effect=QuotaExceededError("alice"))

    client = TestClient(_build_app(resolver))
    r = client.get("/api/quota/me", headers=jwt_headers)

    assert r.status_code == 200
    resolver.resolve_and_set.assert_not_called()


def test_provider_sub_path_also_bypassed(force_cloud_mode, jwt_headers):
    """/api/providers/slots/validate is config-related — bypass still applies."""
    resolver = MagicMock()
    resolver.resolve_and_set = AsyncMock(side_effect=QuotaExceededError("alice"))

    client = TestClient(_build_app(resolver))
    r = client.get("/api/providers/slots/validate", headers=jwt_headers)

    assert r.status_code == 200
    resolver.resolve_and_set.assert_not_called()


# --------- Non-bypass: LLM-calling paths still go through resolver -------

def test_chat_route_runs_resolver(force_cloud_mode, jwt_headers):
    resolver = MagicMock()
    resolver.resolve_and_set = AsyncMock()  # resolves cleanly

    client = TestClient(_build_app(resolver))
    r = client.post("/api/chat", json={}, headers=jwt_headers)

    assert r.status_code == 200
    resolver.resolve_and_set.assert_awaited_once_with("alice")


# --------- Error-code mapping on non-bypassed paths ----------------------

def test_chat_quota_exceeded_returns_402_with_quota_exceeded_code(
    force_cloud_mode, jwt_headers,
):
    resolver = MagicMock()
    resolver.resolve_and_set = AsyncMock(side_effect=QuotaExceededError("alice"))

    client = TestClient(_build_app(resolver))
    r = client.post("/api/chat", json={}, headers=jwt_headers)

    assert r.status_code == 402
    body = r.json()
    assert body["error_code"] == "QUOTA_EXCEEDED_NO_USER_PROVIDER"
    assert body["success"] is False


def test_chat_no_provider_configured_returns_402_with_no_provider_code(
    force_cloud_mode, jwt_headers,
):
    resolver = MagicMock()
    resolver.resolve_and_set = AsyncMock(side_effect=NoProviderConfiguredError("alice"))

    client = TestClient(_build_app(resolver))
    r = client.post("/api/chat", json={}, headers=jwt_headers)

    assert r.status_code == 402
    body = r.json()
    assert body["error_code"] == "NO_PROVIDER_CONFIGURED"


# --------- JWT still enforced on bypassed paths --------------------------

def test_bypassed_path_still_requires_jwt(force_cloud_mode):
    """Bypass skips provider_resolver, NOT JWT. Unauthenticated requests to
    /api/providers must still 401."""
    resolver = MagicMock()
    resolver.resolve_and_set = AsyncMock()

    client = TestClient(_build_app(resolver))
    r = client.post("/api/providers", json={})  # no Authorization header

    assert r.status_code == 401


# --------- Safe/read-only methods bypass the quota gate on ANY path ------
# GH #61: an exhausted free tier 402'd every /api/ endpoint, including
# plain reads (GET /api/agents, GET /api/dashboard) that never touch an
# LLM. The gate must only stand between exhausted accounts and requests
# that actually spend quota -- and every quota-spending endpoint in this
# codebase is a mutating verb (POST), never GET/HEAD.

def test_get_on_non_bypass_path_reachable_when_resolver_would_raise(
    force_cloud_mode, jwt_headers,
):
    """GET /api/agents is not in QUOTA_BYPASS_PREFIXES, but it's a pure read
    -- an exhausted account must still be able to see its own agent list."""
    resolver = MagicMock()
    resolver.resolve_and_set = AsyncMock(side_effect=QuotaExceededError("alice"))

    client = TestClient(_build_app(resolver))
    r = client.get("/api/agents", headers=jwt_headers)

    assert r.status_code == 200
    assert r.json()["route"] == "list_agents"
    resolver.resolve_and_set.assert_not_called()


def test_head_on_non_bypass_path_reachable_when_resolver_would_raise(
    force_cloud_mode, jwt_headers,
):
    resolver = MagicMock()
    resolver.resolve_and_set = AsyncMock(side_effect=QuotaExceededError("alice"))

    client = TestClient(_build_app(resolver))
    r = client.head("/api/agents", headers=jwt_headers)

    assert r.status_code == 200
    resolver.resolve_and_set.assert_not_called()


def test_post_on_same_path_still_gated(force_cloud_mode, jwt_headers):
    """Same URL, but POST (a write) -- the quota gate must still apply.
    Proves the safe-method exemption is method-scoped, not path-scoped."""
    resolver = MagicMock()
    resolver.resolve_and_set = AsyncMock(side_effect=QuotaExceededError("alice"))

    client = TestClient(_build_app(resolver))
    r = client.post("/api/agents", json={}, headers=jwt_headers)

    assert r.status_code == 402
    resolver.resolve_and_set.assert_awaited_once_with("alice")


def test_get_on_non_bypass_path_still_requires_jwt(force_cloud_mode):
    """Safe-method exemption skips the quota gate, NOT authentication."""
    resolver = MagicMock()
    resolver.resolve_and_set = AsyncMock()

    client = TestClient(_build_app(resolver))
    r = client.get("/api/agents")  # no Authorization header

    assert r.status_code == 401
