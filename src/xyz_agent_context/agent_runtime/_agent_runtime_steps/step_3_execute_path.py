"""
@file_name: step_3_execute_path.py
@author: NetMind.AI
@date: 2025-12-22
@description: Step 3 - Execution path selection

Select execution path based on execution_type (AGENT_LOOP or DIRECT_TRIGGER).
"""

from __future__ import annotations

from typing import AsyncGenerator, Any, TYPE_CHECKING

from loguru import logger
from xyz_agent_context.utils.logging import timed

from xyz_agent_context.schema import ExecutionPath, PathExecutionResult

from .step_3_agent_loop import step_3_agent_loop
from .step_3_direct_trigger import step_3_direct_trigger

if TYPE_CHECKING:
    from .context import RunContext


@timed("step.3_execute_path")

async def step_3_execute_path(
    ctx: "RunContext",
    db_client,
    response_processor
) -> AsyncGenerator[Any, None]:
    """
    Step 3: Execution path selection

    Decide to execute AGENT_LOOP or DIRECT_TRIGGER based on execution_type.

    Args:
        ctx: Run context
        db_client: Database client
        response_processor: Response processor

    Yields:
        ProgressMessage: Progress messages
        AgentTextDelta: Agent text deltas (AGENT_LOOP only)
        PathExecutionResult: Execution result (handled internally, not yielded)
    """
    execution_type = ctx.execution_type

    if execution_type == ExecutionPath.AGENT_LOOP:
        # CASE1: Context organization + Agent Loop (implicit Module orchestration)
        async for msg in step_3_agent_loop(ctx, db_client, response_processor):
            if isinstance(msg, PathExecutionResult):
                # Unified execution result, save to context, not yielded externally
                ctx.execution_result = msg
            else:
                yield msg

    elif execution_type == ExecutionPath.DIRECT_TRIGGER:
        # CASE2: Directly call Trigger (skip Agent Loop)
        # Get configuration from load_result.direct_trigger
        import json
        direct_trigger = ctx.load_result.direct_trigger
        if direct_trigger is None:
            raise ValueError("DIRECT_TRIGGER mode but direct_trigger config is empty")

        module_class = direct_trigger.module_class
        trigger_name = direct_trigger.trigger_name
        try:
            args = json.loads(direct_trigger.params) if direct_trigger.params else {}
        except json.JSONDecodeError:
            args = {}

        execution_result = await step_3_direct_trigger(
            ctx.active_instances,
            module_class,
            trigger_name,
            args
        )
        ctx.execution_result = execution_result

    # Ensure execution_result is not empty
    assert ctx.execution_result is not None, "Execution result is not found"

    logger.info(f"Step 3 completed: execution_type={execution_type.value}")
