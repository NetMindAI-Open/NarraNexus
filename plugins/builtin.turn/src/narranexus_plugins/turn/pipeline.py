"""
@file_name: pipeline.py
@author: Bin Liang
@date: 2026-09-07
@description: builtin.turn — ``TurnPipeline``: the one place the seven stages run, in order, through the strategies a profile names, with hooks at every boundary.

For each stage: ``onWill<Stage>`` (hooks may observe the frozen inputs;
returning a value is reserved for a later batch) → the strategy the profile
selects from the ``turn.pipeline.<stage>`` registry → ``onDid<Stage>`` with
the frozen output view. Stage messages stream through to the caller
unchanged. Hooks run with the profile's sync budget and never fail the turn.

This class lives in the PLUGIN, not the platform. It used to be
``narranexus.platform.turn.pipeline:TurnPipeline`` with the manifest pointing
at it — the only one of the 95 builtin ``provides`` refs that left its own
package, which meant ``turn.pipeline`` (the slot whose whole purpose is
"replace the turn runtime wholesale") was the one slot a third party could not
fill and a distribution could not really exclude. The platform reaches it
through the binding (``platform.turn.pipeline.turn_pipeline_for``); PROFILE
SELECTION stays platform-side because it is about the turn's facts, not about
any one pipeline.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Callable

from loguru import logger

from narranexus.contracts.agent.events import hook_name
from narranexus.contracts.agent.pipeline import PipelineProfile
from narranexus.contracts.agent.stages import STAGES, Stage
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES, Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.turn import observe
from narranexus.platform.turn.inputs import StageInputs, TurnServices
from narranexus.platform.turn.stages import slot_path

_VIEWS: dict[Stage, Callable[[Any], Any]] = {
    Stage.INGRESS: observe.ingress_view,
    Stage.RECALL: observe.recall_view,
    Stage.COMPOSE: observe.compose_view,
    Stage.ASSEMBLE: observe.assemble_view,
    Stage.ACT: observe.act_view,
    Stage.COMMIT: observe.commit_view,
    Stage.REFLECT: observe.reflect_view,
}


class TurnPipeline:
    """Runs one turn; ``registries`` defaults to the process registries.

    Declares nothing: the seven stage slots, their strategies and the profiles
    all come from this plugin's manifest at host boot. A hand-built
    ``Registries()`` in a test gets them the same way the hosts do, by calling
    ``load_builtins(regs, "backend")`` — the constructor used to declare the
    slots itself, which produced a second, ``kind``-less definition of the same
    seven paths and silently disabled their contract-version check.
    """

    def __init__(self, registries: Registries | None = None) -> None:
        self.registries = registries or KERNEL_REGISTRIES

    def strategy_for(self, stage: Stage, profile: PipelineProfile) -> Any:
        name = profile.strategy_for(stage)
        registry = self.registries.registry_for(slot_path(stage))
        return registry.get(name)  # UnknownEntry: a profile naming a strategy nobody provides fails loud

    async def _hook(self, stage: Stage, *, did: bool, ctx: Any, profile: PipelineProfile) -> None:
        name = hook_name(stage, did=did)
        hooks = self.registries.hooks
        if name not in hooks or not hooks.caller(name).owners():
            return
        view = _VIEWS[stage](ctx)
        payload = {"stage": stage.value, ("output" if did else "inputs"): view, "agent_id": ctx.agent_id, "run_id": getattr(ctx, "run_id", "")}
        try:
            await asyncio.wait_for(hooks.caller(name).call(**payload), timeout=profile.budgets.sync_hook_total_s)
        except asyncio.TimeoutError:
            logger.warning(f"[turn] {name} hooks exceeded {profile.budgets.sync_hook_total_s}s and were abandoned")
        except Exception as exc:  # noqa: BLE001 — hooks never fail the turn
            logger.warning(f"[turn] {name} hooks raised {exc!r}")

    async def run(self, ctx: Any, profile: PipelineProfile, services: TurnServices, *, silent: bool = False) -> AsyncIterator[Any]:
        t = services.timings
        t.setdefault("run_start", time.monotonic())
        t["setup_start"] = time.monotonic()
        event_bound = False

        def _bind_event_once() -> None:
            # The Event row is created inside a stage (Ingress); the scopes that
            # attribute cost and logs to it must be entered before the NEXT LLM
            # call, which may happen before the pipeline yields anything (Recall
            # and Compose run helper LLMs and yield nothing). So the check runs
            # at every stage boundary and every yield, not on the first message.
            nonlocal event_bound
            if event_bound or services.bind_event is None:
                return
            event = getattr(ctx, "event", None)
            if event is not None:
                services.bind_event(str(event.id))
                event_bound = True

        for stage in STAGES:
            if stage is Stage.ACT:
                t["loop_start"] = time.monotonic()
            await self._hook(stage, did=False, ctx=ctx, profile=profile)
            strategy = self.strategy_for(stage, profile)
            async for msg in strategy.run(StageInputs(ctx=ctx, services=services, profile=profile, silent=silent)):
                _bind_event_once()
                yield msg
            _bind_event_once()
            if stage is Stage.ACT:
                t["loop_end"] = time.monotonic()
            await self._hook(stage, did=True, ctx=ctx, profile=profile)
            if services.aborted:
                return


# §8: a one-arity slot is filled by a symbol named ``CONTRIBUTION``.
CONTRIBUTION = Contribution("builtin.turn", lambda: TurnPipeline)

__all__ = ["CONTRIBUTION", "TurnPipeline"]
