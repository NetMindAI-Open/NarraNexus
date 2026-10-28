"""
@file_name: test_auth_provider_binding.py
@author: Bin Liang
@date: 2026-09-04
@description: kernel.auth is a bound plugin (authProviders): the deployment mode picks the builtin when no distribution is set, a distribution's `auth` overrides it, an unbound id fails closed, and the middleware answers with whatever the bound provider says (identity, None, AuthError codes) in both the local and cloud branches.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend import auth as auth_mod
from backend import auth_provider as ap
from backend.auth import auth_middleware, create_token
from backend.auth_errors import AuthError
from narranexus.kernel.plugins.registry import Contribution

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    ap.reset_auth_provider()
    monkeypatch.setattr("backend.plugins_boot._DISTRIBUTION", {})
    monkeypatch.delenv("NARRANEXUS_DIST", raising=False)
    yield
    ap.reset_auth_provider()


def _app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(auth_middleware)

    @app.get("/api/whoami")
    async def whoami(request: Request):
        return {"user_id": getattr(request.state, "user_id", None), "role": getattr(request.state, "role", None)}

    return app


def test_mode_picks_the_builtin_and_the_distribution_overrides_it(monkeypatch):
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: False)
    assert ap.bound_provider_id() == ap.LOCAL_PROVIDER and ap.auth_provider().id == ap.LOCAL_PROVIDER
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)
    assert ap.bound_provider_id() == ap.CLOUD_PROVIDER and ap.auth_provider().id == ap.CLOUD_PROVIDER
    monkeypatch.setattr("backend.plugins_boot._DISTRIBUTION", {})
    monkeypatch.setenv("NARRANEXUS_DIST", str(REPO / "distributions" / "example-tob"))
    assert ap.bound_provider_id() == "acme.auth-sso"
    with pytest.raises(RuntimeError, match="acme.auth-sso.*no contribution"):
        ap.auth_provider()  # the bundled provider only exists in a booted distribution: fail closed


def test_local_branch_asks_the_bound_provider(monkeypatch):
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: False)
    client = TestClient(_app())
    assert client.get("/api/whoami").status_code == 401
    assert client.get("/api/whoami", headers={"X-User-Id": "u1"}).json()["user_id"] == "u1"


def test_cloud_branch_reports_the_provider_codes(monkeypatch):
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)
    client = TestClient(_app())
    assert client.get("/api/whoami").json()["code"] == "token_missing"
    r = client.get("/api/whoami", headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401 and r.json()["code"] == "token_invalid"
    monkeypatch.setattr(auth_mod, "_account_state", _active)
    r = client.get("/api/whoami", headers={"Authorization": f"Bearer {create_token('u9', 'admin')}"})
    assert r.status_code == 200 and r.json() == {"user_id": "u9", "role": "admin"}


async def _active(user_id: str) -> str:
    return "active"


class _Sso:
    id = "acme.auth-sso"
    scheme = "bearer"

    async def authenticate(self, request):
        token = (request.headers.get("Authorization") or "")[7:]
        if token == "boom":
            raise AuthError("sso_denied", "SSO said no", status_code=403)
        return {"user_id": f"sso:{token}", "role": "user"} if token else None


def test_a_distribution_provider_replaces_the_builtin(monkeypatch):
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)
    monkeypatch.setattr(auth_mod, "_account_state", _active)
    monkeypatch.setattr(ap, "bound_provider_id", lambda: "acme.auth-sso")
    registry = ap.KERNEL_REGISTRIES.registry_for(ap.AUTH_SLOT)
    monkeypatch.setattr(registry, "entries", lambda: [type("E", (), {"owner": "acme.auth-sso", "factory": staticmethod(lambda: _Sso())})()])
    client = TestClient(_app())
    assert client.get("/api/whoami", headers={"Authorization": "Bearer alice"}).json()["user_id"] == "sso:alice"
    r = client.get("/api/whoami", headers={"Authorization": "Bearer boom"})
    assert r.status_code == 403 and r.json()["code"] == "sso_denied"
    assert Contribution  # the real path registers a Contribution; the fake entry mirrors its owner/factory shape
