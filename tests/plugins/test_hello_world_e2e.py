"""
@file_name: test_hello_world_e2e.py
@author: Bin Liang
@date: 2026-09-03
@description: hello-world linked through the CLI, booted for the three backend roles: every contribution kind is visible; disabling it removes them all.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.cli.main import main as cli
from narranexus.contracts.agent.stages import Stage
from narranexus.hosts.boot import boot
from narranexus.kernel.plugins.importer import import_plugin_module, plugin_finder, uninstall_synthetic_package
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME, registry_path
from narranexus.kernel.plugins.registries import Registries
from narranexus.sdk.testing import PluginTestHost

HELLO = Path(__file__).resolve().parent / "hello_world"
PID = "acme.hello_world"



def _user_names(registries, path: str) -> tuple[str, ...]:
    """Contribution names in ``path`` that come from user plugins (builtin feature plugins such as builtin.teams excluded)."""
    return tuple(e.name for e in registries.registry_for(path).entries() if not e.owner.startswith("builtin."))

@pytest.fixture
def home(tmp_path: Path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(h))
    yield h
    uninstall_synthetic_package(PID)
    plugin_finder().unregister_deps(PID)


def _boot(role, registries=None, register_table=None):
    registries = registries or Registries()
    report = boot(role, registries=registries, cloud=False, host_version="1.19.0", store=RegistryStore(path=registry_path()), register_table=register_table)
    report.mark_healthy()
    return registries, report


def test_cli_link_then_every_role_sees_its_contributions(home: Path, capsys):
    assert cli(["plugin", "link", str(HELLO)]) == 0
    assert cli(["plugin", "list"]) == 0
    assert PID in capsys.readouterr().out
    assert cli(["plugin", "doctor", "--json"]) == 0
    assert PID in capsys.readouterr().out

    tables = []
    backend, report = _boot("backend", register_table=lambda spec, owner: tables.append((spec.name, owner)))
    assert report.user_plugin_ids == (PID,) and report.isolated == {}
    assert _user_names(backend, "backend.routes") == ("hello_world_api", "hello_world_webhook")
    assert tables == [("ext_acme_hello_world__greetings", PID)]
    assert backend.registry_for("backend.settings").get("hello_world_settings").fields["token"].secret is True
    assert [t.name for t in backend.registry_for("agent.capabilities.tools").get("hello_world").list_tools()] == ["hello_wave", "hello_now"]
    assert backend.registry_for("agent.capabilities.mcp_servers").get("hello_world").url == "https://example.com/mcp"
    assert backend.registry_for("content.bundles").get("hello_world_team").path.is_file()
    assert backend.registry_for("content.skills").get("hello_skill").manifest_path.is_file()
    assert backend.hooks.caller("onDidPersistTurn").owners() == (PID,)
    assert "hello_graph" in backend.registry_for("turn.pipeline.recall").names()
    assert backend.registry_for("turn.profiles").get("hello_research").strategy_for(Stage.RECALL) == "hello_graph"
    assert backend.registry_for("agent.capabilities.context_providers").get("hello_calendar").name == "hello_calendar"
    asyncio.run(backend.hooks.caller("onDidPersistTurn").call(run_id="r1", agent_id="a1", user_id="u1", event_id="e1", narrative_ids=[]))
    assert import_plugin_module(PID).HOOK_CALLS == ["a1:r1"]

    # routes mount on a real FastAPI app with the real auth middleware
    from backend.auth import auth_middleware
    from backend.plugins_host import mount_plugin_routes

    from narranexus.kernel.plugins.loader import discover

    app = FastAPI()
    app.middleware("http")(auth_middleware)
    # The manifest is the authority on what is public: the mount reads the
    # plugin's declared publicPrefixes (no manifest → auth="none" is refused).
    mount_plugin_routes(app, backend, manifests=discover(cloud=False, user_registry_path=registry_path(), host_version="1.19.0").manifests)
    client = TestClient(app)
    assert client.get("/api/x/acme.hello_world/hello").status_code == 401
    assert client.get("/api/x/acme.hello_world/hello", headers={"X-User-Id": "u1"}).json()["message"] == "hello"
    assert client.post("/api/x/acme.hello_world/webhook/ping").json() == {"ok": True}  # auth=none

    # the mcp role registers the same declarative contributions (consumers pick what applies)
    mcp, _ = _boot("mcp")
    assert mcp.registry_for("agent.capabilities.tools").names() == ("hello_world",)
    workers, _ = _boot("workers")
    from narranexus.platform.module_system.run_worker_supervisor import build_specs

    assert [s.name for s in build_specs(registries=workers)][-1] == f"{PID}:hello_world_greeter"


def test_backend_boot_with_the_real_table_registrar_does_not_isolate_the_plugin(home: Path, capsys):
    """The backend host passes ``schema_registry.register_table`` as the registrar; the
    fixture plugin's table must register through it (not only through a test lambda),
    or a real install of any plugin with a table is isolated at boot."""
    from narranexus.platform.utils.db import schema_registry as sr

    assert cli(["plugin", "link", str(HELLO)]) == 0
    saved_tables, saved_owners = dict(sr.TABLES), dict(sr.TABLE_OWNERS)
    try:
        _backend, report = _boot("backend", register_table=sr.register_table)
        assert PID not in report.rejected, report.rejected
        assert PID not in report.isolated, report.isolated
        assert sr.TABLE_OWNERS["ext_acme_hello_world__greetings"] == PID
    finally:
        sr.TABLES.clear(); sr.TABLES.update(saved_tables)
        sr.TABLE_OWNERS.clear(); sr.TABLE_OWNERS.update(saved_owners)


def test_disabling_removes_every_contribution(home: Path, capsys):
    assert cli(["plugin", "link", str(HELLO)]) == 0
    assert cli(["plugin", "disable", PID]) == 0
    registries, report = _boot("backend")
    assert report.user_plugin_ids == ()
    for path in ("backend.routes", "agent.capabilities.tools", "content.skills"):
        assert _user_names(registries, path) == ()
    assert cli(["plugin", "enable", PID, "--ack"]) == 0
    registries2, report2 = _boot("backend")
    assert report2.user_plugin_ids == (PID,)
    assert cli(["plugin", "uninstall", PID]) == 0
    assert cli(["plugin", "list"]) == 0
    assert "no user plugins" in capsys.readouterr().out


def test_plugin_test_host_activates_with_settings(tmp_path: Path):
    with PluginTestHost(HELLO, tmp_path / "home") as host:
        ctx = asyncio.run(host.activate())
        assert ctx.settings.get("greeting") == "hi"
        assert import_plugin_module(PID).ACTIVATIONS == [PID]
        assert TestClient(host.test_app()).get("/api/x/acme.hello_world/hello").json()["plugin"] == PID
    assert os.environ.get(ENV_PLUGIN_HOME) != str(tmp_path / "home")


def test_publish_check_passes_for_hello_world(capsys):
    assert cli(["plugin", "publish-check", str(HELLO)]) == 1  # frontend integrity not set on purpose
    out = capsys.readouterr().out
    assert "frontend.integrity" in out and "README" not in out
