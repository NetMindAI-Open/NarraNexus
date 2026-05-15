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
from xyz_agent_context.utils.logging import timed

from xyz_agent_context.schema import (
    AgentTextDelta,
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


def _should_run_helper_llm_fallback(
    working_source: str,
    agent_loop_response: list,
    cancellation,
) -> tuple[bool, str]:
    """Decide whether the chat no-reply helper_llm fallback should run.

    Returns (should_run, skip_reason). skip_reason is informational —
    "" when should_run is True, otherwise a short tag suitable for log
    output and tests. Pulled out of the generator body so the four
    skip conditions can be exercised by pure unit tests; the generator
    just reads (should_run, reason).

    Skip semantics:
      - Out-of-scope trigger: only chat needs this fallback; message_bus
        deliberately stays quiet, job/lark have their own reply tooling.
      - Fatal error already on the response stream: agent loop did not
        complete cleanly. state.final_output is partial reasoning;
        asking helper_llm to summarise that produces a hallucinated
        "reply" based on a half-thought. Let chat_module's failed-turn
        path handle it instead.
      - Cancellation requested: user pressed stop. The whole point of
        the cancellation token is to stop work; firing the fallback
        anyway burns helper_llm tokens for a reply the user actively
        rejected.
      - Already replied: at least one send_message_to_user_directly
        tool call exists — nothing to recover.
    """
    if working_source != "chat":
        return False, "non_chat_trigger"

    for r in agent_loop_response:
        if isinstance(r, ErrorMessage) and getattr(r, "severity", "fatal") == "fatal":
            return False, "fatal_error_in_loop"

    if cancellation is not None and getattr(cancellation, "is_cancelled", False):
        return False, "cancellation_requested"

    for r in agent_loop_response:
        if not isinstance(r, ProgressMessage) or not r.details:
            continue
        tool_name = (
            (r.details.get("tool_name") or "")
            if isinstance(r.details, dict)
            else ""
        )
        if "send_message_to_user_directly" in tool_name:
            return False, "already_replied_via_tool"

    return True, ""


_FALLBACK_REPLY_INSTRUCTIONS = (
    "You are converting an agent's internal reasoning into a direct, "
    "user-facing reply. The agent finished its turn without invoking "
    "the formal `send_message_to_user_directly` tool, so its raw "
    "reasoning was never spoken to the user. Your job: read what the "
    "agent thought and produce the single message it should have sent.\n\n"
    "Rules:\n"
    "- Reply in the same language as the user's question.\n"
    "- Address the user directly, not the agent. Do NOT describe the "
    "agent in third person.\n"
    "- Do NOT mention tools, send_message_to_user_directly, the "
    "agent's reasoning, this fallback path, or any internal state.\n"
    "- Keep it natural, useful, and proportional to the user's question."
)


async def _generate_fallback_reply_stream(
    user_input: str,
    agent_reasoning: str,
    db,
    agent_id: str,
):
    """Stream a helper_llm reply that translates the agent's internal
    reasoning into a user-facing message. Yields str deltas.

    Wrapped in its own function for two reasons:
    1. keeps the helper_llm import + cost-context setup out of the main
       agent-loop generator body.
    2. lets us test the fallback prompt in isolation."""
    from xyz_agent_context.agent_framework.openai_agents_sdk import OpenAIAgentsSDK
    from xyz_agent_context.utils.cost_tracker import set_cost_context, clear_cost_context

    set_cost_context(agent_id, db)
    try:
        sdk = OpenAIAgentsSDK()
        user_input_for_helper = (
            f"User's question (literal):\n{user_input!r}\n\n"
            f"Agent's internal reasoning this turn (may include "
            f"meta-talk about tools — ignore that):\n{agent_reasoning}\n\n"
            "Write the single reply the agent should send to the user."
        )
        async for delta in sdk.llm_stream(
            instructions=_FALLBACK_REPLY_INSTRUCTIONS,
            user_input=user_input_for_helper,
        ):
            yield delta
    finally:
        clear_cost_context()


@timed("step.3_agent_loop")

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

    # ------------- 3.1: Initialize ContextRuntime -------------
    context_runtime = ContextRuntime(ctx.agent_id, ctx.user_id, db_client)
    substeps.append("[3.1] ✓ ContextRuntime initialization complete")
    logger.debug("ContextRuntime initialized")

    yield ProgressMessage(
        step="3",
        title="Execute Agent Loop",
        description="[3.1] ContextRuntime initialization complete",
        status=ProgressStatus.RUNNING,
        substeps=substeps
    )

    # ------------- 3.2: Run ContextRuntime -------------
    # Await EverMemOS episodes (launched in parallel at Step 0)
    relevant_episodes = await ctx.evermemos_task if hasattr(ctx, 'evermemos_task') and ctx.evermemos_task else []
    logger.info(f"  [EverMemOS-Search] Awaited: {len(relevant_episodes)} episodes ready for context")

    context = await context_runtime.run(
        ctx.narrative_list,
        ctx.active_instances,
        ctx.input_content,
        working_source=ctx.working_source,
        query_embedding=ctx.query_embedding,
        created_job_ids=ctx.created_job_ids,
        trigger_extra_data=ctx.trigger_extra_data,
        relevant_episodes=relevant_episodes,
    )
    substeps.append(
        f"[3.2] ✓ Context build complete: {len(context.messages)} messages, "
        f"{len(context.mcp_urls)} MCP servers"
    )
    logger.debug("ContextRuntime execution completed")

    yield ProgressMessage(
        step="3",
        title="Execute Agent Loop",
        description=f"[3.2] Context build complete: {len(context.messages)} messages",
        status=ProgressStatus.RUNNING,
        substeps=substeps
    )

    # ------------- 3.3: Extract messages and MCP URLs -------------
    messages = context.messages
    ctx.mcp_urls.update(context.mcp_urls)
    substeps.append(
        f"[3.3] ✓ Extraction complete: {len(messages)} messages, {len(ctx.mcp_urls)} MCP servers"
    )
    logger.debug(f"context.messages count={len(messages)}")
    logger.debug(f"context.mcp_urls={list(ctx.mcp_urls.keys())}")
    yield ProgressMessage(
        step="3",
        title="Execute Agent Loop",
        description=f"[3.3] Extraction complete: {len(messages)} messages",
        status=ProgressStatus.RUNNING,
        substeps=substeps
    )

    # ------------- 3.4: Run Agent Loop -------------
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
            cancellation=ctx.cancellation,
        ):
            # ResponseProcessor.process is a generator yielding 0..N
            # ProcessedResponse per raw event (Phase B 2026-05-13 —
            # thinking deltas get coalesced via _ThinkingBatcher, and a
            # non-thinking event may emit a buffered-thinking flush
            # FIRST plus the actual event SECOND).
            for result in response_processor.process(response, state):
                state = response_processor.apply_state_update(state, result)
                if result.message is not None:
                    agent_loop_response.append(result.message)
                    yield result.message
        # End-of-stream — flush any residual thinking buffer so the last
        # partial thinking segment is not silently dropped.
        for result in response_processor.flush_pending(state):
            state = response_processor.apply_state_update(state, result)
            if result.message is not None:
                agent_loop_response.append(result.message)
                yield result.message
    except Exception as e:
        # Before propagating the error to the frontend, drain any residual
        # thinking buffer so the user does not lose their last partial
        # thinking on an exception path. This is best-effort: errors here
        # are logged but never re-raise.
        try:
            for result in response_processor.flush_pending(state):
                state = response_processor.apply_state_update(state, result)
                if result.message is not None:
                    agent_loop_response.append(result.message)
                    yield result.message
        except Exception as flush_err:  # noqa: BLE001
            logger.warning(f"Failed to flush thinking buffer on error path: {flush_err}")

        # Yield error to frontend so the user sees what went wrong
        # (instead of a cryptic "Agent decided no response needed").
        # Also append the ErrorMessage to agent_loop_response so
        # downstream hooks (notably ChatModule.hook_after_event_execution)
        # can detect the failure and avoid persisting the turn as if it
        # had succeeded — see Bug 8.
        #
        # Severity is fatal: by definition we caught a framework-level
        # exception (TimeoutError, SDK crash, cancellation we couldn't
        # recover from). The agent cannot continue this turn. Recoverable
        # errors (transient rate-limit signals etc.) are yielded inside
        # response_processor without raising, and they get
        # severity="recoverable" so chat_module doesn't kill the turn.
        error_str = str(e)
        error_type = type(e).__name__
        logger.exception(f"[AGENT-LOOP-FATAL] {error_type}: {error_str}")
        error_msg = ErrorMessage(
            error_message=f"Agent execution error: {error_str}",
            error_type=error_type,
            severity="fatal",
        )
        agent_loop_response.append(error_msg)
        yield error_msg

    # Finalize state BEFORE inspecting it for the fallback — accessing
    # `state.final_output` on an unfinalized state is undefined behaviour
    # per ExecutionState's contract.
    state = state.finalize()

    # ------------- 3.4.X: No-reply fallback via helper_llm -------------
    # When a chat-triggered turn ends without send_message_to_user_directly,
    # the user gets a "(Agent decided no response needed)" placeholder.
    # Per the 5/11 product review: ask helper_llm to translate the
    # agent's reasoning into a user-facing reply, streamed through
    # AgentTextDelta so the frontend renders it like an organic reply.
    #
    # Scope: only `chat`. message_bus deliberately avoids replying
    # (prevents agent-to-agent loops); job/lark have their own reply
    # pathways.
    #
    # Skip conditions (each guards a distinct failure mode):
    #   • working_source != "chat" — out of scope.
    #   • fatal ErrorMessage in agent_loop_response — agent loop crashed
    #     mid-turn (CLI timeout / SDK exception); state.final_output is
    #     likely incomplete reasoning, feeding it to helper_llm would
    #     hallucinate a "reply" based on a half-thought. chat_module
    #     will write the user-row-only failed-turn record instead.
    #   • cancellation requested — user pressed stop; honouring it is
    #     the whole point of the cancellation token.
    #   • already sent — at least one send_message_to_user_directly
    #     tool call exists in the response stream.
    should_fallback, skip_reason = _should_run_helper_llm_fallback(
        working_source=ctx.working_source or "",
        agent_loop_response=agent_loop_response,
        cancellation=getattr(ctx, "cancellation", None),
    )
    if not should_fallback and skip_reason != "already_replied_via_tool":
        # already_replied is the silent-default case; the others are
        # noteworthy enough to log so ops can see why a chat turn
        # ended without firing the fallback.
        logger.info(f"[NO-REPLY-FALLBACK] skipped: {skip_reason}")
    if should_fallback:
        logger.warning(
            f"[NO-REPLY-FALLBACK] chat turn finished without "
            f"send_message_to_user_directly; invoking helper_llm to "
            f"generate a real reply (reasoning_chars={len(state.final_output)})"
        )
        # `fallback_chunks` is captured in the outer scope so the
        # `finally` block can synthesize a ProgressMessage from
        # whatever streamed in before helper_llm failed mid-stream.
        # Without this, a partial stream would leave the user
        # staring at half a reply with a "(decided not to respond)"
        # placeholder in DB — exactly the kind of state-mismatch we
        # are trying to eliminate.
        fallback_chunks: list[str] = []
        fallback_error: Exception | None = None
        try:
            async for delta_text in _generate_fallback_reply_stream(
                user_input=ctx.input_content,
                agent_reasoning=state.final_output,
                db=db_client,
                agent_id=ctx.agent_id,
            ):
                if (
                    getattr(ctx, "cancellation", None)
                    and getattr(ctx.cancellation, "is_cancelled", False)
                ):
                    logger.info(
                        "[NO-REPLY-FALLBACK] cancellation requested "
                        "mid-stream; aborting helper_llm fallback."
                    )
                    break
                fallback_chunks.append(delta_text)
                delta_msg = AgentTextDelta(delta=delta_text)
                agent_loop_response.append(delta_msg)
                yield delta_msg
        except Exception as e:
            fallback_error = e
            logger.exception(
                f"[NO-REPLY-FALLBACK] helper_llm stream failed mid-flight: {e}"
            )

        fallback_full = "".join(fallback_chunks).strip()
        if fallback_full:
            # Synthesize a send_message_to_user_directly tool call so
            # the downstream extractor + chat_module persists the reply
            # normally. The reply_via tag distinguishes organic vs.
            # recovered replies; the partial flag (only present when
            # the stream errored mid-way) lets observability tooling
            # spot partial recoveries that may need ops attention.
            synth_details = {
                "tool_name": "mcp__chat_module__send_message_to_user_directly",
                "arguments": {"content": fallback_full},
                "reply_via": "helper_llm_fallback",
            }
            if fallback_error is not None:
                synth_details["fallback_partial"] = True
                synth_details["fallback_error"] = type(fallback_error).__name__
            synthetic = ProgressMessage(
                step="3.4.fallback",
                title="Reply (helper_llm fallback)",
                description=(
                    "Agent did not call send_message_to_user_directly; "
                    "helper_llm generated a reply"
                    + (" (partial — stream errored)" if fallback_error else ".")
                ),
                status=ProgressStatus.COMPLETED,
                details=synth_details,
            )
            agent_loop_response.append(synthetic)
            yield synthetic
            logger.warning(
                f"[NO-REPLY-FALLBACK] persisted reply "
                f"(len={len(fallback_full)} chars, "
                f"partial={fallback_error is not None})"
            )
        else:
            logger.warning(
                f"[NO-REPLY-FALLBACK] no content recovered "
                f"(error={fallback_error!r}); placeholder will "
                f"be persisted by chat_module."
            )

    # Update 3.4 sub-step to completed status
    substeps[-1] = (
        f"[3.4] ✓ Agent Loop complete: {state.response_count} responses, "
        f"{len(state.final_output)} chars output"
    )
    logger.info(f"Agent Loop completed: {state.response_count} responses received")
    logger.debug(f"agent_loop.final_output_chars={len(state.final_output)}")

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
