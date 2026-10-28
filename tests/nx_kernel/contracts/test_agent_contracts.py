"""
@file_name: test_agent_contracts.py
@author: Bin Liang
@date: 2026-09-03
@description: The agent contracts — stages, capabilities, pipeline profiles, agent spec, stage hooks — are value types with the promised semantics.
"""
from __future__ import annotations

import dataclasses

import pytest

from narranexus.contracts import API_VERSIONS
from narranexus.contracts.agent import (
    STAGE_HOOKS,
    STAGES,
    AgentSpec,
    Budgets,
    CapabilityFilter,
    CapabilityMeta,
    CapabilitySet,
    CapabilityTier,
    ModelIdentity,
    Persona,
    PipelineProfile,
    Stage,
    ToolSurface,
    TurnOverride,
    hook_name,
)
from narranexus.contracts.agent.capability import STAGE_METHODS, TIER_STAGES, StageParticipant
from narranexus.contracts.agent.pipeline import BUILTIN_PROFILE_IDS
from narranexus.kernel.plugins.hooks import HookRegistry, HookSpec


def test_seven_stages_in_fixed_order():
    assert [s.value for s in STAGES] == ["ingress", "recall", "compose", "assemble", "act", "commit", "reflect"]
    assert API_VERSIONS["agent"] == 0


def test_value_objects_are_frozen():
    meta = CapabilityMeta(name="x", tier=CapabilityTier.TOOL)
    with pytest.raises(dataclasses.FrozenInstanceError):
        meta.name = "y"  # type: ignore[misc]
    assert ToolSurface().expressive_tools == ()


def test_stage_methods_cover_exactly_the_participant_protocol():
    declared = {m for methods in STAGE_METHODS.values() for m in methods}
    protocol = set(StageParticipant.__protocol_attrs__)  # type: ignore[attr-defined]
    assert declared == protocol


def test_every_tier_is_expressible_through_stage_methods():
    assert set(TIER_STAGES) == set(CapabilityTier)
    for tier, stages in TIER_STAGES.items():
        assert stages, tier
        for stage in stages:
            assert stage in STAGE_METHODS, (tier, stage)
    assert TIER_STAGES[CapabilityTier.TOOL] == {Stage.ACT}
    assert TIER_STAGES[CapabilityTier.MEMORY_KIND] == {Stage.RECALL, Stage.COMMIT, Stage.REFLECT}
    assert Stage.COMPOSE not in TIER_STAGES[CapabilityTier.MODULE]


def test_capability_filter_and_set_semantics():
    f = CapabilityFilter(include=("chat", "jobs"), exclude=("jobs",))
    assert f.allows("chat") and not f.allows("jobs") and not f.allows("memory")
    assert CapabilityFilter().allows("anything")
    cs = CapabilitySet(enabled=("acme.crm",), disabled=("builtin.jobs",))
    assert cs.is_enabled("acme.crm", default=False)
    assert not cs.is_enabled("builtin.jobs", default=True)
    assert cs.is_enabled("builtin.chat", default=True)


def test_profile_override_merges_strategies_and_keeps_the_rest():
    base = PipelineProfile(id="default", strategies={Stage.RECALL: "narrative_llm"})
    fast = base.with_override(TurnOverride(strategies={Stage.RECALL: "narrative_fast"}, narrative_persistence="ephemeral"))
    assert fast.id == "default"
    assert fast.strategy_for(Stage.RECALL) == "narrative_fast"
    assert fast.strategy_for(Stage.ACT) == "default"
    assert fast.narrative_persistence == "ephemeral" and fast.budgets == base.budgets
    assert BUILTIN_PROFILE_IDS[0] == "default"


def test_agent_spec_is_one_value():
    spec = AgentSpec(
        agent_id="a1",
        owner_id="u1",
        persona=Persona(awareness="I help.", reply_language="zh"),
        capabilities=CapabilitySet(enabled=("builtin.chat",)),
        pipeline_profile="default",
        model=ModelIdentity(provider="netmind", model="deepseek", framework="nexus_power"),
    )
    assert spec.budgets == Budgets()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.pipeline_profile = "fast"  # type: ignore[misc]


def test_stage_hooks_are_fourteen_and_declarable_on_the_kernel_hook_registry():
    assert len(STAGE_HOOKS) == 14
    assert hook_name(Stage.COMMIT, did=True) == "onDidCommit"
    assert hook_name(Stage.ACT, did=False) == "onWillAct"
    reg = HookRegistry()
    for name, (params, firstresult) in STAGE_HOOKS.items():
        reg.declare(HookSpec(name, params=params, firstresult=firstresult))
    assert reg.caller("onWillRecall").spec.firstresult is True
    assert reg.caller("onDidRecall").spec.firstresult is False
