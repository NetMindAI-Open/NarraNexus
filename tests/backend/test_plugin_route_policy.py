"""
@file_name: test_plugin_route_policy.py
@author: Bin Liang
@date: 2026-09-07
@description: The MANIFEST is the authority on a plugin route's auth="none" / quota_bypass, on BOTH mount paths, through the real auth middleware.

These are the only two safety knobs on "a plugin may mount backend routes", and
they had no test at all: deleting either check left the suite green. Each test
here drives the REAL ``backend.auth.auth_middleware`` (a hand-rolled identity
middleware cannot tell "wired behind the middleware" from "outside it"), and
covers both the eager path (``mount_plugin_routes``, everything registered at
import) and the lazy path (``mount_user_plugin_routes`` → ``LazyRouterApp``),
because a rule enforced on only one of them is the defect this file exists for.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from backend import auth as auth_mod
from backend.auth import auth_middleware, create_token
from backend.plugins_host import mount_plugin_routes, mount_user_plugin_routes
from narranexus.contracts.route import RouterSpec
from narranexus.kernel.plugins.builtins import slot_tree_with_builtins
from narranexus.kernel.plugins.manifest import parse_manifest
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.agent_framework.providers.resolver import (
    NoProviderConfiguredError,
)

ROUTES = {"backend.routes": ["nxplugins.acme:ROUTES"]}


def _get_router(text: str) -> APIRouter:
    r = APIRouter()

    @r.get("/ping")
    async def ping():
        return {"ok": text}

    return r


def _post_router(text: str) -> APIRouter:
    r = APIRouter()

    @r.post("/act")
    async def act():
        return {"ok": text}

    return r


def _manifest(pid: str, *, public=(), quota=(), provides=None):
    data = {
        "id": pid,
        "version": "0.1.0",
        "displayName": pid,
        "hosts": ["backend"],
        "backend": {
            "activate": True,
            "publicPrefixes": list(public),
            "quotaBypassPrefixes": list(quota),
        },
        "api": {"route": 0},
        "provides": provides if provides is not None else {},
    }
    return parse_manifest(data, tree=slot_tree_with_builtins())


@pytest.fixture
def auth_sets(monkeypatch):
    """``PLUGIN_EXEMPT_PREFIXES`` / ``PLUGIN_QUOTA_BYPASS_PREFIXES`` are
    module-level MUTABLE globals. Swap in fresh empty sets (monkeypatch restores
    the originals on teardown) so a leak from another test cannot make a
    401/402 expectation here pass for the wrong reason, and so what this test
    registers does not leak on to the next one."""
    monkeypatch.setattr(auth_mod, "PLUGIN_EXEMPT_PREFIXES", set())
    monkeypatch.setattr(auth_mod, "PLUGIN_QUOTA_BYPASS_PREFIXES", set())
    assert auth_mod.PLUGIN_EXEMPT_PREFIXES == set()
    assert auth_mod.PLUGIN_QUOTA_BYPASS_PREFIXES == set()


@pytest.fixture
def app(auth_sets) -> FastAPI:
    a = FastAPI()
    a.middleware("http")(auth_middleware)
    return a


@pytest.fixture
def force_cloud_mode(monkeypatch):
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)
    # In cloud the bound auth provider (builtin.auth.netmind) decodes the
    # session JWT through the kernel's WEB_HOST service. That is normally
    # exposed by importing backend.main; publish it here so this file does not
    # silently depend on some other test having imported the app first.
    from backend.plugin_sdk_host import install_web_host

    install_web_host()


@pytest.fixture
def jwt_headers():
    return {"Authorization": f"Bearer {create_token(user_id='alice', role='user')}"}


def _registries(entries) -> Registries:
    # Deliberately NOT load_builtins(): these tests assert on the whole
    # MountReport, so the 15 builtin routers would drown the one under test.
    regs = Registries()
    reg = regs.registry_for("backend.routes")
    for owner, name, spec in entries:
        reg.register_contribution(Contribution(name, (lambda s=spec: s)), owner=owner)
    return regs


# ------------------------------------------------------- publicPrefixes (eager)


def test_declared_public_prefix_is_reachable_unauthenticated_eager(app):
    """The one way a plugin gets an anonymous endpoint: the manifest said so."""
    spec = RouterSpec(_get_router("open"), "/api/x/acme.pub/webhook", auth="none")
    report = mount_plugin_routes(
        app,
        _registries([("acme.pub", "hook", spec)]),
        manifests=[_manifest("acme.pub", public=("/api/x/acme.pub/webhook",))],
    )
    assert report.refused == []
    assert "/api/x/acme.pub/webhook" in auth_mod.PLUGIN_EXEMPT_PREFIXES
    c = TestClient(app)
    assert c.get("/api/x/acme.pub/webhook/ping").json() == {"ok": "open"}


def test_undeclared_auth_none_is_refused_eager(app):
    """A runtime-computed ``auth="none"`` the user never approved must not mount.

    This is the eager half of the promise; before the fix it was enforced on
    the lazy path only, and a distribution's bundled plugin takes this one.
    """
    spec = RouterSpec(_get_router("sneaky"), "/api/x/acme.sneak/webhook", auth="none")
    report = mount_plugin_routes(
        app,
        _registries([("acme.sneak", "hook", spec)]),
        manifests=[_manifest("acme.sneak")],  # declares NOTHING
    )
    assert report.mounted == []
    assert report.refused[0][:2] == ("acme.sneak", "hook")
    assert "publicPrefixes" in report.refused[0][2]
    assert auth_mod.PLUGIN_EXEMPT_PREFIXES == set()
    assert TestClient(app).get("/api/x/acme.sneak/webhook/ping").status_code == 401


def test_owner_without_a_manifest_fails_closed_eager(app):
    """No manifest in hand (a registry entry from somewhere else) is not a pass."""
    spec = RouterSpec(_get_router("x"), "/api/x/acme.nomanifest/hook", auth="none")
    report = mount_plugin_routes(app, _registries([("acme.nomanifest", "hook", spec)]), manifests=[])
    assert report.mounted == [] and "publicPrefixes" in report.refused[0][2]


def test_builtin_owner_is_trusted_without_a_manifest(app):
    """Builtins are host code: the parser already reserves their prefixes."""
    spec = RouterSpec(_get_router("b"), "/api/builtin-open", auth="none")
    report = mount_plugin_routes(app, _registries([("builtin.teams", "open", spec)]), manifests=[])
    assert report.mounted == [("builtin.teams", "open", "/api/builtin-open")]
    assert TestClient(app).get("/api/builtin-open/ping").json() == {"ok": "b"}


# -------------------------------------------------------- publicPrefixes (lazy)


def test_declared_public_prefix_is_reachable_unauthenticated_lazy(app):
    """Registered at MOUNT time, so the anonymous first request can activate
    the plugin at all — the exemption cannot live inside activation."""
    spec = RouterSpec(_get_router("open"), "/api/x/acme.lazy/webhook", auth="none")
    regs = _registries([("acme.lazy", "hook", spec)])
    manifest = _manifest("acme.lazy", public=("/api/x/acme.lazy/webhook",), provides=ROUTES)
    mounted = mount_user_plugin_routes(app, regs, manifests=[manifest])
    # BEFORE any request: the middleware consults the set ahead of the router.
    assert "/api/x/acme.lazy/webhook" in auth_mod.PLUGIN_EXEMPT_PREFIXES
    assert not mounted["acme.lazy"].activated
    c = TestClient(app)
    assert c.get("/api/x/acme.lazy/webhook/ping").json() == {"ok": "open"}
    assert mounted["acme.lazy"].activated


def test_public_declaration_outside_the_plugins_own_prefix_is_ignored_lazy(app):
    """A plugin cannot open somebody else's path by naming it in its manifest."""

    @app.get("/api/agents/ping")
    async def agents_ping():
        return {"ok": "host"}

    manifest = _manifest("acme.greedy", public=("/api/agents", "webhook"), provides=ROUTES)
    mount_user_plugin_routes(app, _registries([]), manifests=[manifest])
    assert auth_mod.PLUGIN_EXEMPT_PREFIXES == {"/api/x/acme.greedy/webhook"}
    assert TestClient(app).get("/api/agents/ping").status_code == 401


