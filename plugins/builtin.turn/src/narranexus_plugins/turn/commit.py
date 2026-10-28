"""
@file_name: commit.py
@author: Bin Liang
@date: 2026-09-04
@description: Commit stage default strategy: step_4 (trajectory, event, narrative stats) + modules' ``persist_turn`` + the [turn-timing] line.
"""
from __future__ import annotations

import time
from typing import Any, AsyncIterator

from loguru import logger

from narranexus.contracts.agent.stages import Stage
from narranexus.platform.turn.inputs import StageInputs


class DefaultCommit:
    stage = Stage.COMMIT

    async def run(self, inputs: StageInputs) -> AsyncIterator[Any]:
        from narranexus.platform.agent_runtime import steps as ar
        from narranexus.platform.agent_runtime.steps import build_after_execution_params

        ctx, s = inputs.ctx, inputs.services
        async for msg in ar.step_4_persist_results(
            ctx, s.event_service, s.narrative_service, s.markdown_manager, s.trajectory_recorder, s.session_service
        ):
            yield msg
        try:
            await s.hook_manager.persist_turn(ctx.module_list, build_after_execution_params(ctx))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"persist_turn phase failed (non-fatal): {e}")
        t = s.timings
        now = time.monotonic()
        logger.info(
            ar.turn_timing_line(
                agent_id=ctx.agent_id,
                event_id=str(ctx.event.id) if ctx.event else "-",
                source=str(ctx.working_source),
                pre_s=t.get("setup_start", now) - t.get("run_start", now),
                setup_s=t.get("loop_start", now) - t.get("setup_start", now),
                loop_s=t.get("loop_end", now) - t.get("loop_start", now),
                persist_s=now - t.get("loop_end", now),
                total_s=now - t.get("run_start", now),
                interrupted=bool(ctx.cancellation.is_cancelled),
                profile=(ctx.turn_profile.name if ctx.turn_profile else ""),
            )
        )
        ctx.cancellation.raise_if_cancelled()


__all__ = ["DefaultCommit"]
