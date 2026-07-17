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
import xyz_agent_context.services.netmind_provisioner as prov_mod
import xyz_agent_context.module.job_module.job_recovery as jr_mod
from xyz_agent_context.services.netmind_key_client import KeyAuthError, KeyUpstreamError
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


def test_404_when_power_login_disabled(make_client):
    # Local install with no NARRANEXUS_ENABLE_POWER_LOGIN opt-in.
    client = make_client(cloud=False, enabled=True)
    r = client.post("/api/providers/use-subscription", headers={**USER, **TOK})
    assert r.status_code == 404


def test_reachable_in_local_when_power_login_enabled(make_client, monkeypatch):
    # Dual-mode: a local deployment that opted into Power login passes the 404
    # gate. No X-Netmind-Token here -> 401 proves we got past the 404 + feature
    # gates (i.e. the route is reachable in local mode).
    monkeypatch.setenv("NARRANEXUS_ENABLE_POWER_LOGIN", "true")
    client = make_client(cloud=False, enabled=True)
    r = client.post("/api/providers/use-subscription", headers=USER)
    assert r.status_code == 401


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


# --- Delegation to the provisioner --------------------------------------------
# The mint + onboard logic now lives in netmind_provisioner (unit-tested in
# test_netmind_provisioner.py). These verify the route only gates and maps the
# provisioner's outcome to HTTP.


def _patch_ensure(monkeypatch, *, returns=None, raises=None):
    async def _ensure(uid, token, *, activate_if_fresh=True):
        if raises is not None:
            raise raises
        return returns
    monkeypatch.setattr(prov_mod, "ensure_netmind_provider", _ensure)


def test_success_returns_200(make_client, monkeypatch):
    _patch_ensure(monkeypatch, returns=True)

    class _Svc:
        async def get_user_config(self, uid):
            return object()

    async def _get_service():
        return _Svc()

    monkeypatch.setattr(providers_mod, "_get_service", _get_service)
    monkeypatch.setattr(providers_mod, "_config_to_response", lambda c: {"ok": True})
    monkeypatch.setattr(jr_mod, "schedule_user_no_quota_rearm", lambda uid: None)

    client = make_client(cloud=True, enabled=True)
    r = client.post("/api/providers/use-subscription", headers={**USER, **TOK})
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_noop_returns_409(make_client, monkeypatch):
    _patch_ensure(monkeypatch, returns=False)  # already connected
    client = make_client(cloud=True, enabled=True)
    r = client.post("/api/providers/use-subscription", headers={**USER, **TOK})
    assert r.status_code == 409


@pytest.mark.parametrize(
    "exc,code",
    [
        (KeyAuthError("bad token"), 401),
        (KeyUpstreamError("upstream 500"), 502),
        (ValueError("already exists"), 409),
        (ValueError("key rejected by netmind"), 502),
        (ValueError("some other problem"), 400),
    ],
)
def test_provisioner_errors_mapped_to_http(make_client, monkeypatch, exc, code):
    _patch_ensure(monkeypatch, raises=exc)
    client = make_client(cloud=True, enabled=True)
    r = client.post("/api/providers/use-subscription", headers={**USER, **TOK})
    assert r.status_code == code
