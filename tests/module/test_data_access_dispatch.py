"""
@file_name: test_data_access_dispatch.py
@author: Bin Liang
@date: 2026-09-04
@description: DirectStore dispatches every capability method through agent.capabilities.data_access; the builtin providers cover the whole AgentDataStore surface (minus the platform memory methods), a disabled builtin degrades to the tool's own failure shape, and the twin routes ride with their plugins.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.contracts.data_access import DataAccessSpec
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.module_system.slots import DATA_ACCESS_SLOT
from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.platform.module_system.data_access import store as st

PLATFORM_METHODS = {"remember", "grep_memory", "memory_retain"}  # memory engine is platform, not a builtin


def _regs() -> Registries:
    regs = Registries()
    load_builtins(regs, "backend")
    return regs


def _protocol_methods() -> set[str]:
    return {n for n, _ in inspect.getmembers(st.AgentDataStore) if not n.startswith("_") and callable(getattr(st.AgentDataStore, n))}


def test_builtin_providers_cover_every_capability_method():
    names = set(_regs().registry_for(DATA_ACCESS_SLOT).names())
    assert names == _protocol_methods() - PLATFORM_METHODS


def test_every_provider_is_a_spec_named_after_its_entry():
    reg = _regs().registry_for(DATA_ACCESS_SLOT)
    for entry in reg.entries():
        spec = entry.factory()
        assert isinstance(spec, DataAccessSpec) and spec.name == entry.name and inspect.iscoroutinefunction(spec.handler)


def _store(regs: Registries) -> st.DirectStore:
    store = st.DirectStore(registries=regs)

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    return store


def test_dispatch_passes_db_first_then_the_method_arguments():
    regs = Registries()
    seen: list = []

    async def handler(db, agent_id, job_id, fields):
        seen.append((db, agent_id, job_id, fields))
        return {"success": True, "job_id": job_id}

    regs.registry_for(DATA_ACCESS_SLOT).register_contribution(Contribution("job_update", lambda: DataAccessSpec("job_update", handler)), owner="acme.jobs")
    store = _store(regs)
    assert asyncio.run(store.job_update("a1", "j1", {"title": "x"})) == {"success": True, "job_id": "j1"}
    db, agent_id, job_id, fields = seen[0]
    assert (agent_id, job_id, fields) == ("a1", "j1", {"title": "x"}) and db is not None


def test_missing_provider_degrades_to_each_tools_failure_shape():
    store = _store(Registries())  # nothing registered: every builtin "disabled"
    assert asyncio.run(store.update_awareness("a1", "x")).startswith("Error: update_awareness unavailable")
    assert asyncio.run(store.update_agent_profile("a1", "n", None)).startswith("Error: update_agent_profile unavailable")
    social = asyncio.run(store.delete_entity("a1", "e1"))
    assert social["success"] is False and "unavailable" in social["message"] and "results" not in social
    search = asyncio.run(store.search_social_network("a1", "bob", "name", 5))
    assert search["success"] is False and search["results"] == []
    view = asyncio.run(store.view_narrative("a1", "n1"))
    assert view == {"success": False, "error": st._unavailable_msg("view_narrative")}
    pause = asyncio.run(store.job_pause("a1", "j1"))
    assert pause == {"success": False, "job_id": "j1", "message": st._unavailable_msg("job_pause")}
    history = asyncio.run(store.get_chat_history("a1", "i1", 10))
    assert history["success"] is False and history["messages"] == [] and history["instance_id"] == "i1"


def test_parity_rejects_still_run_before_dispatch():
    store = _store(Registries())
    # An over-long query is rejected by the store itself — no provider consulted.
    reject = asyncio.run(store.job_retrieval_semantic("a1", "q" * 600, None, None, 5))
    assert reject["success"] is False and "unavailable" not in str(reject)


def test_provider_exception_is_wrapped_never_raised():
    regs = Registries()

    async def boom(db, *a):
        raise RuntimeError("db gone")

    reg = regs.registry_for(DATA_ACCESS_SLOT)
    reg.register_contribution(Contribution("view_event", lambda: DataAccessSpec("view_event", boom)), owner="acme.x")
    reg.register_contribution(Contribution("merge_entities", lambda: DataAccessSpec("merge_entities", boom)), owner="acme.x")
    store = _store(regs)
    assert asyncio.run(store.view_event("a1", "e1")) == {"success": False, "error": "db gone"}
    assert asyncio.run(store.merge_entities("a1", "e1", "e2", True)) == {"success": False, "message": "Error: db gone"}


def test_store_no_longer_imports_builtin_modules():
    src = inspect.getsource(st)
    assert "narranexus.platform.module_system." + "job_module" not in src
    assert "narranexus.platform.module_system." + "social_network_module" not in src
    assert "narranexus.platform.module_system." + "chat_module" not in src


# ---- twin routes travel with their plugins


TWINS = {
    "builtin.awareness": {"agents_awareness", "agents_profile"},
    "builtin.social_network": {"agents_social_network"},
    "builtin.basic_info": {"agents_narrative"},
    "builtin.job": {"agents_jobs", "jobs", "dashboard_jobs"},  # + its own /api/jobs and dashboard controls (3c.5)
    "builtin.chat": {"agents_chat_history"},
}


@pytest.mark.parametrize("plugin_id", sorted(TWINS))
def test_twin_routes_are_backend_routes_contributions_of_their_plugin(plugin_id: str, tmp_path, monkeypatch):
    from narranexus.hosts.boot import boot
    from narranexus.kernel.plugins.lifecycle import RegistryStore
    from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    store = RegistryStore(path=home / "registry.json", lkg=home / "lkg.json")
    regs = Registries()
    load_builtins(regs, "backend")
    boot("backend", registries=regs, cloud=False, host_version="1.19.0", store=store)
    names = {e.name for e in regs.registry_for("backend.routes").entries() if e.owner == plugin_id}
    assert names == TWINS[plugin_id]


def test_disabling_builtin_job_removes_its_twin_routes_and_provider(tmp_path, monkeypatch):
    from backend.plugins_host import mount_plugin_routes
    from narranexus.hosts.boot import boot
    from narranexus.kernel.plugins.lifecycle import RegistryStore
    from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    store = RegistryStore(path=home / "registry.json", lkg=home / "lkg.json")
    store.update(lambda reg: reg.builtin_overrides.__setitem__("builtin.job", {"enabled": False}))
    regs = Registries()
    load_builtins(regs, "backend")
    boot("backend", registries=regs, cloud=False, host_version="1.19.0", store=store)
    assert "job_create" not in regs.registry_for(DATA_ACCESS_SLOT).names()
    assert "view_narrative" in regs.registry_for(DATA_ACCESS_SLOT).names()
    app = FastAPI()
    mount_plugin_routes(app, regs)
    client = TestClient(app)
    assert client.get("/api/agents/a1/jobs/j1").status_code == 404
    # basic_info's twin is still mounted: the request reaches the handler
    # and gets its normal 200 + `{"success": false, ...}` not-found
    # envelope, not FastAPI's catch-all 404 for an unrouted path — a 500
    # or any other non-404 status would also satisfy "!= 404".
    r = client.get("/api/agents/a1/narratives/n1")
    assert r.status_code == 200 and r.json() == {"success": False, "error": "narrative n1 not found"}


def test_core_router_no_longer_includes_the_twins():
    import backend.routes.agents.core as core

    src = inspect.getsource(core)
    for name in ("awareness_router", "social_network_router", "chat_history_router", "narrative_router", "jobs_router", "profile_router"):
        assert f"include_router({name})" not in src
