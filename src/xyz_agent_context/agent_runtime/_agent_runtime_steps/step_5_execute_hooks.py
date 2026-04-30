"""
@file_name: step_5_execute_hooks.py
@author: NetMind.AI
@date: 2025-12-22
@description: Step 5 - Execute Hooks

Execute post-processing hooks for each module (memory storage, logging, etc.).

Refactoring notes (2025-12-24):
- Get instances from ctx.active_instances (i.e., load_result.active_instances)
- No longer depends on narrative.active_instances (JSON embedded data)
"""

from __future__ import annotations

from typing import AsyncGenerator, TYPE_CHECKING

from loguru import logger
from xyz_agent_context.utils.logging import timed

from xyz_agent_context.schema import ProgressMessage, ProgressStatus
from xyz_agent_context.schema import (
    WorkingSource,
    HookExecutionContext,
    HookIOData,
    HookExecutionTrace,
    HookAfterExecutionParams,
)

if TYPE_CHECKING:
    from .context import RunContext
    from xyz_agent_context.module import HookManager


@timed("step.5_execute_hooks")

async def step_5_execute_hooks(
    ctx: "RunContext",
    hook_manager: "HookManager"
) -> AsyncGenerator[ProgressMessage, None]:
    """
    Step 5: Execute Hooks

    Execute post-processing hooks for each module.

    Args:
        ctx: Run context
        hook_manager: Hook manager

    Yields:
        ProgressMessage: Progress messages

    Returns:
        callback_results: Callback results produced after hook execution
    """
    ctx.substeps_5 = []

    yield ProgressMessage(
        step="5",
        title="Execute Hooks",
        description="Execute post-processing hooks for each module (memory storage, logging, etc.)",
        status=ProgressStatus.RUNNING,
        substeps=ctx.substeps_5
    )

    execution_result = ctx.execution_result

    # Determine the currently executing instance (for state checking)
    # Refactored: get from ctx.active_instances (from load_result, not narrative JSON)
    current_instance = None
    current_narrative = ctx.main_narrative
    active_instances = ctx.active_instances  # From load_result.active_instances

    if active_instances:
        if ctx.working_source == WorkingSource.CHAT:
            # CHAT trigger: get the current user's ChatModule instance from ctx.user_chat_instances
            # 2026-01-21 P1-1: no longer using main_chat_instance_id, each user has an independent ChatModule instance
            user_chat_id = None
            if current_narrative and hasattr(ctx, 'user_chat_instances'):
                user_chat_id = ctx.user_chat_instances.get(current_narrative.id)

            if user_chat_id:
                for instance in active_instances:
                    if instance.instance_id == user_chat_id:
                        current_instance = instance
                        logger.info(f"  → Current instance: {instance.instance_id} (user_chat)")
                        break
            else:
                # Fallback: try to find the current user's ChatModule in active_instances
                for instance in active_instances:
                    if instance.module_class == "ChatModule":
                        current_instance = instance
                        logger.info(f"  → Current instance: {instance.instance_id} (chat_fallback)")
                        break
        elif ctx.working_source == WorkingSource.JOB:
            # JOB trigger: prefer ctx.job_instance_id (passed in by JobTrigger)
            # Fall back to ctx_data.extra_data (backward compatibility)
            job_instance_id = ctx.job_instance_id or (
                execution_result.ctx_data.extra_data.get("instance_id")
                if execution_result.ctx_data and execution_result.ctx_data.extra_data
                else None
            )
            if job_instance_id:
                for instance in active_instances:
                    if instance.instance_id == job_instance_id:
                        current_instance = instance
                        logger.info(f"  → Current instance: {instance.instance_id} (job)")
                        break

    if current_instance:
        status_value = (
            current_instance.status.value
            if hasattr(current_instance.status, 'value')
            else current_instance.status
        )
        logger.info(f"  ✓ Instance found: {current_instance.instance_id}, status={status_value}")
    else:
        logger.warning(f"No instance found for working_source={ctx.working_source}")

    # Build structured Hook parameters
    hook_params = HookAfterExecutionParams(
        execution_ctx=HookExecutionContext(
            event_id=ctx.event.id,
            agent_id=ctx.agent_id,
            user_id=ctx.user_id,
            working_source=ctx.working_source,
        ),
        io_data=HookIOData(
            input_content=ctx.input_content,
            final_output=execution_result.final_output,
        ),
        trace=HookExecutionTrace(
            event_log=ctx.event_log_entries,
            agent_loop_response=execution_result.agent_loop_response,
        ),
        ctx_data=execution_result.ctx_data,
        instance=current_instance,
        # Narrative-related (for MemoryModule writing to EverMemOS)
        event=ctx.event,
        narrative=current_narrative,
    )

    # Get information about hooks to be executed
    hooks_to_execute = []
    for module in ctx.module_list:
        if hasattr(module, 'hook_after_event_execution'):
            hooks_to_execute.append(module.config.name)
            ctx.substeps_5.append(f"[5.{len(hooks_to_execute)}] Preparing to execute: {module.config.name}")

    logger.info(f"  Hooks to execute: {hooks_to_execute}")

    callback_results = await hook_manager.hook_after_event_execution(
        ctx.module_list, hook_params
    )

    # Update substeps to completed status
    substeps_5_completed = []
    for i, hook_name in enumerate(hooks_to_execute):
        substeps_5_completed.append(f"[5.{i+1}] ✓ {hook_name} execution completed")

    logger.info("Hooking after event execution completed")

    yield ProgressMessage(
        step="5",
        title="Modules Begin Async Processing",
        description=f"✓ {len(hooks_to_execute)} hooks executed",
        status=ProgressStatus.COMPLETED,
        details={
            "hooks_executed": hooks_to_execute,
            "hook_count": len(hooks_to_execute),
            "callbacks_triggered": len(callback_results)
        },
        substeps=substeps_5_completed
    )

    # Return hook_callback_results for subsequent processing
    # Note: async generators cannot return values, so return via yield
    yield callback_results  # Named hook_callback_results, handled by the caller
