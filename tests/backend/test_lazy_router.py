"""
@file_name: test_lazy_router.py
@author: Bin Liang
@date: 2026-09-03
@description: A lazy plugin router activates on the first request, stays auth fail-closed, and answers 503 (not a crash) when activation fails.
"""
from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from narranexus.contracts.route import RouterSpec
from backend.plugins_host import mount_lazy_router


def _app():
    from backend.auth import auth_middleware

    app = FastAPI()
    app.middleware("http")(auth_middleware)
    return app


def test_first_request_activates_then_reuses():
    app = _app()
    calls = []

    async def activate():
        calls.append(1)
        r = APIRouter()

        @r.get("/ping")
        async def ping():
            return {"ok": True}

        return RouterSpec(r, "/api/x/acme.w")

    lazy = mount_lazy_router(app, "acme.w", "/api/x/acme.w", activate)
    client = TestClient(app)
    assert not lazy.activated
    assert client.get("/api/x/acme.w/ping").status_code == 401  # auth runs before activation
    assert calls == [] and not lazy.activated
    assert client.get("/api/x/acme.w/ping", headers={"X-User-Id": "u1"}).json() == {"ok": True}
    assert client.get("/api/x/acme.w/ping", headers={"X-User-Id": "u1"}).status_code == 200
    assert calls == [1] and lazy.activated


def test_failed_activation_answers_503_and_is_not_retried_in_a_loop():
    app = _app()
    calls = []

    async def activate():
        calls.append(1)
        raise RuntimeError("import failed")

    lazy = mount_lazy_router(app, "acme.w", "/api/x/acme.w", activate)
    client = TestClient(app)
    r = client.get("/api/x/acme.w/ping", headers={"X-User-Id": "u1"})
    assert r.status_code == 503 and "acme.w" in r.json()["detail"]
    client.get("/api/x/acme.w/ping", headers={"X-User-Id": "u1"})
    assert calls == [1] and lazy.error == "RuntimeError: import failed"


def test_prefix_outside_the_plugin_namespace_is_refused():
    import pytest

    async def activate():  # pragma: no cover
        raise AssertionError

    with pytest.raises(ValueError, match="outside"):
        mount_lazy_router(_app(), "acme.w", "/api/agents", activate)
