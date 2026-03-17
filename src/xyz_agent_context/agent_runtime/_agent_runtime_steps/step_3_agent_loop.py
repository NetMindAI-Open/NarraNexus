"""
@file_name: step_3_agent_loop.py
@author: NetMind.AI
@date: 2025-12-22
@description: Step 3 - Narrative Smart Agent Loop (CASE1: AGENT_LOOP)

Build context and run Agent Loop (implicit Module orchestration).
This is the processing path for complex tasks, requiring LLM implicit orchestration within the Agent Loop.
"""

from __future__ import annotations

import os
from typing import AsyncGenerator, Any, Union, TYPE_CHECKING

from loguru import logger

from xyz_agent_context.schema import (
    ProgressMessage,
    ProgressStatus,
    PathExecutionResult,
    ErrorMessage,
)
from xyz_agent_context.context_runtime import ContextRuntime
from xyz_agent_context.agent_framework import ClaudeAgentSDK
from xyz_agent_context.agent_runtime.execution_state import ExecutionState

if TYPE_CHECKING:
    from .context import RunContext


async def step_3_agent_loop(
    ctx: "RunContext",
    db_client,
    response_processor
) -> AsyncGenerator[Union[ProgressMessage, PathExecutionResult, Any], None]:
    """
    Step 3: Narrative Smart Agent Loop (CASE1: AGENT_LOOP)

    Executed as Step 3, contains the following sub-steps:
    - 3.1: Initialize ContextRuntime
    - 3.2: Run ContextRuntime (build Context)
    - 3.3: Extract messages and MCP URLs
    - 3.4: Run Agent Loop (ClaudeAgentSDK)
    - 3.5: Agent's final thinking for this round

    Args:
        ctx: Run context
        db_client: Database client
        response_processor: Response processor

    Yields:
        ProgressMessage: Step 3 progress messages
        AgentTextDelta: Agent text output deltas
        PathExecutionResult: Unified execution result (returned last)
    """
    # Local variables
    context = None
    messages = []
    state = None
    agent_loop_response = []
    substeps = []  # Step 3 substep list

    # ============================================================================= Step 3: Narrative Smart Agent Loop
    yield ProgressMessage(
        step="3",
        title="Execute Agent Loop",
        description="Build context and run Agent Loop (CASE1: implicit orchestration)",
        status=ProgressStatus.RUNNING,
        substeps=substeps
    )

    logger.info("🚀 Step 3: Narrative Smart Agent Loop (CASE1: AGENT_LOOP)")

    # ------------- 3.1: Initialize ContextRuntime -------------
    logger.info("  ⚙️  Step 3.1: Initializing ContextRuntime")
    context_runtime = ContextRuntime(ctx.agent_id, ctx.user_id, db_client)
    substeps.append("[3.1] ✓ ContextRuntime initialization complete")
    logger.success("  ✅ ContextRuntime initialized")

    yield ProgressMessage(
        step="3",
        title="Execute Agent Loop",
        description="[3.1] ContextRuntime initialization complete",
        status=ProgressStatus.RUNNING,
        substeps=substeps
    )

    # ------------- 3.2: Run ContextRuntime -------------
    logger.info("  🏃 Step 3.2: Running ContextRuntime")
    context = await context_runtime.run(
        ctx.narrative_list,
        ctx.active_instances,
        ctx.input_content,
        working_source=ctx.working_source,
        query_embedding=ctx.query_embedding,
        created_job_ids=ctx.created_job_ids,  # Jobs created this round, for context passing
        evermemos_memories=ctx.evermemos_memories,  # Phase 2: Pass EverMemOS cache
        trigger_extra_data=ctx.trigger_extra_data,  # Trigger 层附加数据（channel_tag 等）
    )
    substeps.append(
        f"[3.2] ✓ Context build complete: {len(context.messages)} messages, "
        f"{len(context.mcp_urls)} MCP servers"
    )
    logger.success("  ✅ ContextRuntime execution completed")

    yield ProgressMessage(
        step="3",
        title="Execute Agent Loop",
        description=f"[3.2] Context build complete: {len(context.messages)} messages",
        status=ProgressStatus.RUNNING,
        substeps=substeps
    )

    # ------------- 3.3: Extract messages and MCP URLs -------------
    logger.info("  📤 Step 3.3: Extracting messages and MCP URLs")
    messages = context.messages
    ctx.mcp_urls.update(context.mcp_urls)
    substeps.append(
        f"[3.3] ✓ Extraction complete: {len(messages)} messages, {len(ctx.mcp_urls)} MCP servers"
    )
    logger.info(f"    Messages count: {len(messages)}")
    logger.info(f"    MCP URLs: {list(ctx.mcp_urls.keys())}")
    logger.success("  ✅ Messages and MCP URLs extracted")

    yield ProgressMessage(
        step="3",
        title="Execute Agent Loop",
        description=f"[3.3] Extraction complete: {len(messages)} messages",
        status=ProgressStatus.RUNNING,
        substeps=substeps
    )

    # ------------- 3.4: Run Agent Loop -------------
    logger.info("  🤖 Step 3.4: Starting Agent Loop (ClaudeAgentSDK)")
    substeps.append("[3.4] ⏳ Agent Loop running...")

    yield ProgressMessage(
        step="3",
        title="Execute Agent Loop",
        description="[3.4] Agent Loop running...",
        status=ProgressStatus.RUNNING,
        substeps=substeps
    )

    state = ExecutionState()

    # Set up Agent working directory
    from xyz_agent_context.settings import settings
    working_path = settings.base_working_path
    agent_working_path = f"{working_path}/{ctx.agent_id}_{ctx.user_id}"
    if not os.path.exists(agent_working_path):
        os.makedirs(agent_working_path)

    # Extract skill-configured env vars from context for runtime injection
    skill_env_vars = {}
    if context.ctx_data and context.ctx_data.extra_data:
        skill_env_vars = context.ctx_data.extra_data.get("skill_env_vars", {})

    try:
        async for response in ClaudeAgentSDK(working_path=agent_working_path).agent_loop(
            messages=messages,
            mcp_server_urls=ctx.mcp_urls,
            extra_env=skill_env_vars or None,
        ):
            # Use ResponseProcessor to process responses
            result = response_processor.process(response, state)
            state = response_processor.apply_state_update(state, result)
            if result.message is not None:
                agent_loop_response.append(result.message)
                yield result.message
    except Exception as e:
        # Yield error to frontend so the user sees what went wrong
        # (instead of a cryptic "Agent decided no response needed")
        error_str = str(e)
        error_type = type(e).__name__
        logger.error(f"  ❌ Agent loop error ({error_type}): {error_str}")
        yield ErrorMessage(
            error_message=f"Agent execution error: {error_str}",
            error_type=error_type,
        )

    # After Agent Loop completes, record final output
    state = state.finalize()

    # Update 3.4 sub-step to completed status
    substeps[-1] = (
        f"[3.4] ✓ Agent Loop complete: {state.response_count} responses, "
        f"{len(state.final_output)} chars output"
    )
    logger.success(f"  ✅ Agent Loop completed: {state.response_count} responses received")
    logger.info(f"    Final output length: {len(state.final_output)} characters")

    # ------------- 3.5: Agent's final thinking for this round -------------
    final_output_preview = (
        state.final_output[:200] + "..."
        if len(state.final_output) > 200
        else state.final_output
    )
    substeps.append("[3.5] Agent's final thinking for this round")

    yield ProgressMessage(
        step="3.5",
        title="Agent's Final Thinking for This Round",
        description=final_output_preview,
        status=ProgressStatus.COMPLETED,
        details={
            "final_output": state.final_output,
            "output_length": len(state.final_output)
        }
    )

    # Step 3 complete
    yield ProgressMessage(
        step="3",
        title="Agent Loop Complete",
        description=f"✓ Complete: {state.response_count} responses, {len(state.final_output)} chars output",
        status=ProgressStatus.COMPLETED,
        details={
            "response_count": state.response_count,
            "output_length": len(state.final_output),
            "mcp_servers": list(ctx.mcp_urls.keys())
        },
        substeps=substeps
    )

    logger.success("✅ Step 3: Narrative Smart Agent Loop completed")

    # Return unified execution result
    yield PathExecutionResult(
        final_output=state.final_output,
        execution_steps=state.get_all_steps_as_list(),
        response_count=state.response_count,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        model=state.model,
        total_cost_usd=state.total_cost_usd,
        agent_loop_response=agent_loop_response,
        ctx_data=context.ctx_data,
    )
