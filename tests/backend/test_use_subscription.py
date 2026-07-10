"""
@file_name: test_use_subscription.py
@author: NarraNexus
@date: 2026-07-02
@description: Route tests for POST /api/providers/use-subscription (module F).

Focus on the gating / short-circuit paths (cloud gate, feature flag, token
requirement, dedup) which don't need the full key-gen + onboard chain. The
key client is unit-tested separately; onboard_one_key is tested in
test_one_key_onboarding.py.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.providers as providers_mod
import xyz_agent_context.utils.db_factory as db_factory
from xyz_agent_context.settings import settings

USER = {"X-User-Id": "user_test"}
TOK = {"X-Netmind-Token": "jwt"}


@pytest.fixture
def make_client(monkeypatch):
    def _make(*, cloud: bool, enabled: bool):
        monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud" if cloud else "local")
        monkeypatch.setattr(settings, "netmind_use_subscription_enabled", enabled, raising=False)
        app = FastAPI()

        @app.middleware("http")
        async def fake_auth(request: Request, call_next):
            request.state.user_id = request.headers.get("X-User-Id") or None
            return await call_next(request)

        app.include_router(providers_mod.router, prefix="/api/providers")
        return TestClient(app, raise_server_exceptions=False)

    return _make


def _stub_db(monkeypatch, *, existing):
    class _DB:
        async def get_one(self, table, filters):
            return {"provider_id": "p1"} if existing else None

    async def _get_db_client():
        return _DB()

    monkeypatch.setattr(db_factory, "get_db_client", _get_db_client)


def test_404_in_local_mode(make_client):
    client = make_client(cloud=False, enabled=True)
    r = client.post("/api/providers/use-subscription", headers={**USER, **TOK})
    assert r.status_code == 404


def test_403_when_feature_disabled(make_client):
    client = make_client(cloud=True, enabled=False)
    r = client.post("/api/providers/use-subscription", headers={**USER, **TOK})
    assert r.status_code == 403


def test_401_missing_netmind_token(make_client):
    client = make_client(cloud=True, enabled=True)
    r = client.post("/api/providers/use-subscription", headers=USER)
    assert r.status_code == 401


def test_409_when_already_connected(make_client, monkeypatch):
    _stub_db(monkeypatch, existing=True)
    client = make_client(cloud=True, enabled=True)
    r = client.post("/api/providers/use-subscription", headers={**USER, **TOK})
    assert r.status_code == 409


def test_unauthenticated_local_identity_401(make_client):
    client = make_client(cloud=True, enabled=True)
    # no X-User-Id -> _get_user_id raises 401 before anything else
    r = client.post("/api/providers/use-subscription", headers=TOK)
    assert r.status_code == 401


def test_minted_key_onboarded_with_configured_inference_base(make_client, monkeypatch):
    """The minted-key path must forward settings.netmind_inference_base into
    onboard_one_key (so a dev key is wired to dev inference). We capture the
    kwarg on the error path (fake raises 'rejected' -> 502) to avoid mocking the
    full happy-path config/hot-reload/job-recovery tail."""
    import xyz_agent_context.services.netmind_key_client as key_mod
    from xyz_agent_context.services.netmind_key_client import MintedKey

    _stub_db(monkeypatch, existing=False)
    monkeypatch.setattr(settings, "netmind_inference_base", "https://test.api.netmind.ai/inference-api", raising=False)

    class _FakeKeyClient:
        def __init__(self, *a, **k): pass
        async def create_key(self, token): return MintedKey(apitoken="mint-x", token_id=7)
        async def delete_key(self, token, tid): return None
    monkeypatch.setattr(key_mod, "NetmindKeyClient", _FakeKeyClient)

    captured = {}

    class _FakeService:
        async def onboard_one_key(self, uid, key, provider_type=None, inference_base=None):
            captured["inference_base"] = inference_base
            captured["provider_type"] = provider_type
            raise ValueError("key rejected by netmind")  # -> route maps to 502

    async def _fake_get_service():
        return _FakeService()
    monkeypatch.setattr(providers_mod, "_get_service", _fake_get_service)

    client = make_client(cloud=True, enabled=True)
    r = client.post("/api/providers/use-subscription", headers={**USER, **TOK})
    assert r.status_code == 502  # "rejected" path
    assert captured["provider_type"] == "netmind"
    assert captured["inference_base"] == "https://test.api.netmind.ai/inference-api"
