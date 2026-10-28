"""
@file_name: test_engine.py
@author: Bin Liang
@date: 2026-09-04
@description: The headless Engine (spec section 19.4): `Engine.load(dist)` boots one distribution on private registries (builtins outside it gone, bundled plugins in), refuses a second boot of the same registries, `run_turn` streams the AgentRuntime's messages with the engine's registries, `agents()` lists own + public agents, `events()` is a subscribable bus, and `close()` releases only a db it owns.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from narranexus.engine import Engine
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME
from narranexus.kernel.plugins.registries import Registries

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _forget_bundled_packages():
    """Bundled plugins get a synthetic ``nxplugins.<id>`` package; each test must leave the process clean."""
    from narranexus.kernel.plugins.importer import uninstall_synthetic_package

    yield
    for pid in ("acme.crm", "acme.auth-sso", "acme.sso"):
        uninstall_synthetic_package(pid)


@pytest.fixture
def home(tmp_path: Path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(h))
    return h


def test_load_boots_the_distribution_on_private_registries(home: Path):
    regs = Registries()
    engine = Engine.load(REPO / "distributions" / "example-tob", registries=regs, cloud=False, host_version="1.15.0")
    assert engine.report.distribution == "acme.crm-agent" and regs.frozen
    assert "acme.crm" in engine.plugin_ids and "acme.auth-sso" in engine.plugin_ids and "builtin.teams" not in engine.plugin_ids
    assert {e.owner for e in regs.registry_for("kernel.auth").entries()} == {"acme.auth-sso"}
    with pytest.raises(RuntimeError, match="already booted"):
        Engine.load(None, registries=regs, cloud=False, host_version="1.15.0")


def test_load_without_a_distribution_boots_every_builtin(home: Path):
    engine = Engine.load(None, registries=Registries(), cloud=False, host_version="1.15.0")
    assert engine.distribution is None and "builtin.teams" in engine.plugin_ids and "builtin.auth.local" in engine.plugin_ids


def test_run_turn_streams_the_runtime_with_the_engine_registries(home: Path, monkeypatch):
    engine = Engine.load(REPO / "distributions" / "minimal", registries=Registries(), cloud=False, host_version="1.15.0")
    seen = {}

    class FakeRuntime:
        def __init__(self, database_client=None, registries=None, **_):
            seen["registries"] = registries
            seen["db"] = database_client

        async def run(self, agent_id, user_id, input_content, **kw):
            seen["call"] = (agent_id, user_id, input_content, kw["working_source"], kw["pipeline_profile"])
            yield {"type": "text", "content": "hi"}
            yield {"type": "done"}

    import narranexus.platform.agent_runtime as rt

    monkeypatch.setattr(rt, "AgentRuntime", FakeRuntime)
    engine.use_db(object())

    async def collect():
        return [m async for m in engine.run_turn("agent_1", "u1", "hello", pipeline_profile="fast")]

    msgs = asyncio.run(collect())
    assert [m["type"] for m in msgs] == ["text", "done"]
    assert seen["registries"] is engine.registries and seen["call"] == ("agent_1", "u1", "hello", "chat", "fast")


@pytest.mark.asyncio
async def test_agents_lists_own_and_public_and_close_keeps_a_borrowed_db(home: Path, db_client):
    from narranexus.platform.repository.agent_repository import AgentRepository
    from narranexus.platform.schema.entity_schema import Agent

    engine = Engine.load(REPO / "distributions" / "minimal", registries=Registries(), cloud=False, host_version="1.15.0").use_db(db_client)
    repo = AgentRepository(db_client)
    for aid, owner, public in (("a_mine", "u1", False), ("a_other", "u2", False), ("a_pub", "u2", True)):
        await repo.insert(Agent(agent_id=aid, agent_name=aid, created_by=owner, is_public=public))
    mine = {a["agent_id"] for a in await engine.agents("u1")}
    assert mine == {"a_mine", "a_pub"}
    assert {a["agent_id"] for a in await engine.agents()} == {"a_mine", "a_other", "a_pub"}
    got = []
    engine.events().subscribe("onDidStartRun", lambda payload: got.append(payload), owner="test")
    await engine.events().emit("onDidStartRun", {"run_id": "r1"})
    assert got == [{"run_id": "r1"}]  # a subscribable bus: the subscriber sees the emit
    await engine.close()
    assert engine._db is None
    assert await repo.find({}) and len(await repo.find({})) == 3  # the borrowed client is still open
