"""
@file_name: test_plugins_routes.py
@author: NarraNexus
@date: 2026-08-28
@description: Route-level tests for /api/plugins — list/install/uninstall
over backend.integrations.plugins.PluginService, and the cloud-mode 403 gate
on the two mutating verbs (install is platform-managed in cloud).
"""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.plugins.routes as plugins_mod
from backend.integrations.plugins.service import PluginStatus


def _status(**overrides) -> PluginStatus:
    base = dict(
        id="claude_code",
        display_name="Claude Code",
        installed=False,
        version=None,
        target_version="0.1.43",
        update_available=False,
        logged_in=False,
        size_hint="~190 MB",
        busy=False,
    )
    base.update(overrides)
    return PluginStatus(**base)


@pytest.fixture
def make_client(monkeypatch):
    """Build a TestClient with only the plugins router mounted.

    Cloud/local is controlled the same way the providers.py tests do it:
    DATABASE_URL flavor, with NARRANEXUS_DEPLOYMENT_MODE cleared so an
    inherited env var can't override the test.
    """

    def _make(*, cloud: bool):
        from narranexus.platform.utils.deployment_mode import DEPLOYMENT_MODE_ENV_VAR
        monkeypatch.delenv(DEPLOYMENT_MODE_ENV_VAR, raising=False)
        monkeypatch.setenv(
            "DATABASE_URL",
            "mysql://u:p@h/db" if cloud else "sqlite:///local.db",
        )
        app = FastAPI()
        app.include_router(plugins_mod.router)
        return TestClient(app, raise_server_exceptions=False)

    return _make


# ───────────── GET /api/plugins ─────────────────────────────────────────

def test_list_plugins_returns_status_and_cloud_managed_false_locally(make_client, monkeypatch):
    statuses = [_status(), _status(id="codex_cli", display_name="Codex CLI")]
    monkeypatch.setattr(plugins_mod._service, "list_plugins", lambda: statuses)
    client = make_client(cloud=False)

    resp = client.get("/api/plugins")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["cloud_managed"] is False
    assert [p["id"] for p in body["data"]["plugins"]] == ["claude_code", "codex_cli"]
    assert body["data"]["plugins"][0] == asdict(statuses[0])


def test_list_plugins_reports_cloud_managed_true_in_cloud(make_client, monkeypatch):
    monkeypatch.setattr(plugins_mod._service, "list_plugins", lambda: [_status()])
    client = make_client(cloud=True)

    resp = client.get("/api/plugins")

    assert resp.status_code == 200
    assert resp.json()["data"]["cloud_managed"] is True


# ───────────── POST /api/plugins/{id}/install ───────────────────────────

def test_install_forbidden_in_cloud_mode(make_client):
    client = make_client(cloud=True)

    resp = client.post("/api/plugins/claude_code/install")

    assert resp.status_code == 403


def test_install_unknown_plugin_id_is_404_before_streaming(make_client):
    # No monkeypatch on the real service — this proves the KeyError raised
    # inside PluginService.install's async-generator body is caught and
    # turned into a 404 BEFORE StreamingResponse commits a 200 status line.
    client = make_client(cloud=False)

    resp = client.post("/api/plugins/bogus_plugin/install")

    assert resp.status_code == 404


def test_install_streams_ndjson_progress_then_a_final_done_event(make_client, monkeypatch):
    events = [
        {"phase": "pip", "line": "Collecting claude-agent-sdk", "done": False},
        {"phase": "npm", "line": "added 1 package", "done": False},
        {"done": True, "ok": True, "error": None, "status": asdict(_status(installed=True))},
    ]

    async def fake_install(plugin_id):
        for event in events:
            yield event

    monkeypatch.setattr(plugins_mod._service, "install", fake_install)
    client = make_client(cloud=False)

    resp = client.post("/api/plugins/claude_code/install")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in resp.text.strip().splitlines()]
    assert lines == events


# ───────────── POST /api/plugins/{id}/uninstall ─────────────────────────

def test_uninstall_forbidden_in_cloud_mode(make_client):
    client = make_client(cloud=True)

    resp = client.post("/api/plugins/claude_code/uninstall")

    assert resp.status_code == 403


def test_uninstall_unknown_plugin_id_is_404(make_client):
    # Real service, unpatched — PluginService.uninstall raises KeyError for
    # an unknown id synchronously, which the route must map to 404.
    client = make_client(cloud=False)

    resp = client.post("/api/plugins/bogus_plugin/uninstall")

    assert resp.status_code == 404


def test_uninstall_returns_refreshed_status(make_client, monkeypatch):
    async def fake_uninstall(plugin_id):
        return None

    monkeypatch.setattr(plugins_mod._service, "uninstall", fake_uninstall)
    monkeypatch.setattr(
        plugins_mod._service, "list_plugins", lambda: [_status(installed=False)]
    )
    client = make_client(cloud=False)

    resp = client.post("/api/plugins/claude_code/uninstall")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["id"] == "claude_code"
    assert body["data"]["installed"] is False


def test_uninstall_while_busy_is_409(make_client, monkeypatch):
    from backend.integrations.plugins.errors import PluginBusyError

    async def busy_uninstall(plugin_id):
        raise PluginBusyError(plugin_id, "Claude Code is busy; cannot uninstall right now")

    monkeypatch.setattr(plugins_mod._service, "uninstall", busy_uninstall)
    client = make_client(cloud=False)

    resp = client.post("/api/plugins/claude_code/uninstall")

    assert resp.status_code == 409
    assert "busy" in resp.json()["detail"].lower()
