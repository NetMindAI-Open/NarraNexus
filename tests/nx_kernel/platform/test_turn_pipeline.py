"""
@file_name: test_turn_pipeline.py
@author: Bin Liang
@date: 2026-09-04
@description: The pipeline runs the seven stages in order through the strategies a profile names, fires onWill/onDid with frozen views, and resolves legacy flags to profiles.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from narranexus.contracts import UnknownEntry
from narranexus.contracts.agent.pipeline import PipelineProfile
from narranexus.contracts.agent.stages import STAGES, IngressContext, Stage
from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus_plugins.turn.pipeline import TurnPipeline
from narranexus.platform.turn import resolve_profile
from narranexus.platform.turn.inputs import TurnServices
from narranexus_plugins.turn.profiles import BUILTIN_PROFILES
from narranexus.platform.turn.stages import slot_path


@dataclass
class _Ctx:
    agent_id: str = "a1"
    user_id: str = "u1"
    input_content: str = "hi"
    working_source: str = "chat"
    trigger_extra_data: dict = field(default_factory=dict)
    job_instance_id: str | None = None
    forced_narrative_id: str | None = None
    narrative_list: list = field(default_factory=list)
    session: object | None = None
    markdown_history: str = ""
    no_durable_topic: bool = False
    module_list: list = field(default_factory=list)
    execution_result: object | None = None
    assembled: object | None = None
    event: object | None = None
    execution_type: object | None = None
    cancellation: object = field(default_factory=lambda: SimpleNamespace(is_cancelled=False, raise_if_cancelled=lambda: None))
    active_instances: list = field(default_factory=list)
    run_id: str = "run_x"


def _services() -> TurnServices:
    return TurnServices(None, None, None, None, None, None, None, None, execute_callback_instance=lambda *a, **k: None)


def _fake_strategy(stage: Stage, log: list, name: str = "default", *, abort: bool = False):
    class S:
        async def run(self, inputs):
            log.append((stage.value, name))
            if abort:
                inputs.services.aborted = True
            yield f"{stage.value}:{name}"

    S.stage = stage
    return Contribution(name, S)


def _registries_with(log: list, extra: dict[Stage, list[str]] | None = None, abort_at: Stage | None = None) -> Registries:
    # The seven stage slots come from builtin.turn's manifest, exactly the way
    # a host gets them (``TurnPipeline.__init__`` no longer declares a second,
    # kind-less copy — see turn/stages/__init__.py).
    regs = Registries()
    load_builtins(regs, "backend")
    for stage in STAGES:
        reg = regs.registry_for(slot_path(stage))
        reg.register_contribution(_fake_strategy(stage, log, abort=abort_at is stage), owner="test", replace=True)
        for name in (extra or {}).get(stage, []):
            reg.register_contribution(_fake_strategy(stage, log, name), owner="test", replace=True)
    return regs


def test_stages_run_in_order_with_default_strategies():
    log: list = []
    regs = _registries_with(log)
    out = asyncio.run(_collect(TurnPipeline(regs).run(_Ctx(), PipelineProfile(id="default"), _services())))
    assert [s for s, _ in log] == [s.value for s in STAGES]
    assert out == [f"{s.value}:default" for s in STAGES]


def test_profile_selects_named_strategies_and_unknown_fails_loud():
    log: list = []
    regs = _registries_with(log, extra={Stage.RECALL: ["graph"], Stage.ACT: ["silent"]})
    profile = PipelineProfile(id="research", strategies={Stage.RECALL: "graph", Stage.ACT: "silent"})
    asyncio.run(_collect(TurnPipeline(regs).run(_Ctx(), profile, _services())))
    assert ("recall", "graph") in log and ("act", "silent") in log
    with pytest.raises(UnknownEntry):
        asyncio.run(_collect(TurnPipeline(regs).run(_Ctx(), PipelineProfile(id="x", strategies={Stage.RECALL: "nope"}), _services())))


def test_abort_after_ingress_stops_the_pipeline():
    log: list = []
    regs = _registries_with(log, abort_at=Stage.INGRESS)
    out = asyncio.run(_collect(TurnPipeline(regs).run(_Ctx(), PipelineProfile(id="default"), _services())))
    assert out == ["ingress:default"] and [s for s, _ in log] == ["ingress"]


def test_hooks_fire_with_frozen_views_and_never_fail_the_turn():
    log: list = []
    regs = _registries_with(log)
    seen: list = []

    async def will_ingress(stage, inputs, agent_id, run_id):
        seen.append(("will", stage, type(inputs).__name__, agent_id, run_id))

    async def did_commit(stage, output):
        seen.append(("did", stage, type(output).__name__))
        raise RuntimeError("hook bug")

    regs.hooks.add("onWillIngress", will_ingress, owner="acme.audit")
    regs.hooks.add("onDidCommit", did_commit, owner="acme.audit")
    asyncio.run(_collect(TurnPipeline(regs).run(_Ctx(), PipelineProfile(id="default"), _services())))
    assert ("will", "ingress", IngressContext.__name__, "a1", "run_x") in seen
    assert ("did", "commit", "CommitContext") in seen


def test_resolve_profile_table():
    regs = Registries()
    load_builtins(regs, "backend")
    tp = SimpleNamespace(name="chat_fast", narrative_strategy="bm25_top1")
    voice = SimpleNamespace(name="voice_fast", narrative_strategy="bm25_top1")
    assert resolve_profile(fast_mode=False, silent=True, turn_profile=None, working_source="chat", registries=regs).id == "silent"
    assert resolve_profile(fast_mode=False, silent=False, turn_profile=voice, working_source="chat", registries=regs).id == "voice"
    assert resolve_profile(fast_mode=False, silent=False, turn_profile=tp, working_source="chat", registries=regs).id == "fast"
    assert resolve_profile(fast_mode=True, silent=False, turn_profile=None, working_source="lark", registries=regs).id == "fast"
    assert resolve_profile(fast_mode=False, silent=False, turn_profile=None, working_source="job", registries=regs).id == "job"
    assert resolve_profile(fast_mode=False, silent=False, turn_profile=None, working_source="chat", registries=regs).id == "default"
    assert BUILTIN_PROFILES["fast"].strategy_for(Stage.RECALL) == "narrative_fast"
    assert BUILTIN_PROFILES["voice"].strategy_for(Stage.RECALL) == "ephemeral"
    assert BUILTIN_PROFILES["silent"].strategy_for(Stage.ACT) == "silent"


def test_builtin_turn_manifest_loads_into_fresh_registries():
    from narranexus.kernel.plugins.builtins import builtin_manifests
    from narranexus.kernel.plugins.loader import load

    regs = Registries()
    load_builtins(regs, "backend")
    report = load(regs, [m for m in builtin_manifests() if m.id == "builtin.turn"], role="backend")
    assert not report.errors
    assert regs.registry_for("turn.pipeline.recall").names() == ("default", "narrative_fast", "ephemeral")
    assert set(regs.registry_for("turn.profiles").names()) == set(BUILTIN_PROFILES)
    assert regs.registry_for("turn.pipeline").names() == ("builtin.turn",)


async def _collect(agen):
    return [m async for m in agen]
