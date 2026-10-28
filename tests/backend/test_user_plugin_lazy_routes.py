"""
@file_name: test_user_plugin_lazy_routes.py
@author: Bin Liang
@date: 2026-09-04
@description: Found running the stack: a linked user plugin's routes never mounted (the SPA fallback swallowed /api/x/<id>/...). Now every registry.json plugin declaring backend.routes gets a LazyRouterApp at /api/x/<id> at import; the first request builds the router from the plugin's registered contributions (all of them, prefix-checked), a plugin without a loaded contribution answers 503, and the mount is skipped on cloud.
"""
from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from backend.plugins_host import mount_user_plugin_routes
from narranexus.contracts.route import RouterSpec
from narranexus.kernel.plugins.manifest import parse_manifest
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.kernel.plugins.builtins import load_builtins


def _manifest(pid: str, provides: dict):
    from narranexus.kernel.plugins.builtins import slot_tree_with_builtins

    data = {"id": pid, "version": "0.1.0", "displayName": pid, "hosts": ["backend"], "backend": {"activate": True},
            "api": {"route": 0}, "provides": provides}
    return parse_manifest(data, tree=slot_tree_with_builtins())


def test_lazy_router_builds_from_the_plugins_contributions_on_first_request():
    regs = Registries()
    load_builtins(regs, "backend")
    r1, r2 = APIRouter(), APIRouter()

    @r1.get("/hello")
    async def hello():
        return {"hi": "acme"}

    @r2.get("/more")
    async def more():
        return {"more": True}

    routes = regs.registry_for("backend.routes")
    routes.register_contribution(Contribution("main", lambda: RouterSpec(r1, "/api/x/acme.x")), owner="acme.x")
    routes.register_contribution(Contribution("extra", lambda: RouterSpec(r2, "/api/x/acme.x/v2")), owner="acme.x")
    app = FastAPI()
    mounted = mount_user_plugin_routes(app, regs, manifests=[_manifest("acme.x", {"backend.routes": ["nxplugins.acme_x:ROUTES"]})])
    assert set(mounted) == {"acme.x"} and not mounted["acme.x"].activated

    @app.get("/{path:path}")
    async def spa(path: str):
        return {"spa": path}

    client = TestClient(app)
    assert client.get("/api/x/acme.x/hello").json() == {"hi": "acme"}
    assert client.get("/api/x/acme.x/v2/more").json() == {"more": True}
    assert mounted["acme.x"].activated
    assert client.get("/somewhere").json() == {"spa": "somewhere"}


def test_plugin_without_loaded_routes_answers_503_and_builtins_are_ignored():
    regs = Registries()
    load_builtins(regs, "backend")
    app = FastAPI()
    mounted = mount_user_plugin_routes(app, regs, manifests=[
        _manifest("acme.ghost", {"backend.routes": ["nxplugins.acme_ghost:ROUTES"]}),
        _manifest("acme.noroutes", {}),
    ])
    assert set(mounted) == {"acme.ghost"}
    r = TestClient(app).get("/api/x/acme.ghost/anything")
    assert r.status_code == 503 and "acme.ghost" in r.json()["detail"] and mounted["acme.ghost"].error


def test_cloud_mounts_nothing(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    assert mount_user_plugin_routes(FastAPI(), Registries()) == {}


def test_lazy_router_is_wired_behind_the_real_auth_middleware():
    """The two other tests in this file build a bare ``FastAPI()`` with no
    auth middleware at all, so they cannot tell "wired correctly behind the
    real middleware stack" from "wired outside it entirely, unauthenticated
    end to end" — both look identical with no middleware installed. Mount
    with `backend.auth.auth_middleware` for real (as `test_lazy_router.py`
    does for the lower-level `mount_lazy_router`) and prove the unauthenticated
    request 401s before the plugin's own router ever runs.
    """
    from backend.auth import auth_middleware

    regs = Registries()
    load_builtins(regs, "backend")
    r1 = APIRouter()

    @r1.get("/hello")
    async def hello():
        return {"hi": "acme"}

    regs.registry_for("backend.routes").register_contribution(
        Contribution("main", lambda: RouterSpec(r1, "/api/x/acme.auth")), owner="acme.auth"
    )
    app = FastAPI()
    app.middleware("http")(auth_middleware)
    mount_user_plugin_routes(app, regs, manifests=[_manifest("acme.auth", {"backend.routes": ["nxplugins.acme_auth:ROUTES"]})])
    client = TestClient(app)

    r = client.get("/api/x/acme.auth/hello")
    assert r.status_code == 401

    r = client.get("/api/x/acme.auth/hello", headers={"X-User-Id": "u1"})
    assert r.status_code == 200 and r.json() == {"hi": "acme"}
