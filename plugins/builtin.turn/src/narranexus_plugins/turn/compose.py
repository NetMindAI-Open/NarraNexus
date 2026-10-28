"""
@file_name: compose.py
@author: Bin Liang
@date: 2026-09-04
@description: Compose stage default strategy: step_2 (module load + execution path decision) and step_2.5 (instance sync), cancellation-checked.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from narranexus.contracts.agent.stages import Stage
from narranexus.platform.turn.inputs import StageInputs


class DefaultCompose:
    stage = Stage.COMPOSE

    async def run(self, inputs: StageInputs) -> AsyncIterator[Any]:
        from narranexus.platform.agent_runtime import steps as ar

        ctx, s = inputs.ctx, inputs.services
        async for msg in ar.step_2_load_modules(ctx):
            yield msg
        ctx.cancellation.raise_if_cancelled()
        async for msg in ar.step_2_5_sync_instances(ctx, s.narrative_service, s.markdown_manager):
            yield msg
        ctx.cancellation.raise_if_cancelled()


__all__ = ["DefaultCompose"]
