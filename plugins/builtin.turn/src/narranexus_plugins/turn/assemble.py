"""
@file_name: assemble.py
@author: Bin Liang
@date: 2026-09-04
@description: Assemble stage default strategy (``layered_prompt``): ContextRuntime builds prompt + tool surface, plus every ``contextProviders`` capability.

Only runs when the turn will act through the agent loop (direct-trigger
and silent turns build no prompt — legacy behaviour). Providers come from
the ``agent.capabilities.context_providers`` registry, filtered by the
profile's capability filter.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from loguru import logger

from narranexus.contracts.agent.capability import ContextProvider
from narranexus.contracts.agent.stages import Stage
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.platform.turn.inputs import StageInputs

PROVIDERS_SLOT = "agent.capabilities.context_providers"


def context_providers(profile: Any, registries: Any = None) -> tuple[Any, ...]:
    regs = registries or KERNEL_REGISTRIES
    if PROVIDERS_SLOT not in regs.slots:
        return ()
    out: list[Any] = []
    for entry in regs.registry_for(PROVIDERS_SLOT).entries():
        if not profile.capability_filter.allows(entry.name):
            continue
        try:
            provider = entry.factory()
        except Exception as exc:  # noqa: BLE001 — isolate the plugin
            logger.warning(f"[turn] context provider {entry.owner}:{entry.name} failed to build: {exc}")
            continue
        if isinstance(provider, ContextProvider) or hasattr(provider, "contribute_instructions") or hasattr(provider, "contribute_turn_context"):
            out.append(provider)
    return tuple(out)


class LayeredPromptAssemble:
    stage = Stage.ASSEMBLE

    async def run(self, inputs: StageInputs) -> AsyncIterator[Any]:
        from narranexus.platform.agent_runtime.steps import step_3_assemble_context
        from narranexus.platform.schema.decision_schema import ExecutionPath

        ctx, s = inputs.ctx, inputs.services
        if inputs.silent or ctx.execution_type != ExecutionPath.AGENT_LOOP:
            return
        async for msg in step_3_assemble_context(ctx, s.db_client, context_providers=context_providers(inputs.profile)):
            yield msg


__all__ = ["LayeredPromptAssemble", "PROVIDERS_SLOT", "context_providers"]
