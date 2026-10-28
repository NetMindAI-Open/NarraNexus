"""
@file_name: act.py
@author: Bin Liang
@date: 2026-09-04
@description: Act stage strategies: ``default`` (agent loop or direct trigger via step_3_execute_path with interrupt drain) and ``silent`` (empty result, no model call).

Both leave ``ctx.execution_result`` set; an interrupted loop is marked and
persisted with its partial steps (legacy behaviour, moved verbatim).
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from loguru import logger

from narranexus.contracts.agent.stages import Stage
from narranexus.platform.turn.inputs import StageInputs


def _empty_result(ctx: Any):
    from narranexus.platform.schema import PathExecutionResult
    from narranexus.platform.schema.context_schema import ContextData

    return PathExecutionResult(
        final_output="",
        execution_steps=[],
        response_count=0,
        agent_loop_response=[],
        ctx_data=ContextData(
            agent_id=ctx.agent_id,
            user_id=ctx.user_id,
            input_content=ctx.input_content,
            narrative_id=(ctx.main_narrative.id if ctx.main_narrative else None),
            working_source=ctx.working_source,
            extra_data=dict(ctx.trigger_extra_data or {}),
        ),
    )


def _mark_interrupted(ctx: Any) -> None:
    if ctx.execution_result is None:
        ctx.execution_result = _empty_result(ctx)
    ctx.execution_result.interrupted = True
    logger.info(f"Interrupted turn will persist with partial results ({len(ctx.execution_result.execution_steps)} steps)")


class AgentLoopAct:
    stage = Stage.ACT

    async def run(self, inputs: StageInputs) -> AsyncIterator[Any]:
        from narranexus.platform.agent_runtime import steps as ar

        ctx, s = inputs.ctx, inputs.services
        async for msg in ar.stream_with_interrupt_drain(ar.step_3_execute_path(ctx, s.db_client, s.response_processor), ctx.cancellation):
            yield msg
        if ctx.cancellation.is_cancelled:
            _mark_interrupted(ctx)


class SilentAct:
    stage = Stage.ACT

    async def run(self, inputs: StageInputs) -> AsyncIterator[Any]:
        ctx = inputs.ctx
        ctx.execution_result = _empty_result(ctx)
        logger.info("AgentRuntime.run(silent=True): skipped step_3, wrote empty PathExecutionResult; proceeding to persistence.")
        if ctx.cancellation.is_cancelled:
            _mark_interrupted(ctx)
        return
        yield  # pragma: no cover - makes this an async generator


__all__ = ["AgentLoopAct", "SilentAct"]
