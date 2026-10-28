"""
@file_name: test_pipeline_scenarios.py
@author: Bin Liang
@date: 2026-09-04
@description: Spec §7.8 scenarios against the real AgentRuntime.run(): swap the Recall strategy through a plugin profile, observe commits with an onDidCommit hook, and contribute context providers.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest

from narranexus.contracts import UnknownEntry
from narranexus.contracts.agent.pipeline import PipelineProfile
from narranexus.contracts.agent.stages import CommitContext, Stage
from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.turn.stages import slot_path
from narranexus.platform.agent_runtime.agent_runtime import AgentRuntime
from narranexus.platform.schema.hook_schema import WorkingSource
from narranexus.platform.utils.background_tasks import pending


@pytest.fixture(autouse=True)
def patch_get_db(monkeypatch, db_client):
    from narranexus.platform.utils.db import db_factory

    async def _fake_get_db():
        return db_client

    monkeypatch.setattr(db_factory, "get_db_client", _fake_get_db)


@pytest.fixture(autouse=True)
def stub_preparation_steps(monkeypatch):
    """Steps 1/1.5/2/2.5 call helper LLMs; the default Recall is stubbed here (the swapped one is real)."""
    from narranexus.platform.agent_runtime import steps as ar

    async def _fake_step_1(ctx, narrative_service, session_service):
        ctx.narrative_list = []
        return
        yield  # pragma: no cover

    async def _fake_step_1_5(*a, **k):
        return None

    async def _fake_step_2(ctx):
        ctx.load_result = None
        ctx.module_list = []
        return
        yield  # pragma: no cover

    async def _fake_step_2_5(*a, **k):
        return
        yield  # pragma: no cover

    monkeypatch.setattr(ar, "step_1_select_narrative", _fake_step_1)
    monkeypatch.setattr(ar, "step_1_5_init_markdown", _fake_step_1_5)
    monkeypatch.setattr(ar, "step_2_load_modules", _fake_step_2)
    monkeypatch.setattr(ar, "step_2_5_sync_instances", _fake_step_2_5)


@pytest.fixture(autouse=True)
def patch_llm_config(monkeypatch):
    from narranexus.platform.agent_framework import api_config

    class _Configs:
        def __getattr__(self, _name):
            return None

    async def _configs(_agent_id: str):
        return _Configs()

    monkeypatch.setattr(api_config, "get_agent_owner_runtime_llm_configs", _configs, raising=False)


@pytest.fixture(autouse=True)
def helper_injection_succeeds(monkeypatch):
    async def _inject(_agent_id, _db):
        return "owner_1"

    monkeypatch.setattr("narranexus.platform.agent_framework.providers.resolver.inject_owner_helper_credentials", _inject)


@pytest.fixture(autouse=True)
async def no_task_leaks():
    yield
    leftovers = [t for t in pending() if t.get_name().startswith("post_turn_hooks:")]
    for t in leftovers:
        t.cancel()
    if leftovers:
        await asyncio.gather(*leftovers, return_exceptions=True)


class _Hooks:
    async def persist_turn(self, module_list, params):
        return None

    async def after_turn(self, module_list, params):
        return []

    async def hook_callback_results(self, **kwargs):
        return None

    async def gather(self, *a, **k):
        return {}


async def _seed_agent(db, agent_id: str) -> None:
    await db.insert("agents", {"agent_id": agent_id, "agent_name": agent_id, "created_by": "owner_1", "agent_type": "general", "is_public": 0})


async def _run(db_client, registries: Registries | None = None, **kw) -> list:
    await _seed_agent(db_client, kw.get("agent_id", "agent_s"))
    runtime = AgentRuntime(database_client=db_client, hook_manager=_Hooks(), registries=registries)
    gen = runtime.run(agent_id=kw.pop("agent_id", "agent_s"), user_id="owner_1", input_content="scenario", working_source=WorkingSource.CHAT, silent=True, **kw)
    return [m async for m in gen]


@pytest.mark.asyncio
async def test_scenario_1_swap_the_recall_strategy_through_a_plugin_profile(db_client):
    """§7.8-1: a plugin contributes RecallStrategy 'graph' and profile 'research'; a turn names the profile."""
    calls: list[str] = []

    class GraphRecall:
        stage = Stage.RECALL

        async def run(self, inputs) -> AsyncIterator[Any]:
            calls.append(inputs.ctx.agent_id)
            inputs.ctx.narrative_list = []
            return
            yield  # pragma: no cover

    # A private Registries (the process ones may be frozen by an earlier boot in the session).
    regs = Registries()
    load_builtins(regs, "backend")
    regs.registry_for(slot_path(Stage.RECALL)).register_contribution(Contribution("graph", GraphRecall), owner="acme.graph_recall")
    regs.registry_for("turn.profiles").register_contribution(
        Contribution("research", lambda: PipelineProfile(id="research", strategies={Stage.RECALL: "graph", Stage.ACT: "silent"})), owner="acme.graph_recall"
    )
    await _run(db_client, registries=regs, pipeline_profile="research")
    assert calls == ["agent_s"]
    with pytest.raises(UnknownEntry):
        await _run(db_client, registries=regs, agent_id="agent_t", pipeline_profile="nope")


@pytest.mark.asyncio
async def test_scenario_4_audit_hook_observes_every_commit(db_client):
    """§7.8-4: an audit plugin's onDidCommit hook receives a frozen CommitContext with the real event id."""
    seen: list[CommitContext] = []

    async def audit(stage, output, agent_id, run_id):
        seen.append(output)

    regs = Registries()
    load_builtins(regs, "backend")
    regs.hooks.add("onDidCommit", audit, owner="acme.audit")
    await _run(db_client, registries=regs)
    assert len(seen) == 1 and isinstance(seen[0], CommitContext)
    rows = await db_client.get("events", {"agent_id": "agent_s"})
    assert seen[0].event_id == str(rows[0]["event_id"] if "event_id" in rows[0] else rows[0]["id"])


