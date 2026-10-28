"""
@file_name: test_plugin_routes_mount.py
@author: Bin Liang
@date: 2026-09-03
@description: backend.routes contributions mount under /api/x/<id>, are auth fail-closed, and explicit auth="none" is honoured.
"""
from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from narranexus.contracts.route import RouterSpec
from narranexus.kernel.plugins.builtins import slot_tree_with_builtins
from narranexus.kernel.plugins.manifest import parse_manifest
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution


def _manifest(pid: str, *, public=()):
    """A real parsed manifest: the mount path now asks it whether a public
    prefix was declared, so a bare RouterSpec with no manifest is (correctly)
    refused. See tests/backend/test_plugin_route_policy.py for that contract."""
    return parse_manifest(
        {"id": pid, "version": "0.1.0", "displayName": pid, "hosts": ["backend"],
         "backend": {"activate": True, "publicPrefixes": list(public)},
         "api": {"route": 0}, "provides": {}},
        tree=slot_tree_with_builtins(),
    )


def _router(text: str) -> APIRouter:
    r = APIRouter()

    @r.get("/ping")
    async def ping():
        return {"ok": text}

    return r


@pytest.fixture
def host_app(monkeypatch):
    from backend.auth import PLUGIN_EXEMPT_PREFIXES, auth_middleware

    assert isinstance(PLUGIN_EXEMPT_PREFIXES, set)
    monkeypatch.setattr("backend.auth.PLUGIN_EXEMPT_PREFIXES", set())
    # Empty ON ENTRY, not merely a set: a leak from another test would
    # otherwise hand these 401 assertions a free pass.
    from backend import auth as _a
    assert _a.PLUGIN_EXEMPT_PREFIXES == set()
    app = FastAPI()
    app.middleware("http")(auth_middleware)
    return app


def _mount(app, entries, manifests=()):
    from backend.plugins_host import mount_plugin_routes

    registries = Registries()
    reg = registries.registry_for("backend.routes")
    for owner, name, spec in entries:
        reg.register_contribution(Contribution(name, (lambda s=spec: s)), owner=owner)
    return mount_plugin_routes(app, registries, manifests=list(manifests))


def test_user_route_mounts_and_is_fail_closed(host_app):
    report = _mount(host_app, [("acme.weather", "api", RouterSpec(_router("w"), "/api/x/acme.weather"))])
    assert report.mounted == [("acme.weather", "api", "/api/x/acme.weather")]
    client = TestClient(host_app)
    assert client.get("/api/x/acme.weather/ping").status_code == 401
    assert client.get("/api/x/acme.weather/ping", headers={"X-User-Id": "u1"}).json() == {"ok": "w"}


def test_auth_none_route_is_public_and_only_that_prefix(host_app):
    from backend import auth as auth_mod

    _mount(
        host_app,
        [
            ("acme.weather", "hook", RouterSpec(_router("open"), "/api/x/acme.weather/webhook", auth="none")),
            ("acme.weather", "api", RouterSpec(_router("closed"), "/api/x/acme.weather/private")),
        ],
        manifests=[_manifest("acme.weather", public=("/api/x/acme.weather/webhook",))],
    )
    assert "/api/x/acme.weather/webhook" in auth_mod.PLUGIN_EXEMPT_PREFIXES
    client = TestClient(host_app)
    assert client.get("/api/x/acme.weather/webhook/ping").json() == {"ok": "open"}
    assert client.get("/api/x/acme.weather/private/ping").status_code == 401
    # segment boundary: a sibling prefix that merely STARTS with the public one stays authenticated
    assert client.get("/api/x/acme.weather/webhook-admin/ping").status_code == 401
    assert client.get("/api/x/acme.weather2/webhook/ping").status_code == 401


def test_foreign_prefix_is_refused_not_mounted(host_app):
    report = _mount(host_app, [("acme.weather", "evil", RouterSpec(_router("x"), "/api/agents"))])
    assert report.mounted == []
    assert report.refused[0][:2] == ("acme.weather", "evil")
    assert TestClient(host_app).get("/api/agents/ping", headers={"X-User-Id": "u1"}).status_code == 404


def test_builtin_owner_may_use_any_api_prefix(host_app):
    report = _mount(host_app, [("builtin.teams", "teams", RouterSpec(_router("t"), "/api/teams-x"))])
    assert report.mounted[0][2] == "/api/teams-x"


def test_broken_factory_is_isolated(host_app):
    from backend.plugins_host import mount_plugin_routes

    registries = Registries()
    reg = registries.registry_for("backend.routes")

    def boom():
        raise RuntimeError("no router for you")

    reg.register_contribution(Contribution("api", boom), owner="acme.weather")
    report = mount_plugin_routes(host_app, registries, manifests=[])
    assert report.mounted == [] and "RuntimeError" in report.refused[0][2]


def test_main_app_mounts_plugin_routes_before_the_spa_fallback():
    import backend.main as main

    report = main.app.state.plugin_routes
    assert hasattr(report, "mounted")
    paths = [getattr(r, "path", "") for r in main.app.routes]
    # The SPA fallback (or root redirect) is after the API routes; plugin
    # mounting happened before it. No user plugins in tests → only builtin
    # feature plugins (builtin.teams' /api/teams) are mounted.
    assert [m for m in report.mounted if not m[0].startswith("builtin.")] == []
    assert ("builtin.teams", "teams", "/api/teams") in report.mounted
    assert any(p.startswith("/api/") for p in paths)
