"""
@file_name: step_3_agent_loop.py
@author: NetMind.AI
@date: 2025-12-22
@description: Step 3 - Narrative Smart Agent Loop (CASE1: AGENT_LOOP)

Build context and run Agent Loop (implicit Module orchestration).
This is the processing path for complex tasks, requiring LLM implicit orchestration within the Agent Loop.
"""

from __future__ import annotations

import json
import os
from typing import AsyncGenerator, Any, Union, TYPE_CHECKING

from loguru import logger
from xyz_agent_context.utils.logging import timed

from xyz_agent_context.schema import (
    AgentTextDelta,
    AgentThinking,
    ProgressMessage,
    ProgressStatus,
    PathExecutionResult,
    ErrorMessage,
    AUTH_EXPIRED_ERROR_TYPE,
)
from xyz_agent_context.context_runtime import ContextRuntime
from xyz_agent_context.agent_framework import get_agent_loop_driver
from xyz_agent_context.agent_runtime.execution_state import ExecutionState

if TYPE_CHECKING:
    from .context import RunContext


# Default size caps for the fallback-prompt serializer. Tuned for
# helper_llm context budget — 32 KB total leaves room for the system
# prompt block + chat history. 4 KB per entry stops a single oversized
# tool result from dominating.
_DEFAULT_MAX_PER_ENTRY = 4096
_DEFAULT_MAX_TOTAL = 32768
_DROPPED_PREFIX_MARKER = "[... earlier activity omitted to fit context budget ...]\n"
_EMPTY_RESPONSE_SENTINEL = "(no activity recorded)"


async def _resolve_agent_framework_name(user_id: str, db_client: Any) -> str:
    """Return the user's coding-agent framework name (for the driver
    registry). Reads ``user_slots[user_id, slot_name='agent'].agent_framework``.

    Always falls back to ``"claude_code"`` on:
      - row missing (new users)
      - column null (rows from before Task 1 migration)
      - DB lookup error (defensive — never let an ``agent_framework``
        column issue block an agent run)

    Unknown framework names are NOT silently rewritten here — they're
    handed to ``get_agent_loop_driver`` which raises ``ValueError`` so a
    typo in config surfaces at the dispatch site instead of masquerading
    as "claude". The driver registry has both ``claude_code`` and
    ``codex_cli`` aliases registered, so the two values
    ``user_slots.agent_framework`` ever holds resolve cleanly.
    """
    try:
        row = await db_client.get_one(
            "user_slots", {"user_id": user_id, "slot_name": "agent"}
        )
    except Exception as e:  # noqa: BLE001 — defensive: any DB hiccup
        logger.warning(
            f"[step_3] agent_framework lookup failed for user={user_id}: {e}; "
            f"falling back to claude_code"
        )
        return "claude_code"

    return (row or {}).get("agent_framework") or "claude_code"


def _truncate(text: str, limit: int) -> str:
    """Tail-truncate ``text`` to ``limit`` bytes, appending a clear
    marker so the LLM knows content was dropped."""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    return text[:limit] + f"\n[truncated {dropped} bytes]"