def test_scenario_2_context_providers_are_selected_by_the_profile_filter():
    """§7.8-2: a contextProviders capability is picked up by Assemble; the profile's capability filter can exclude it."""
    from narranexus.contracts.agent.pipeline import CapabilityFilter
    from narranexus.kernel.plugins.registries import Registries
    from narranexus_plugins.turn.assemble import PROVIDERS_SLOT, context_providers

    class Calendar:
        name = "calendar"
        context_cost_hint = 120

        async def contribute_turn_context(self, ctx_data):
            return "Today: standup 10:00"

    regs = Registries()
    load_builtins(regs, "backend")
    regs.registry_for(PROVIDERS_SLOT).register_contribution(Contribution("calendar", Calendar), owner="acme.calendar")
    regs.registry_for(PROVIDERS_SLOT).register_contribution(Contribution("broken", lambda: (_ for _ in ()).throw(RuntimeError("x"))), owner="acme.b")
    assert [p.name for p in context_providers(PipelineProfile(id="default"), regs)] == ["calendar"]
    assert context_providers(PipelineProfile(id="voice", capability_filter=CapabilityFilter(exclude=("calendar",))), regs) == ()


@pytest.mark.asyncio
async def test_context_provider_sections_are_ordered_stable_then_volatile_and_isolated():
    from narranexus.platform.context_runtime.context_runtime import ContextRuntime
    from narranexus.platform.schema.context_schema import ContextData

    class Stable:
        name = "policy"

        async def contribute_instructions(self, ctx_data):
            return "Always cite sources."

    class Volatile:
        name = "calendar"

        async def contribute_turn_context(self, ctx_data):
            return "Today: standup 10:00"

    class Broken:
        name = "broken"

        async def contribute_instructions(self, ctx_data):
            raise RuntimeError("boom")

    class Silent:
        name = "silent"

        async def contribute_turn_context(self, ctx_data):
            return "   "

    ctx = ContextData(agent_id="a", user_id="u", input_content="x")
    text = await ContextRuntime._context_provider_sections((Volatile(), Broken(), Stable(), Silent()), ctx)
    assert text == "## policy\nAlways cite sources.\n\n## calendar\nToday: standup 10:00"
    assert await ContextRuntime._context_provider_sections((), ctx) == ""
