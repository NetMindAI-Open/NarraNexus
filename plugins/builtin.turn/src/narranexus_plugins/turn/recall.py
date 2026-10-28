"""
@file_name: recall.py
@author: Bin Liang
@date: 2026-09-04
@description: Recall stage strategies: ``default`` (LLM narrative selection + history), ``narrative_fast`` (BM25 top-1, durable), ``ephemeral`` (BM25 top-1, no session writes).

``narrative_fast`` and ``ephemeral`` both run ``step_1_fast_select``; the
difference — whether a miss creates a narrative and writes the session —
is the profile's ``narrative_persistence``, which the step reads from
``ctx.turn_profile`` (the knob carrier).
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from narranexus.contracts.agent.stages import Stage
from narranexus.platform.turn.inputs import StageInputs


class NarrativeLlmRecall:
    stage = Stage.RECALL

    async def run(self, inputs: StageInputs) -> AsyncIterator[Any]:
        from narranexus.platform.agent_runtime import steps as ar

        ctx, s = inputs.ctx, inputs.services
        async for msg in ar.step_1_select_narrative(ctx, s.narrative_service, s.session_service):
            yield msg
        await ar.step_1_5_init_markdown(ctx, s.markdown_manager, read_history=True)


class NarrativeFastRecall:
    stage = Stage.RECALL

    async def run(self, inputs: StageInputs) -> AsyncIterator[Any]:
        from narranexus.platform.agent_runtime import steps as ar

        ctx, s = inputs.ctx, inputs.services
        async for msg in ar.step_1_fast_select(ctx, s.narrative_service, s.session_service):
            yield msg
        await ar.step_1_5_init_markdown(ctx, s.markdown_manager, read_history=False)


class EphemeralRecall(NarrativeFastRecall):
    """Voice: same BM25 selection; the ephemeral persistence comes from the profile's TurnProfile."""


__all__ = ["EphemeralRecall", "NarrativeFastRecall", "NarrativeLlmRecall"]