def test_undeclared_auth_none_is_refused_lazy(app):
    spec = RouterSpec(_get_router("sneaky"), "/api/x/acme.lsneak/webhook", auth="none")
    regs = _registries([("acme.lsneak", "hook", spec)])
    mounted = mount_user_plugin_routes(
        app, regs, manifests=[_manifest("acme.lsneak", provides=ROUTES)]
    )
    # No exemption exists, so the anonymous caller is stopped at the middleware…
    assert TestClient(app).get("/api/x/acme.lsneak/webhook/ping").status_code == 401
    # …and an authenticated one still cannot activate it.
    r = TestClient(app).get("/api/x/acme.lsneak/webhook/ping", headers={"X-User-Id": "u1"})
    assert r.status_code == 503 and "publicPrefixes" in mounted["acme.lsneak"].error


def test_public_exemption_is_segment_matched_not_string_matched(app):
    """A sibling prefix that merely STARTS with the public one stays closed."""
    specs = [
        ("acme.seg", "hook", RouterSpec(_get_router("open"), "/api/x/acme.seg/webhook", auth="none")),
        ("acme.seg", "admin", RouterSpec(_get_router("closed"), "/api/x/acme.seg/webhook-admin")),
    ]
    mount_plugin_routes(
        app, _registries(specs), manifests=[_manifest("acme.seg", public=("/api/x/acme.seg/webhook",))]
    )
    c = TestClient(app)
    assert c.get("/api/x/acme.seg/webhook/ping").json() == {"ok": "open"}
    assert c.get("/api/x/acme.seg/webhook-admin/ping").status_code == 401