def _render_entry(msg: Any, max_per_entry: int) -> str | None:
    """Render one runtime frame as a single labelled string, or return
    ``None`` if the frame carries nothing worth showing the fallback
    LLM (e.g. structural progress messages with no tool/result payload).
    """
    if isinstance(msg, AgentTextDelta):
        return f"[assistant_text] {msg.delta}"
    if isinstance(msg, AgentThinking):
        return _truncate(
            f"[thinking] {msg.thinking_content}", max_per_entry
        )
    if isinstance(msg, ErrorMessage):
        body = f"[error] {msg.error_type}: {msg.error_message}"
        if msg.severity != "fatal":
            body += f" (severity={msg.severity})"
        return _truncate(body, max_per_entry)
    if isinstance(msg, ProgressMessage):
        details = msg.details or {}
        tool_name = details.get("tool_name")
        if tool_name and msg.status == ProgressStatus.RUNNING:
            args = details.get("arguments", {})
            try:
                args_json = json.dumps(args, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                args_json = repr(args)
            return _truncate(
                f"[tool_call] {tool_name}({args_json})", max_per_entry
            )
        if "output" in details and msg.status == ProgressStatus.COMPLETED:
            return _truncate(
                f"[tool_output] {details.get('output', '')}", max_per_entry
            )
    return None


def _serialize_agent_loop_for_prompt(
    agent_loop_response: list,
    *,
    max_per_entry: int = _DEFAULT_MAX_PER_ENTRY,
    max_total: int = _DEFAULT_MAX_TOTAL,
) -> str:
    """Render an ``agent_loop_response`` list into a flat plain-text
    block for the fallback LLM prompt.

    Why this exists separately from the streaming/persistence paths:
    the fallback LLM needs a compact, ordered snapshot of "what
    happened this turn so far" so it can write a recovery reply that
    references the work the agent actually completed. The live stream
    is too noisy (every text delta is its own frame); the persisted
    form (chat_module) is too lossy (only the final assistant message
    survives).

    Contract:
      - Frames render in their original order (causal sequence matters).
      - Each entry is capped at ``max_per_entry`` bytes; truncation
        gets a ``[truncated N bytes]`` marker.
      - Total output is capped at ``max_total`` bytes; if exceeded,
        oldest entries drop FIRST (with a ``[... earlier activity
        omitted ...]`` marker prepended) — recent activity is what the
        recovery reply needs.
      - Adjacent ``AgentTextDelta`` frames are concatenated into one
        ``[assistant_text]`` block (matching how the frontend renders
        them) so the LLM sees coherent text instead of a delta soup.
      - Frames with no useful payload (structural ProgressMessages
        with neither tool_name nor output) are silently dropped.
    """
    if not agent_loop_response:
        return _EMPTY_RESPONSE_SENTINEL

    # Pass 1: coalesce adjacent AgentTextDelta into single entries so
    # one delta stream renders as one [assistant_text] block.
    coalesced: list[Any] = []
    buffer: list[str] = []
    for msg in agent_loop_response:
        if isinstance(msg, AgentTextDelta):
            buffer.append(msg.delta)
            continue
        if buffer:
            coalesced.append(AgentTextDelta(delta="".join(buffer)))
            buffer = []
        coalesced.append(msg)
    if buffer:
        coalesced.append(AgentTextDelta(delta="".join(buffer)))

    # Pass 2: render each entry (or drop if nothing meaningful).
    rendered: list[str] = []
    for msg in coalesced:
        line = _render_entry(msg, max_per_entry)
        if line is not None:
            rendered.append(line)

    if not rendered:
        return _EMPTY_RESPONSE_SENTINEL

    # Pass 3: enforce total cap by dropping oldest entries first.
    # Compute total length including newline separators.
    def _join(entries: list[str]) -> str:
        return "\n".join(entries)

    dropped_any = False
    while rendered and len(_join(rendered)) > max_total:
        rendered.pop(0)
        dropped_any = True

    body = _join(rendered) if rendered else ""
    if dropped_any:
        body = _DROPPED_PREFIX_MARKER + body
    return body


def _should_run_helper_llm_fallback(
    working_source: str,
    agent_loop_response: list,
    cancellation,
) -> tuple[str | None, str]:
    """Decide what the chat fallback path should do this turn.

    Returns ``(mode, skip_reason)``:

    - ``("no_reply", "")``: chat turn finished cleanly without
      ``send_message_to_user_directly`` — run helper_llm to write the
      reply the agent forgot to send. No error to surface.
    - ``("after_error", "")``: chat turn hit a fatal mid-stream AND no
      organic reply was sent yet — run helper_llm with full context
      (system prompts + completed tool results + error info) so the
      recovered reply tells the user what was achieved, what failed,
      and a useful next step. Surface the original error as
      severity=``recovered`` after the recovery stream completes.
    - ``("partial_reply_then_error", "")``: chat turn hit a fatal AFTER
      the agent already sent a real reply — do NOT invoke helper_llm
      (the user already heard from the agent), but surface the
      truncated execution via severity=``recovered_after_reply`` so
      the badge tells the user the turn didn't finish all planned work.
    - ``(None, reason)``: nothing to do. Reasons:
        * ``"non_chat_trigger"``: out of scope — message_bus
          deliberately stays quiet, job/lark have their own reply
          tooling.
        * ``"cancellation_requested"``: user pressed stop; honour it.
          Don't burn helper_llm tokens recovering from rejected work.
        * ``"already_replied_via_tool"``: agent did its job — clean
          loop + organic reply, nothing to recover.

    Pulled out of the generator body so each case is exercisable by
    pure unit tests without spinning up the full async generator.
    """
    if working_source != "chat":
        return None, "non_chat_trigger"

    if cancellation is not None and getattr(cancellation, "is_cancelled", False):
        return None, "cancellation_requested"

    has_fatal = any(
        isinstance(r, ErrorMessage) and getattr(r, "severity", "fatal") == "fatal"
        for r in agent_loop_response
    )
    has_reply = False
    for r in agent_loop_response:
        if not isinstance(r, ProgressMessage) or not r.details:
            continue
        tool_name = (
            (r.details.get("tool_name") or "")
            if isinstance(r.details, dict)
            else ""
        )
        if "send_message_to_user_directly" in tool_name:
            has_reply = True
            break

    if has_fatal and has_reply:
        return "partial_reply_then_error", ""
    if has_fatal:
        return "after_error", ""
    if has_reply:
        return None, "already_replied_via_tool"
    return "no_reply", ""


_FALLBACK_NO_REPLY_INSTRUCTIONS = (
    "You are the agent's voice. The agent finished thinking but didn't "
    "call send_message_to_user_directly, so its reasoning was never "
    "spoken to the user. Produce the single message it should have sent."
    "\n\nRules:\n"
    "- Reply in the user's language (match `<current_user_message>`).\n"
    "- Address the user directly, in first person as the agent.\n"
    "- Do NOT mention tools, send_message_to_user_directly, helper_llm, "
    "this fallback path, or any internal state.\n"
    "- Keep it natural, useful, and proportional to the question."
)


_FALLBACK_AFTER_ERROR_INSTRUCTIONS = (
    "You are the agent continuing the same turn. The agent was working "
    "on the user's request, completed some steps successfully, then a "
    "step failed and the turn cannot finish as planned. Your job: tell "
    "the user what was achieved, what couldn't be done, and a useful "
    "next step they can try."
    "\n\nRules:\n"
    "- Reply in the user's language (match `<current_user_message>`).\n"
    "- Speak in first person, as the agent. Never break character with "
    "phrases like \"the system failed\" or \"an error occurred "
    "internally\". Phrase the failure operationally: \"I tried to X "
    "but couldn't reach Y\" / \"I got partway through Z\".\n"
    "- Use the `<this_turn_activity>` to be concrete about what you "
    "found in the steps that did succeed.\n"
    "- Suggest a concrete next step: rephrasing, splitting the request, "
    "or noting a temporary limitation if the error is clearly transient "
    "(rate limit / timeout).\n"
    "- Do NOT mention tool names, raw error type strings, helper_llm, "
    "or fallback paths. Translate technical errors into operational "
    "language.\n"
    "- Keep it short — one paragraph plus optional next-step bullet."
)


def _build_helper_user_input(
    *,
    mode: str,
    context_messages: list[dict],
    agent_loop_response: list,
    final_output: str,
    user_input: str,
    error_info: dict | None,
) -> str:
    """Construct the user-input payload fed to the helper_llm for the
    fallback reply.

    Strategy: don't replay context_messages verbatim into helper_llm —
    re-instantiating the agent persona + every tool instruction risks
    helper_llm trying to "tool-call" via text. Instead extract the
    system prompts as background, render history as a transcript, and
    render this-turn-so-far via the dedicated serializer.
    """
    sections: list[str] = []

    system_blocks = [
        str(m.get("content", "")).strip()
        for m in context_messages
        if isinstance(m, dict) and m.get("role") == "system"
    ]
    system_blocks = [s for s in system_blocks if s]
    if system_blocks:
        sections.append(
            "<original_system_instructions>\n"
            + "\n\n".join(system_blocks)
            + "\n</original_system_instructions>"
        )

    history_msgs = [
        m for m in context_messages
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ]
    # Drop the trailing user message if it duplicates `user_input` (the
    # current turn's user input is shown verbatim in its own section).
    if (
        history_msgs
        and history_msgs[-1].get("role") == "user"
        and str(history_msgs[-1].get("content", "")) == user_input
    ):
        history_msgs = history_msgs[:-1]
    if history_msgs:
        rendered = "\n".join(
            f"[{m['role']}] {str(m.get('content', '')).strip()}"
            for m in history_msgs
        )
        sections.append(
            "<conversation_history>\n"
            + rendered
            + "\n</conversation_history>"
        )

    sections.append(
        "<current_user_message>\n"
        + user_input
        + "\n</current_user_message>"
    )

    this_turn = _serialize_agent_loop_for_prompt(agent_loop_response)
    sections.append(
        "<this_turn_activity>\n"
        + this_turn
        + "\n</this_turn_activity>"
    )

    if final_output and mode == "no_reply":
        # In no_reply mode the agent's final reasoning IS the seed for
        # the missing reply. In after_error mode the reasoning may be a
        # half-thought (loop crashed mid-stream); rely on tool results
        # in <this_turn_activity> instead.
        sections.append(
            "<agent_final_reasoning>\n"
            + final_output
            + "\n</agent_final_reasoning>"
        )

    if mode == "after_error" and error_info:
        sections.append(
            "<execution_error>\n"
            f"The turn was interrupted by:\n"
            f"  type: {error_info.get('error_type', 'unknown')}\n"
            f"  message: {error_info.get('error_message', '')}\n"
            "</execution_error>"
        )

    if mode == "after_error":
        sections.append(
            "Write a single reply to the user that names what was "
            "achieved (concrete details from <this_turn_activity>), "
            "what couldn't be done (translating <execution_error> into "
            "operational language), and a useful next step."
        )
    else:
        sections.append(
            "Write the single reply the agent should send to the user."
        )

    return "\n\n".join(sections)


async def _generate_fallback_reply_stream(
    *,
    mode: str,
    context_messages: list[dict],
    agent_loop_response: list,
    final_output: str,
    user_input: str,
    error_info: dict | None,
    db,
    agent_id: str,
):
    """Stream a helper_llm reply for the recovery slot. Yields str
    deltas.

    Modes:
      - ``"no_reply"``: agent finished cleanly but never called
        send_message_to_user_directly; produce the reply it forgot to
        send.
      - ``"after_error"``: agent loop crashed mid-stream; produce a
        recovery reply telling the user what was achieved, what
        failed, and a useful next step.

    Wrapped in its own function for two reasons:
    1. Keeps the helper_llm import + cost-context setup out of the main
       agent-loop generator body.
    2. Lets us test the prompt assembly + streaming wiring in isolation.
    """
    from xyz_agent_context.agent_framework.helper_sdk import get_helper_sdk
    from xyz_agent_context.utils.cost_tracker import set_cost_context, clear_cost_context

    set_cost_context(agent_id, db)
    try:
        sdk = get_helper_sdk()
        instructions = (
            _FALLBACK_AFTER_ERROR_INSTRUCTIONS
            if mode == "after_error"
            else _FALLBACK_NO_REPLY_INSTRUCTIONS
        )
        user_input_for_helper = _build_helper_user_input(
            mode=mode,
            context_messages=context_messages,
            agent_loop_response=agent_loop_response,
            final_output=final_output,
            user_input=user_input,
            error_info=error_info,
        )
        async for delta in sdk.llm_stream(
            instructions=instructions,
            user_input=user_input_for_helper,
        ):
            yield delta
    finally:
        clear_cost_context()


async def _stream_fallback_recovery(
    *,
    fallback_mode: str | None,
    captured_error: dict | None,
    context_messages: list[dict],
    agent_loop_response: list,
    final_output: str,
    user_input: str,
    cancellation,
    db,
    agent_id: str,
):
    """Drive the post-agent-loop recovery phase, yielding the messages
    the frontend should see in causal order.

    Yields (when applicable, strictly in this order):
      1. ``AgentTextDelta`` frames from the helper_llm stream.
      2. A synthetic ``ProgressMessage`` (one) tagging the fallback as
         a ``send_message_to_user_directly`` call so downstream
         persistence (chat_module) records it as a normal turn. Carries
         ``details.reply_via=helper_llm_{mode}``.
      3. An ``ErrorMessage`` (one) if ``captured_error`` was set,
         with severity computed from outcome:
           - ``recovered`` — fallback produced non-empty content;
           - ``recovered_after_reply`` — partial_reply_then_error mode
             (helper_llm did not run, agent already spoke);
           - ``fatal`` — fallback produced nothing and we have no
             organic reply either.

    Why the ErrorMessage comes LAST: the frontend reduces
    ``responseParts`` from synthetic tool calls and falls back to
    ``currentErrors`` only when no responseParts exist. If we yielded
    ErrorMessage first, ``displayContent`` would briefly flip to the
    error string before the synthetic send_message lands — half a
    second of "system broke" UX even when we recovered cleanly.

    The caller is responsible for appending each yielded message to
    its own ``agent_loop_response`` (existing convention so downstream
    hooks see the full turn).
    """
    fallback_full = ""

    if fallback_mode in ("no_reply", "after_error"):
        chunks: list[str] = []
        fallback_error: Exception | None = None
        try:
            async for delta_text in _generate_fallback_reply_stream(
                mode=fallback_mode,
                context_messages=context_messages,
                agent_loop_response=agent_loop_response,
                final_output=final_output,
                user_input=user_input,
                error_info=captured_error,
                db=db,
                agent_id=agent_id,
            ):
                if (
                    cancellation is not None
                    and getattr(cancellation, "is_cancelled", False)
                ):
                    logger.info(
                        "[FALLBACK] cancellation requested mid-stream; "
                        f"aborting helper_llm ({fallback_mode})."
                    )
                    break
                chunks.append(delta_text)
                yield AgentTextDelta(delta=delta_text)
        except Exception as e:  # noqa: BLE001
            fallback_error = e
            logger.exception(
                f"[FALLBACK] helper_llm ({fallback_mode}) stream failed: {e}"
            )

        fallback_full = "".join(chunks).strip()
        if fallback_full:
            synth_details: dict = {
                "tool_name": "mcp__chat_module__send_message_to_user_directly",
                "arguments": {"content": fallback_full},
                "reply_via": f"helper_llm_{fallback_mode}",
            }
            if fallback_error is not None:
                synth_details["fallback_partial"] = True
                synth_details["fallback_error"] = type(fallback_error).__name__
            yield ProgressMessage(
                step="3.4.fallback",
                title=f"Reply (helper_llm {fallback_mode})",
                description=(
                    f"helper_llm generated a reply via {fallback_mode}"
                    + (" (partial — stream errored)" if fallback_error else ".")
                ),
                status=ProgressStatus.COMPLETED,
                details=synth_details,
            )
            logger.warning(
                f"[FALLBACK] persisted reply mode={fallback_mode} "
                f"(len={len(fallback_full)} chars, "
                f"partial={fallback_error is not None})"
            )
        else:
            logger.warning(
                f"[FALLBACK] no content recovered mode={fallback_mode} "
                f"(error={fallback_error!r})"
            )

    if captured_error is not None:
        if fallback_mode == "partial_reply_then_error":
            severity = "recovered_after_reply"
        elif fallback_full:
            severity = "recovered"
        else:
            severity = "fatal"
        yield ErrorMessage(
            error_message=(
                f"Agent execution error: {captured_error.get('error_message', '')}"
            ),
            error_type=captured_error.get("error_type", "Exception"),
            severity=severity,
        )


async def _record_oom_if_killed(
    db_client: Any, user_id: str, error_str: str, output_already_emitted: bool
) -> None:
    """Record an audit row when the agent loop died from an executor OOM
    (subprocess SIGKILL — ``exit code -9``), for monitoring/alerting.

    Best-effort: never raises. A retry/recovery path is intentionally NOT
    added here yet — the streaming loop must first be made cleanly
    re-runnable (tracked separately in the scheduling-resource plan); today
    an OOM falls through to the existing fallback. We only need visibility so
    the platform can alert and the affected user's memory cap can be tuned.
    """
    if "exit code -9" not in error_str:
        return
    try:
        from xyz_agent_context.repository.executor_audit_repository import (
            ExecutorAuditRepository,
        )

        await ExecutorAuditRepository(db_client).record(
            event_type="oom_killed",
            user_id=user_id,
            detail={
                "error_message": error_str[:500],
                "output_already_emitted": output_already_emitted,
            },
        )
    except Exception as audit_err:  # noqa: BLE001
        logger.warning(f"[oom-audit] failed to record OOM event: {audit_err}")


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
    context = await context_runtime.run(
        ctx.narrative_list,
        ctx.active_instances,
        ctx.input_content,
        working_source=ctx.working_source,
        created_job_ids=ctx.created_job_ids,
        trigger_extra_data=ctx.trigger_extra_data,
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
    from xyz_agent_context.utils.workspace_paths import agent_workspace_path
    agent_working_path = str(agent_workspace_path(ctx.agent_id, ctx.user_id, base=working_path))
    if not os.path.exists(agent_working_path):
        os.makedirs(agent_working_path)

    # Extract skill-configured env vars from context for runtime injection
    skill_env_vars = {}
    if context.ctx_data and context.ctx_data.extra_data:
        skill_env_vars = context.ctx_data.extra_data.get("skill_env_vars", {})

    # `captured_error` defers the ErrorMessage yield until AFTER the
    # recovery phase, so frontend renders the recovered reply FIRST and
    # the warning badge SECOND. Yielding ErrorMessage immediately on
    # except would flip displayContent to the error string for the
    # split second before the synthetic send_message lands.
    captured_error: dict | None = None
    # Select the agent-loop framework via the registry (iron rule #9).
    # Read the user's per-agent choice from user_slots; fall back to
    # claude_code on missing row / DB hiccup. Pass the resolved name
    # into the registry — driver factories are registered under both
    # the canonical user-facing names (claude_code / codex_cli) and
    # short aliases (claude / codex), so any value we read here that
    # the system supports will resolve.
    framework_name = await _resolve_agent_framework_name(ctx.user_id, db_client)
    logger.info(f"[step_3] agent_loop framework: {framework_name!r} (user={ctx.user_id})")
    # Per-user executor routing (cloud): ask the broker to ensure this
    # user's Executor container and use its URL. Returns None when no
    # broker is configured (local/desktop, or static AGENT_EXECUTOR_URL),
    # so get_agent_loop_driver falls back. This is the cold-start point.
    from xyz_agent_context.agent_framework.broker_client import (
        ensure_executor,
        wait_until_ready,
    )
    ensured = await ensure_executor(ctx.user_id)
    executor_url = ensured.url if ensured else None
    if ensured is not None and ensured.cold_started:
        # The user's executor was asleep and is being woken — emit a
        # semantic marker so the frontend can show the "waking up" overlay.
        # English text only (iron rule #1); the localized friendly copy
        # lives in the frontend, keyed on step="executor.warming".
        yield ProgressMessage(
            step="executor.warming",
            title="Waking up your agent",
            description="Your agent was idle; starting it up…",
            status=ProgressStatus.RUNNING,
        )
        # The container is started but uvicorn on :8020 takes a few seconds to
        # come up. Wait for it to be ready BEFORE driving the loop — otherwise
        # the first connection races the cold start, fails, and the run drops
        # into the fallback path instead of actually running the agent.
        await wait_until_ready(executor_url)
    driver = get_agent_loop_driver(
        framework=framework_name,
        executor_url=executor_url,
        working_path=agent_working_path,
    )
    # Clear the "waking up" overlay the instant the (now-awake) executor
    # emits its first event — the COMPLETED that pairs the RUNNING above.
    _warming_active = ensured is not None and ensured.cold_started
    try:
        async for response in driver.agent_loop(
            messages=messages,
            mcp_server_urls=ctx.mcp_urls,
            extra_env=skill_env_vars or None,
            cancellation=ctx.cancellation,
        ):
            if _warming_active:
                _warming_active = False
                yield ProgressMessage(
                    step="executor.warming",
                    title="Agent ready",
                    description="Your agent is awake.",
                    status=ProgressStatus.COMPLETED,
                )
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
        # Before deferring the error, drain any residual thinking buffer
        # so the user does not lose their last partial thinking on an
        # exception path. Best-effort: errors here are logged but never
        # re-raise.
        try:
            for result in response_processor.flush_pending(state):
                state = response_processor.apply_state_update(state, result)
                if result.message is not None:
                    agent_loop_response.append(result.message)
                    yield result.message
        except Exception as flush_err:  # noqa: BLE001
            logger.warning(f"Failed to flush thinking buffer on error path: {flush_err}")

        # Capture the fatal for later: the recovery phase below decides
        # whether we surface it as severity=recovered (helper_llm wrote
        # a usable reply), severity=recovered_after_reply (agent already
        # spoke before crash), or severity=fatal (no recovery possible).
        error_str = str(e)
        error_type = type(e).__name__
        logger.exception(f"[AGENT-LOOP-FATAL] {error_type}: {error_str}")
        captured_error = {"error_type": error_type, "error_message": error_str}

        # Executor OOM (SIGKILL / exit code -9): record for monitoring +
        # alerting. Retry is deferred (scheduling-resource plan) — the OOM
        # falls through to the fallback recovery below as before.
        await _record_oom_if_killed(
            db_client, ctx.user_id, error_str, bool(agent_loop_response)
        )

    # Finalize state BEFORE inspecting it — accessing `state.final_output`
    # on an unfinalized state is undefined per ExecutionState's contract.
    state = state.finalize()

    # ------------- 3.4.X: Post-loop recovery phase -------------
    # Three modes cover the recovery slot:
    #   - no_reply: clean loop, agent forgot to call send_message →
    #     helper_llm writes the missing reply using the agent's
    #     reasoning + context.
    #   - after_error: loop crashed mid-stream with no organic reply →
    #     helper_llm writes a recovery reply using full context
    #     (system prompts + completed tool results + error info).
    #   - partial_reply_then_error: loop crashed AFTER an organic reply →
    #     no helper_llm (we already spoke), but a warning badge
    #     surfaces the truncated execution.
    # Out-of-scope triggers (non-chat) and cancellation are skipped.
    # A framework auth failure (e.g. codex OAuth token expired / "refresh
    # token already used") is NOT recoverable by a helper reply — the turn
    # never ran. Fabricating a reply masks the dead login and misleads the
    # user (incident 2026-06-11). response_processor already surfaced a
    # fatal, actionable ErrorMessage tagged ``auth_expired``; when present,
    # skip the helper recovery so the user sees the re-login prompt, not a
    # gpt-5-generated answer pretending codex replied. We still fall
    # through to the PathExecutionResult below — Step 4 must persist this
    # (failed) turn like any other.
    auth_failed = any(
        isinstance(m, ErrorMessage)
        and getattr(m, "error_type", "") == AUTH_EXPIRED_ERROR_TYPE
        for m in agent_loop_response
    )
    if auth_failed:
        logger.warning(
            "[FALLBACK] skipped: agent auth expired — surfacing re-login "
            "prompt instead of fabricating a reply"
        )
    else:
        fallback_mode, skip_reason = _should_run_helper_llm_fallback(
            working_source=ctx.working_source or "",
            agent_loop_response=agent_loop_response,
            cancellation=getattr(ctx, "cancellation", None),
        )
        if fallback_mode is None and skip_reason != "already_replied_via_tool":
            logger.info(
                f"[FALLBACK] skipped: skip_reason={skip_reason!r} "
                f"(captured_error={captured_error!r})"
            )
        if fallback_mode is not None:
            logger.warning(
                f"[FALLBACK] mode={fallback_mode} "
                f"(reasoning_chars={len(state.final_output)}, "
                f"captured_error={bool(captured_error)})"
            )

        async for msg in _stream_fallback_recovery(
            fallback_mode=fallback_mode,
            captured_error=captured_error,
            context_messages=messages,
            agent_loop_response=agent_loop_response,
            final_output=state.final_output,
            user_input=ctx.input_content,
            cancellation=getattr(ctx, "cancellation", None),
            db=db_client,
            agent_id=ctx.agent_id,
        ):
            agent_loop_response.append(msg)
            yield msg

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
