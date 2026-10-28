"""
@file_name: test_pipeline_event_binding.py
@author: Bin Liang
@date: 2026-09-07
@description: The pipeline binds the turn's Event to the runtime scopes the moment a stage creates it — before the next stage runs and regardless of whether anything was yielded — so Recall/Compose helper-LLM spend is booked to this turn, never to the ambient (parent) event. Reverting to "bind on first yielded message" turns this red.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from narranexus.contracts.agent.pipeline import PipelineProfile
from narranexus.contracts.agent.stages import STAGES, Stage
from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus_plugins.turn.pipeline import TurnPipeline
from narranexus.platform.turn.inputs import TurnServices
from narranexus.platform.turn.stages import slot_path

from tests.nx_kernel.platform.test_turn_pipeline import _Ctx


def _silent_strategy(stage: Stage, log: list, *, creates_event: bool = False):
    class S:
        async def run(self, inputs):
            if creates_event:
                inputs.ctx.event = SimpleNamespace(id="ev-42")
            log.append(("stage", stage.value))
            if False:  # a strategy that yields nothing — Recall/Compose shape
                yield None

    S.stage = stage
    return Contribution("default", S)


def _registries(log: list) -> Registries:
    # The stage slots come from builtin.turn's manifest, the way every host
    # gets them (the pipeline used to declare a kind-less second copy itself).
    regs = Registries()
    load_builtins(regs, "backend")
    for stage in STAGES:
        regs.registry_for(slot_path(stage)).register_contribution(
            _silent_strategy(stage, log, creates_event=stage is Stage.INGRESS), owner="test", replace=True
        )
    return regs


async def _drain(gen):
    async for _ in gen:
        pass


def test_event_is_bound_right_after_the_stage_that_created_it_even_with_zero_yields():
    log: list = []
    regs = _registries(log)
    services = TurnServices(None, None, None, None, None, None, None, None, execute_callback_instance=lambda *a, **k: None)
    services.bind_event = lambda event_id: log.append(("bound", event_id))
    asyncio.run(_drain(TurnPipeline(regs).run(_Ctx(), PipelineProfile(id="default"), services)))
    assert log[:3] == [("stage", "ingress"), ("bound", "ev-42"), ("stage", "recall")], log
    assert log.count(("bound", "ev-42")) == 1


def test_no_event_means_no_binding_call():
    log: list = []
    regs = Registries()
    load_builtins(regs, "backend")
    for stage in STAGES:
        regs.registry_for(slot_path(stage)).register_contribution(_silent_strategy(stage, log), owner="test", replace=True)
    services = TurnServices(None, None, None, None, None, None, None, None, execute_callback_instance=lambda *a, **k: None)
    services.bind_event = lambda event_id: log.append(("bound", event_id))
    asyncio.run(_drain(TurnPipeline(regs).run(_Ctx(), PipelineProfile(id="default"), services)))
    assert not [e for e in log if e[0] == "bound"]