# --------------------------------------------------------------- quota_bypass


def _exhausted_resolver():
    resolver = MagicMock()
    resolver.resolve_and_set = AsyncMock(side_effect=NoProviderConfiguredError("alice"))
    return resolver


def test_declared_quota_bypass_route_is_reachable_while_a_sibling_402s(
    app, force_cloud_mode, jwt_headers,
):
    """The reason the knob exists: a quota-exhausted user must still reach the
    plugin's config endpoint. Authentication still applies — only the resolver
    is skipped — so the sibling route on the same plugin still 402s."""
    app.state.provider_resolver = _exhausted_resolver()
    specs = [
        ("acme.q", "config", RouterSpec(_post_router("config"), "/api/x/acme.q/config", quota_bypass=True)),
        ("acme.q", "run", RouterSpec(_post_router("run"), "/api/x/acme.q/run")),
    ]
    report = mount_plugin_routes(
        app, _registries(specs), manifests=[_manifest("acme.q", quota=("/api/x/acme.q/config",))]
    )
    assert report.refused == []
    assert auth_mod.PLUGIN_QUOTA_BYPASS_PREFIXES == {"/api/x/acme.q/config"}
    c = TestClient(app)
    assert c.post("/api/x/acme.q/config/act", headers=jwt_headers).json() == {"ok": "config"}
    assert c.post("/api/x/acme.q/run/act", headers=jwt_headers).status_code == 402
    # Bypassing the quota gate is not bypassing auth.
    assert c.post("/api/x/acme.q/config/act").status_code == 401


def test_undeclared_quota_bypass_is_refused(app):
    """A billing bypass the manifest never declared is a refusal, not a default."""
    spec = RouterSpec(_post_router("free"), "/api/x/acme.free/config", quota_bypass=True)
    report = mount_plugin_routes(
        app, _registries([("acme.free", "config", spec)]), manifests=[_manifest("acme.free")]
    )
    assert report.mounted == []
    assert "quotaBypassPrefixes" in report.refused[0][2]
    assert auth_mod.PLUGIN_QUOTA_BYPASS_PREFIXES == set()


def test_lazy_quota_bypass_is_registered_at_mount_time_not_at_activation(
    app, force_cloud_mode, jwt_headers,
):
    """The bug this pins down: registering the bypass inside activation is a
    self-blocking loop — the middleware 402s the request, so the plugin never
    activates, so the prefix is never registered."""
    app.state.provider_resolver = _exhausted_resolver()
    specs = [
        ("acme.lq", "config", RouterSpec(_post_router("config"), "/api/x/acme.lq/config", quota_bypass=True)),
        ("acme.lq", "run", RouterSpec(_post_router("run"), "/api/x/acme.lq/run")),
    ]
    manifest = _manifest("acme.lq", quota=("/api/x/acme.lq/config",), provides=ROUTES)
    mounted = mount_user_plugin_routes(app, _registries(specs), manifests=[manifest])
    # Registered BEFORE the first request, i.e. before any activation.
    assert not mounted["acme.lq"].activated
    assert auth_mod.PLUGIN_QUOTA_BYPASS_PREFIXES == {"/api/x/acme.lq/config"}
    c = TestClient(app)
    assert c.post("/api/x/acme.lq/config/act", headers=jwt_headers).json() == {"ok": "config"}
    assert c.post("/api/x/acme.lq/run/act", headers=jwt_headers).status_code == 402


def test_quota_bypass_declaration_outside_the_prefix_is_ignored(app):
    manifest = _manifest("acme.qg", quota=("/api/providers-x", "config"), provides=ROUTES)
    mount_user_plugin_routes(app, _registries([]), manifests=[manifest])
    assert auth_mod.PLUGIN_QUOTA_BYPASS_PREFIXES == {"/api/x/acme.qg/config"}
