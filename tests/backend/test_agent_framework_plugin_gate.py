"""
@file_name: test_agent_framework_plugin_gate.py
@author: NarraNexus
@date: 2026-08-28
@description: The local/desktop plugin split (Claude Code / Codex CLI are now
installed on demand into ~/.narranexus/plugins, see plugin_paths.py) adds two
new behaviours to the agent-framework endpoints:

  - GET /api/providers/agent-framework now reports a ``frameworks`` list with
    per-framework ``available`` state, sourced from
    ``plugin_paths.framework_installed``.
  - POST /api/providers/agent-framework fail-closed 409s when switching TO a
    framework whose plugin isn't installed (nexus_power is always exempt).

Deleting either behaviour must turn the corresponding assertion red — the
route's own gates (cloud 403, unknown-framework 400) are exercised elsewhere
(test_provider_oauth_gating.py, test_agent_framework_switch_gate.py) and are
only reused here as scaffolding so the new checks are reached.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.providers as providers_mod

USER = {"X-User-Id": "u1"}


class _StubService:
    def __init__(self, framework: str = "claude_code"):
        self._framework = framework
        self.set_calls: list[str] = []

    async def get_user_agent_framework(self, _uid):
        return self._framework

    async def set_user_agent_framework(self, _uid, framework):
        self.set_calls.append(framework)
        return False  # slot_cleared


@pytest.fixture
def make_client(monkeypatch):
    def _make(*, cloud: bool = False, service: _StubService | None = None):
        from narranexus.platform.utils.deployment_mode import DEPLOYMENT_MODE_ENV_VAR
        monkeypatch.delenv(DEPLOYMENT_MODE_ENV_VAR, raising=False)
        monkeypatch.setenv(
            "DATABASE_URL",
            "mysql://u:p@h/db" if cloud else "sqlite:///local.db",
        )

        svc = service or _StubService()

        async def _get_service():
            return svc

        async def _probe(_framework, user_id=None):
            return {"ok": True, "detail": "stub probe"}

        monkeypatch.setattr(providers_mod, "_get_service", _get_service)
        monkeypatch.setattr(providers_mod, "_probe_agent_framework_auth", _probe)

        app = FastAPI()

        @app.middleware("http")
        async def fake_auth(request: Request, call_next):
            request.state.user_id = request.headers.get("X-User-Id") or None
            role = request.headers.get("X-Role")
            if role:
                request.state.role = role
            return await call_next(request)

        app.include_router(providers_mod.router, prefix="/api/providers")
        return TestClient(app, raise_server_exceptions=False), svc

    return _make


# ───────────── GET /agent-framework — frameworks field ──────────────────

def test_get_agent_framework_reports_per_framework_availability(make_client, monkeypatch):
    installed = {"claude_code": True, "codex_cli": False, "nexus_power": True}
    monkeypatch.setattr(providers_mod, "framework_installed", lambda name: installed[name])
    client, _svc = make_client()

    resp = client.get("/api/providers/agent-framework", headers=USER)

    assert resp.status_code == 200
    frameworks = {f["name"]: f["available"] for f in resp.json()["data"]["frameworks"]}
    assert frameworks == installed


def test_get_agent_framework_frameworks_reflects_uninstalled_plugin(make_client, monkeypatch):
    # Negative case: nothing installed except the built-in framework.
    monkeypatch.setattr(providers_mod, "framework_installed", lambda name: name == "nexus_power")
    client, _svc = make_client()

    resp = client.get("/api/providers/agent-framework", headers=USER)

    frameworks = {f["name"]: f["available"] for f in resp.json()["data"]["frameworks"]}
    assert frameworks["claude_code"] is False
    assert frameworks["codex_cli"] is False
    assert frameworks["nexus_power"] is True


# ───────────── POST /agent-framework — fail-closed install gate ─────────

def test_set_framework_rejects_uninstalled_plugin_with_409(make_client, monkeypatch):
    monkeypatch.setattr(providers_mod, "framework_installed", lambda name: False)
    client, svc = make_client()

    resp = client.post(
        "/api/providers/agent-framework",
        json={"framework": "claude_code"},
        headers=USER,
    )

    assert resp.status_code == 409
    assert "not installed" in resp.json()["detail"]
    assert svc.set_calls == []  # never reached the service


def test_set_framework_allows_installed_plugin(make_client, monkeypatch):
    monkeypatch.setattr(providers_mod, "framework_installed", lambda name: True)
    client, svc = make_client()

    resp = client.post(
        "/api/providers/agent-framework",
        json={"framework": "claude_code"},
        headers=USER,
    )

    assert resp.status_code == 200
    assert svc.set_calls == ["claude_code"]


def test_set_framework_host_shipped_framework_passes_gate_without_any_package(make_client, monkeypatch):
    # nexus_power declares no install recipe, so the registry-driven
    # framework_installed reports it available even when NO package probe
    # succeeds — the route needs no name-keyed exemption for it.
    from narranexus.platform.agent_framework import plugin_paths

    monkeypatch.setattr(plugin_paths, "package_installed", lambda framework, package: False)
    client, svc = make_client()

    resp = client.post(
        "/api/providers/agent-framework",
        json={"framework": "nexus_power"},
        headers=USER,
    )

    assert resp.status_code == 200
    assert svc.set_calls == ["nexus_power"]


def test_set_framework_cloud_gate_still_runs_before_install_gate(make_client, monkeypatch):
    # Cloud non-staff switching to codex_cli must still 403 on the
    # cloud_policy gate, never reaching the (irrelevant, since it never
    # runs in cloud for real) install-gate 409.
    monkeypatch.setattr(providers_mod, "framework_installed", lambda name: False)
    client, svc = make_client(cloud=True)

    resp = client.post(
        "/api/providers/agent-framework",
        json={"framework": "codex_cli"},
        headers=USER,
    )

    assert resp.status_code == 403
    assert svc.set_calls == []


@pytest.mark.asyncio
async def test_ensure_codex_installed_activates_plugin_pyenv_first(tmp_path, monkeypatch):
    """I-new-6 guard: _ensure_codex_installed must activate the plugin pyenv
    BEFORE importing codex_cli_bin (so a Codex plugin installed this session
    resolves without an app restart), AND its failure text must point at
    Settings → Plugins, not the stale "Run uv sync".

    Hermetic: NARRANEXUS_PLUGIN_HOME → empty tmp dir (no real ~/.narranexus on
    sys.path), and codex_cli_bin is forced un-importable so the failure branch
    (the one carrying the user-facing text) actually runs — on CI the base env
    has it, so without this the branch is skipped."""
    import sys

    from narranexus.platform.agent_framework import plugin_paths

    monkeypatch.setenv("NARRANEXUS_PLUGIN_HOME", str(tmp_path / "plugins"))
    monkeypatch.setitem(sys.modules, "codex_cli_bin", None)  # → ImportError on import

    called = {"n": 0}
    original = plugin_paths.activate_pyenv

    def _spy():
        called["n"] += 1
        return original()

    monkeypatch.setattr(plugin_paths, "activate_pyenv", _spy)

    result = await providers_mod._ensure_codex_installed()

    assert called["n"] >= 1  # activate_pyenv ran before the import
    assert result["installed"] is False
    assert "Settings" in result["reason"] and "Plugins" in result["reason"]
    # The stale "Run uv sync" guidance is gone (only the cloud hint may mention
    # `uv sync --extra plugins`).
    assert "uv sync" not in result["reason"] or "--extra plugins" in result["reason"]
