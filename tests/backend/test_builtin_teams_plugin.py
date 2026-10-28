"""
@file_name: test_builtin_teams_plugin.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.teams is a feature-level plugin — its router and summary worker arrive through backend.routes / backend.workers, and disabling it removes /api/teams and the worker while every other builtin keeps booting.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.contracts.route import RouterSpec
from narranexus.contracts.worker import WorkerSpec
from narranexus.hosts.boot import boot
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.kernel.plugins.builtins import load_builtins

TEAMS = "builtin.teams"


def _boot(tmp_path: Path, monkeypatch, *, disable: str | None):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    store = RegistryStore(path=home / "registry.json", lkg=home / "lkg.json")
    if disable:
        store.update(lambda reg: reg.builtin_overrides.__setitem__(disable, {"enabled": False}))
    regs = Registries()
    load_builtins(regs, "backend")
    report = boot("backend", registries=regs, cloud=False, host_version="1.19.0", store=store)
    report.mark_healthy()
    return regs, report, store


def _owners(regs: Registries, slot: str) -> set[str]:
    return {e.owner for e in regs.registry_for(slot).entries()}


def test_teams_router_and_worker_are_manifest_contributions(tmp_path: Path, monkeypatch):
    regs, report, _ = _boot(tmp_path, monkeypatch, disable=None)
    loaded = {pl.plugin_id for pl in report.builtins.loaded}
    assert TEAMS in loaded and not report.builtins.errors
    routes = {e.name: e.factory() for e in regs.registry_for("backend.routes").entries() if e.owner == TEAMS}
    assert set(routes) == {"teams", "marketplace_teams"}
    assert all(isinstance(spec, RouterSpec) for spec in routes.values())
    assert routes["teams"].prefix == "/api/teams" and routes["marketplace_teams"].prefix == "/api/marketplace/teams"
    (worker,) = [e for e in regs.registry_for("backend.workers").entries() if e.owner == TEAMS]
    wspec = worker.factory()
    assert isinstance(wspec, WorkerSpec) and wspec.host == "backend" and wspec.name == "team_summary"


def test_disable_builtin_teams_degrades_cleanly(tmp_path: Path, monkeypatch):
    from backend.plugins_host import mount_plugin_routes, start_backend_workers

    regs, report, _ = _boot(tmp_path, monkeypatch, disable=TEAMS)
    assert report.disabled_builtins == (TEAMS,)
    assert TEAMS not in _owners(regs, "backend.routes") and TEAMS not in _owners(regs, "backend.workers")
    # Every other builtin still loaded, and pure chat survives (the chat module row is intact).
    assert report.builtins is not None and not report.builtins.errors
    assert "builtin.chat" in {pl.plugin_id for pl in report.builtins.loaded}
    app = FastAPI()
    mounted = mount_plugin_routes(app, regs)
    assert all(owner != TEAMS for owner, _, _ in mounted.mounted)
    client = TestClient(app)
    assert client.get("/api/teams/").status_code == 404
    assert client.post("/api/teams/", json={}).status_code == 404
    started = asyncio.run(start_backend_workers(app, regs, db=object()))
    assert started == []


def test_enabled_builtin_teams_mounts_and_worker_starts(tmp_path: Path, monkeypatch):
    from backend.plugins_host import mount_plugin_routes, start_backend_workers, stop_backend_workers
    from narranexus_plugins.teams import summary_worker as tsw

    calls: list[str] = []

    async def fake_start(self):
        calls.append("start")

    async def fake_stop(self):
        calls.append("stop")

    monkeypatch.setattr(tsw.TeamSummaryWorker, "start", fake_start)
    monkeypatch.setattr(tsw.TeamSummaryWorker, "stop", fake_stop)
    regs, _, _ = _boot(tmp_path, monkeypatch, disable=None)
    app = FastAPI()
    mounted = mount_plugin_routes(app, regs)
    assert (TEAMS, "teams", "/api/teams") in mounted.mounted
    # The router is really there: an unknown team id is a 404 from the teams handler, not a bare mount miss.
    paths = {getattr(r, "path", "") for r in app.routes}
    assert any(p.startswith("/api/teams") for p in paths)

    async def scenario():
        started = await start_backend_workers(app, regs, db=object())
        assert started == [f"{TEAMS}:team_summary"]
        await asyncio.sleep(0)
        assert calls == ["start"]
        await stop_backend_workers(app)
        assert calls == ["start", "stop"] and app.state.plugin_backend_workers == []

    asyncio.run(scenario())


def test_backend_workers_only_start_host_backend_specs():
    from backend.plugins_host import start_backend_workers, stop_backend_workers

    regs = Registries()
    reg = regs.registry_for("backend.workers")
    log: list[str] = []

    class Handle:
        def __init__(self):
            self._ev = asyncio.Event()
            self.run = self._run()

        async def _run(self):
            log.append("run")
            await self._ev.wait()
            log.append("done")

        def stop(self):
            self._ev.set()

    async def backend_factory(ctx):
        log.append(f"db={ctx.db}")
        return Handle()

    async def worker_factory(ctx):  # pragma: no cover — must not be called
        raise AssertionError("host=worker spec must not start in the API process")

    reg.register_contribution(Contribution("a", lambda: WorkerSpec("a", backend_factory, host="backend")), owner="acme.a")
    reg.register_contribution(Contribution("b", lambda: WorkerSpec("b", worker_factory, host="worker")), owner="acme.b")
    app = FastAPI()

    async def scenario():
        assert await start_backend_workers(app, regs, db="DB") == ["acme.a:a"]
        await asyncio.sleep(0)
        assert log == ["db=DB", "run"]
        await stop_backend_workers(app)
        assert log[-1] == "done"

    asyncio.run(scenario())


def test_register_builtins_for_import_respects_overrides(tmp_path: Path, monkeypatch):
    from backend.plugins_host import register_builtins_for_import

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    RegistryStore(path=home / "registry.json", lkg=home / "lkg.json").update(
        lambda reg: reg.builtin_overrides.__setitem__(TEAMS, {"enabled": False})
    )
    regs = Registries()
    load_builtins(regs, "backend")
    assert register_builtins_for_import(regs) == (TEAMS,)
    assert TEAMS not in _owners(regs, "backend.routes")
    assert "builtin.nexus_plugins_module" in _owners(regs, "agent.capabilities.modules")


@pytest.mark.parametrize("protected_id", ["builtin.nexus_plugins_module"])
def test_protected_builtin_cannot_be_disabled_through_import_path(tmp_path: Path, monkeypatch, protected_id):
    from backend.plugins_host import register_builtins_for_import

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    RegistryStore(path=home / "registry.json", lkg=home / "lkg.json").update(
        lambda reg: reg.builtin_overrides.__setitem__(protected_id, {"enabled": False})
    )
    regs = Registries()
    load_builtins(regs, "backend")
    assert register_builtins_for_import(regs) == ()
