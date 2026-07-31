"""
@file_name: response_processor.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent response processor

Response processing module extracted from AgentRuntime, responsible for converting raw Agent responses into typed messages.

Design principles:
- Pure function processing: no side effects, easy to test
- Single responsibility: only responsible for response parsing and conversion
- State separation: does not directly modify state, but returns processing results for the caller to use
"""

from typing import Iterator, Union, Optional
from dataclasses import dataclass
from enum import Enum
from loguru import logger

from xyz_agent_context.schema import (
    ProgressMessage,
    ProgressStatus,
    AgentPlan,
    AgentReplyDelta,
    AgentTextDelta,
    AgentThinking,
    AgentToolCall,
    ErrorMessage,
    AUTH_EXPIRED_ERROR_TYPE,
    SELF_SERVICEABLE_ERROR_TYPE,
)
from xyz_agent_context.agent_framework.loop.events import (
    DATA_TYPE_DONE,
    DATA_TYPE_ERROR,
    DATA_TYPE_REPLY_DELTA,
    DATA_TYPE_TEXT_DELTA,
    DATA_TYPE_USAGE,
    ITEM_TYPE_PLAN,
    ITEM_TYPE_THINKING,
    ITEM_TYPE_TOOL_CALL,
    ITEM_TYPE_TOOL_CALL_OUTPUT,
    TYPE_RAW_RESPONSE_EVENT,
    TYPE_RUN_ITEM_STREAM_EVENT,
)
from xyz_agent_context.agent_framework.llm.failure import (
    classify_self_serviceable,
    self_serviceable_user_message,
)
from ._thinking_batcher import _ThinkingBatcher
from .execution_state import ExecutionState
from ._agent_runtime_steps.step_display import (
    format_tool_call_for_display,
    format_thinking_for_display,
)
from xyz_agent_context.channel.message_source_handler import (
    strip_responses_api_citation_tokens,
)


# Tool-name substrings whose ``content`` / ``markdown`` / ``text`` arg
# carries user-visible reply text. When the model is gpt-5.5 with
# WebSearch, that text contains inline ``citeturnNviewN`` citation
# tokens that ChatGPT's first-party UI knows how to resolve — but
# we don't (the SDK doesn't expose the URL/title map). Strip them
# here so the live-streamed UI sees clean text. Same strip is also
# applied at ``MessageSourceHandler.extract_reply_text`` for the
# DB-persist + IM-forward paths; doing both is necessary because
# they're separate downstream consumers of the same raw tool call.
_USER_REPLY_TOOL_PATTERNS: tuple[str, ...] = (
    "send_message_to_user_directly",
    "lark_cli",
    "slack_cli",
    "tg_cli",
)


def _looks_like_user_reply_tool(tool_name: str) -> bool:
    return bool(tool_name) and any(p in tool_name for p in _USER_REPLY_TOOL_PATTERNS)


# Error categories / message fragments that mean "the coding-agent's
# credentials are dead" — the turn cannot run until the user
# re-authenticates. Framework-neutral (iron rule #9): covers codex OAuth
# (``codex_error_info == "unauthorized"`` + "log out and sign in again"),
# Anthropic/OpenAI 401s, and expired CLI sessions. A turn that fails this
# way must NOT be papered over by a helper-LLM reply (incident
# 2026-06-11: a used codex refresh token silently degraded to gpt-5 every
# turn, and the Settings page kept showing "✓ auth ready").
_AUTH_FAILURE_TYPES: frozenset[str] = frozenset({
    "unauthorized",
    "authentication_error",
    "invalid_api_key",
    "permission_error",
})
# NB: ``invalid_request_error`` is deliberately NOT a type here. It is
# OpenAI's catch-all client-error category covering bad/expired keys AND
# many non-auth 400s (context_length_exceeded, bad model id,
# content-policy). codex passes those through verbatim, so keying auth on
# the bare type misfired every long-context / bad-model turn into a fatal
# "re-login" (and suppressed the helper fallback). Genuine auth still
# resolves precisely: codex emits ``unauthorized``, Claude emits
# ``invalid_request`` (no ``_error``), and a bad OpenAI key carries the
# "Incorrect API key provided" phrase below.
_AUTH_FAILURE_PHRASES: tuple[str, ...] = (
    "sign in again",
    "log out and sign in",
    "could not be refreshed",
    "refresh token",
    "not logged in",
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",  # OpenAI's bad-key message wording
    "expired token",
    "401",
)


def _is_auth_failure(error_type: str, error_message: str) -> bool:
    """True when an API error means credentials are dead (re-login needed).

    Matches on the error category first (exact / substring) and falls back
    to message-text fragments, because codex surfaces the category as a
    bare ``codex_error_info`` string while other providers only put a
    useful signal in the human message.
    """
    et = (error_type or "").lower()
    if et in _AUTH_FAILURE_TYPES or "auth" in et or "unauthor" in et:
        return True
    em = (error_message or "").lower()
    return any(frag in em for frag in _AUTH_FAILURE_PHRASES)


# ``AUTH_EXPIRED_ERROR_TYPE`` (imported from schema above) is the
# error_type marker the runtime keys on to (a) prompt re-login and
# (b) skip the helper-LLM no_reply fallback in step_3_agent_loop. It lives
# in the schema layer to avoid a circular import with step_3_agent_loop.
_AUTH_EXPIRED_USER_MESSAGE = (
    "Your coding-agent login has expired or is no longer valid, so this "
    "turn could not run. Re-authenticate — for Claude, run "
    "`claude setup-token` and paste the token in Settings → LLM Providers "
    "(most reliable); or run `codex login` / `claude login` on the host; "
    "or assign an API-key provider to the Agent slot in Settings — then "
    "send the message again."
)

# ``SELF_SERVICEABLE_ERROR_TYPE`` marks a deterministic failure the USER can
# fix (see runtime_message.py). Like auth, step_3 keys on this error_type to
# skip the helper-LLM fallback — a fabricated reply over a turn that never
# ran hides the real, fixable cause. The actionable copy lives in
# ``llm.failure.self_serviceable_user_message`` (shared with step_3's raw-
# exception path). Reason also rides on ``ErrorMessage.action_reason`` so the
# frontend can pick its own copy.


def _clean_reply_args_in_place(arguments: dict) -> dict:
    """Return a copy of ``arguments`` with citation tokens stripped
    from the fields that carry user-visible text. ``content`` covers
    chat_module / message_bus / job; ``markdown`` / ``text`` /
    ``command`` cover Lark / Slack / Telegram CLI wrappers (which
    embed text inside a command string)."""
    if not isinstance(arguments, dict):
        return arguments
    cleaned: dict = dict(arguments)
    for key in ("content", "markdown", "text", "command"):
        v = cleaned.get(key)
        if isinstance(v, str):
            cleaned[key] = strip_responses_api_citation_tokens(v)
    return cleaned


class ResponseType(str, Enum):
    """Response type enum"""
    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    TOOL_OUTPUT = "tool_output"
    THINKING = "thinking"
    REPLY_DELTA = "reply_delta"   # NexusPower: the reply, streaming
    PLAN = "plan"                 # NexusPower: live plan snapshot
    DONE = "done"
    ERROR = "error"
    OTHER = "other"


@dataclass
class ProcessedResponse:
    """
    Processed response result

    Attributes:
        type: Response type
        message: Converted message object (can be yielded to the frontend)
        state_update: State update function name and arguments (for updating ExecutionState)
    """
    type: ResponseType
    message: Union[AgentTextDelta, AgentThinking, AgentToolCall, ProgressMessage, ErrorMessage, dict, None]
    state_update: Optional[dict] = None  # {"method": "append_text", "args": {"text": "..."}}


class ResponseProcessor:
    """
    Agent response processor

    Converts raw responses from ClaudeAgentSDK into typed messages.
    Extracted from AgentRuntime._process_agent_response.

    As of Phase B (2026-05-13) ``process`` is a GENERATOR yielding 0..N
    ``ProcessedResponse`` per raw response — to support thinking-delta
    coalescing where a single thinking_item input may not produce an
    output (still buffered) and a non-thinking input may produce TWO
    outputs (residual thinking flush + the actual non-thinking event).

    Per-instance state: a ``_ThinkingBatcher`` that coalesces consecutive
    thinking_item chunks into ~100 ms WebSocket frames. The batcher's
    lifetime is one ResponseProcessor instance == one agent turn ==
    per-run (iron rule decision: Q1 → per-run).

    Usage:
        >>> processor = ResponseProcessor()
        >>> state = ExecutionState()
        >>> for raw_response in agent_loop():
        ...     for result in processor.process(raw_response, state):
        ...         if result.message:
        ...             yield result.message
        ...         state = processor.apply_state_update(state, result)
        >>> # End-of-stream — flush any residual thinking buffer
        >>> for result in processor.flush_pending(state):
        ...     if result.message:
        ...         yield result.message
        ...     state = processor.apply_state_update(state, result)
    """

    def __init__(self) -> None:
        self._thinking_batcher = _ThinkingBatcher()
        # Monologue chunks (NexusPower text deltas displayed as thinking)
        # buffered since the last batcher flush. Kept OUTSIDE the batcher:
        # the batcher coalesces the display stream (monologue + CoT mixed,
        # iron rule #16 verbatim), while final_output must receive the
        # monologue subset only. Drained into record_thinking's
        # ``monologue`` arg at every flush site.
        self._pending_monologue: list[str] = []

    def _take_pending_monologue(self) -> str:
        text = "".join(self._pending_monologue)
        self._pending_monologue = []
        return text

    def process(
        self,
        response: dict,
        state: ExecutionState
    ) -> Iterator[ProcessedResponse]:
        """
        Process a single Agent Loop response. Yields 0..N ProcessedResponse.

        Most raw events yield exactly one ProcessedResponse — backward
        compatible. Thinking events may yield zero (still buffering) or
        one (flush triggered). Non-thinking events may yield two
        (residual thinking flush THEN the actual event) to preserve
        the user-visible chronological order.
        """
        logger.debug(f"Response[{state.response_count + 1}]: {response}")

        if not isinstance(response, dict):
            yield ProcessedResponse(
                type=ResponseType.OTHER,
                message=response,
                state_update={"method": "increment_response", "args": {}}
            )
            return

        response_type = response.get("type")

        # Handle raw_response_event (text output, completion markers, etc.)
        if response_type == TYPE_RAW_RESPONSE_EVENT:
            # Non-thinking event arriving — flush any residual thinking
            # FIRST so the front-end sees thinking → text in the actual
            # order the LLM produced it.
            yield from self._flush_thinking_residual(state)
            yield self._handle_raw_response_event(response, state)
            return

        # Handle run_item_stream_event (tool calls, tool results, etc.)
        if response_type == TYPE_RUN_ITEM_STREAM_EVENT:
            yield from self._handle_run_item_stream_event(response, state)
            return

        # Other types of responses — also flush thinking residual to be safe
        yield from self._flush_thinking_residual(state)
        yield ProcessedResponse(
            type=ResponseType.OTHER,
            message=response,
            state_update={"method": "increment_response", "args": {}}
        )

    def flush_pending(self, state: ExecutionState) -> Iterator[ProcessedResponse]:
        """Emit any residual buffered thinking content. Caller MUST
        invoke this once after the agent_loop ends (normal end,
        cancellation, exception) so the user does not silently lose
        the last partial thinking buffer.

        Returns an iterator — yields 0 or 1 ProcessedResponse.
        """
        yield from self._flush_thinking_residual(state)

    def _flush_thinking_residual(
        self, state: ExecutionState
    ) -> Iterator[ProcessedResponse]:
        """If the thinking batcher has buffered content, emit it as a
        single AgentThinking message and clear the buffer."""
        if not self._thinking_batcher.has_pending():
            return
        residual = self._thinking_batcher.flush_ws()
        if not residual:
            return
        thinking_display = format_thinking_for_display(residual)
        # Drained ONCE per flush; the message and the state update carry
        # the same subset (message: for collect_run consumers relaying
        # output_text; state: for final_output / reasoning persistence).
        monologue = self._take_pending_monologue()
        yield ProcessedResponse(
            type=ResponseType.THINKING,
            message=AgentThinking(thinking_content=residual, monologue=monologue),
            state_update={
                "method": "record_thinking",
                "args": {
                    "content": residual,
                    "display": thinking_display,
                    "monologue": monologue,
                },
            },
        )

    def apply_state_update(
        self,
        state: ExecutionState,
        result: ProcessedResponse
    ) -> ExecutionState:
        """
        Update state based on processing result

        Args:
            state: Current state
            result: Processing result

        Returns:
            Updated state
        """
        if result.state_update is None:
            return state

        method_name = result.state_update.get("method")
        args = result.state_update.get("args", {})

        if method_name and hasattr(state, method_name):
            method = getattr(state, method_name)
            return method(**args)

        return state

    def _handle_raw_response_event(
        self,
        response: dict,
        state: ExecutionState
    ) -> ProcessedResponse:
        """Handle raw_response_event type responses"""
        data = response.get("data", {})
        data_type = data.get("type")

        if data_type == DATA_TYPE_TEXT_DELTA:
            # Text delta output
            delta = data.get("delta", "")
            # Filter out empty deltas (from structural StreamEvents, input_json_delta, etc.)
            if not delta:
                return ProcessedResponse(
                    type=ResponseType.OTHER,
                    message=None
                )
            logger.debug(f"Text delta: {len(delta)} chars")
            return ProcessedResponse(
                type=ResponseType.TEXT_DELTA,
                message=AgentTextDelta(delta=delta),
                state_update={"method": "append_text", "args": {"text": delta}}
            )

        if data_type == DATA_TYPE_REPLY_DELTA:
            # NexusPower only: the user-facing reply, streamed as the
            # model writes the expression tool's argument. It is NOT
            # appended to final_output — the completed tool call remains
            # the authoritative record (this is a presentation stream,
            # so double-counting it would duplicate the reply).
            delta = data.get("delta", "")
            if not delta:
                return ProcessedResponse(type=ResponseType.OTHER, message=None)
            return ProcessedResponse(
                type=ResponseType.REPLY_DELTA,
                message=AgentReplyDelta(
                    delta=delta,
                    call_id=str(data.get("call_id", "")),
                    tool_name=str(data.get("tool_name", "")),
                ),
            )

        if data_type == DATA_TYPE_ERROR:
            # API error (rate limit, auth failure, quota exhaustion, etc.)
            # surfaced inline by the SDK while the stream is still alive.
            #
            # Pre-2026-05-11 behaviour: chat_module saw any ErrorMessage in
            # agent_loop_response and tore the whole turn down into a
            # failed user-only row. That meant a transient rate-limit blip
            # mid-loop killed turns that had already produced useful
            # output. Now we tag these as severity="recoverable" so the
            # turn keeps assembling — the agent loop may still complete
            # with a valid reply, and chat_module's fatal-only detector
            # leaves it alone. Auth/quota errors are still surfaced to the
            # user via the yielded ErrorMessage (frontend renders it as a
            # warning) and logged here for ops visibility.
            error_message = data.get("error_message", "Unknown API error")
            error_type = data.get("error_type", "api_error")

            # Auth failures are NOT recoverable by retrying or by a helper
            # reply — the credentials are dead. Surface a fatal, actionable
            # message and tag it ``auth_expired`` so step_3 skips the
            # no_reply fallback (which would otherwise fabricate a reply
            # over a turn that never ran — incident 2026-06-11).
            if _is_auth_failure(error_type, error_message):
                logger.error(
                    f"[AGENT-LOOP-AUTH] credentials failure "
                    f"({error_type}): {error_message}"
                )
                return ProcessedResponse(
                    type=ResponseType.ERROR,
                    message=ErrorMessage(
                        error_message=_AUTH_EXPIRED_USER_MESSAGE,
                        error_type=AUTH_EXPIRED_ERROR_TYPE,
                        severity="fatal",
                    ),
                    state_update={"method": "increment_response", "args": {}}
                )

            # Deterministic, user-self-serviceable failures (context window
            # too small, no credits, bad model id) recur every turn with the
            # same config. Left as "recoverable" they get papered over by the
            # helper-LLM fallback — the "black box" incident where a 32k model
            # failed every turn and DeepSeek fabricated a normal-looking reply
            # while the agent never ran. Surface them as fatal + actionable
            # and tag ``config_actionable`` so step_3 skips the fallback (same
            # contract as auth above). Checked BEFORE recoverable; auth is
            # already handled above and takes precedence.
            self_serviceable = classify_self_serviceable(error_type, error_message)
            if self_serviceable is not None:
                logger.error(
                    f"[AGENT-LOOP-SELF-SERVICEABLE] {self_serviceable} "
                    f"({error_type}): {error_message}"
                )
                return ProcessedResponse(
                    type=ResponseType.ERROR,
                    message=ErrorMessage(
                        error_message=self_serviceable_user_message(
                            self_serviceable, error_message
                        ),
                        error_type=SELF_SERVICEABLE_ERROR_TYPE,
                        severity="fatal",
                        action_reason=self_serviceable,
                    ),
                    state_update={"method": "increment_response", "args": {}}
                )

            logger.error(f"[AGENT-LOOP-RECOVERABLE] API error ({error_type}): {error_message}")
            return ProcessedResponse(
                type=ResponseType.ERROR,
                message=ErrorMessage(
                    error_message=error_message,
                    error_type=error_type,
                    severity="recoverable",
                ),
                state_update={"method": "increment_response", "args": {}}
            )

        if data_type == DATA_TYPE_USAGE:
            # Per-turn token usage harvested from the streaming events
            # (message_start → input, message_delta → output), accumulated across
            # turns into a SEPARATE streamed_* tally. It is a FALLBACK: finalize()
            # promotes it only when the terminal ResultMessage.usage is 0 (proxied
            # non-Anthropic model via the LiteLLM gateway). Keeping it separate is
            # what prevents double-counting on real Anthropic (where the DONE
            # event already carries authoritative usage). No message is yielded.
            u = data.get("usage", {})
            return ProcessedResponse(
                type=ResponseType.OTHER,
                message=None,
                state_update={
                    "method": "accumulate_streamed_usage",
                    "args": {
                        "input_tokens": u.get("input_tokens", 0) or 0,
                        "output_tokens": u.get("output_tokens", 0) or 0,
                        "cache_read_tokens": u.get("cache_read_input_tokens", 0) or 0,
                        "cache_creation_tokens": u.get("cache_creation_input_tokens", 0) or 0,
                    },
                },
            )

        if data_type == DATA_TYPE_DONE:
            # Agent Loop completion marker — authoritative token usage when the
            # CLI populates it (real Anthropic). For proxied models it is 0 and
            # finalize() falls back to the streamed_* tally above.
            usage = data.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            # Prompt-cache telemetry. Two provider vocabularies reach here:
            # Anthropic (cache_read_input_tokens + cache_creation_input_tokens)
            # and OpenAI/codex (cached_input_tokens, reads only — no write
            # counter exists in that vocabulary).
            cache_read_tokens = usage.get("cache_read_input_tokens", 0) or usage.get(
                "cached_input_tokens", 0
            )
            cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
            num_turns = data.get("num_turns")  # None when the framework doesn't report it
            # Resumable CLI session handle. None when the framework doesn't
            # report one (only Claude Code's ResultMessage carries it).
            cli_session_id = data.get("session_id")
            model = data.get("model", "")
            total_cost_usd = data.get("total_cost_usd")  # SDK-calculated cost
            stop_reason = data.get("stop_reason", "unknown")
            logger.info(
                f"Agent done: {stop_reason} model={model or '(sdk)'} "
                f"(tokens: {input_tokens}+{output_tokens}"
                f", cache_read={cache_read_tokens}, cache_write={cache_creation_tokens}"
                f"{f', turns={num_turns}' if num_turns is not None else ''}"
                f"{f', sdk_cost=${total_cost_usd:.6f}' if total_cost_usd else ''})"
            )
            return ProcessedResponse(
                type=ResponseType.DONE,
                message=None,  # Do not send message to avoid duplicate completion steps
                state_update={
                    "method": "accumulate_usage",
                    "args": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "model": model,
                        "total_cost_usd": total_cost_usd,
                        "cache_read_tokens": cache_read_tokens,
                        "cache_creation_tokens": cache_creation_tokens,
                        "num_turns": num_turns,
                        "cli_session_id": cli_session_id,
                    },
                },
            )

        # Other types of raw_response_event
        return ProcessedResponse(
            type=ResponseType.OTHER,
            message=response,
            state_update={"method": "increment_response", "args": {}}
        )

    def _handle_run_item_stream_event(
        self,
        response: dict,
        state: ExecutionState
    ) -> Iterator[ProcessedResponse]:
        """Handle run_item_stream_event type responses.

        Generator: yields 0..2 ProcessedResponse per input. A thinking
        item buffers and may yield nothing (still accumulating) or one
        coalesced AgentThinking. Non-thinking items first flush any
        residual buffered thinking THEN yield themselves — two outputs
        — so the visible chronological order tracks the LLM's actual
        emission order."""
        item = response.get("item", {})
        item_type = item.get("type")

        if item_type == ITEM_TYPE_THINKING:
            # Buffer into the WS-tier batcher. May or may not produce
            # an emission this round. The DB-tier (per-segment) flush
            # is added in Phase C alongside event_stream persistence.
            thinking_content = item.get("content", "")
            if item.get("monologue"):
                self._pending_monologue.append(thinking_content)
            coalesced = self._thinking_batcher.append_thinking(thinking_content)
            if coalesced is None:
                return  # still buffering
            thinking_display = format_thinking_for_display(coalesced)
            logger.info(f"  💭 Thinking flush: {len(coalesced)} chars (coalesced)")
            # Same single-drain discipline as _flush_thinking_residual.
            monologue = self._take_pending_monologue()
            yield ProcessedResponse(
                type=ResponseType.THINKING,
                message=AgentThinking(
                    thinking_content=coalesced, monologue=monologue
                ),
                state_update={
                    "method": "record_thinking",
                    "args": {
                        "content": coalesced,
                        "display": thinking_display,
                        "monologue": monologue,
                    },
                },
            )
            return

        # Any non-thinking item — flush thinking residual FIRST so the
        # user sees thinking → tool_call in the correct order.
        yield from self._flush_thinking_residual(state)

        if item_type == ITEM_TYPE_PLAN:
            # NexusPower only: full plan snapshot (replace-on-write).
            yield ProcessedResponse(
                type=ResponseType.PLAN,
                message=AgentPlan(
                    steps=list(item.get("steps") or []),
                    note=str(item.get("note", "")),
                ),
            )
            return

        if item_type == ITEM_TYPE_TOOL_CALL:
            # Tool call - use ProgressMessage to display in the step panel
            # Step numbering uses 3.4.x format (sub-steps of Step 3.4 Agent Loop)
            tool_name = item.get("tool_name", "unknown")
            tool_call_id = item.get("tool_call_id", "")
            arguments = item.get("arguments", {})
            # Name-first frame: the tool's name arrived before its
            # arguments finished streaming. Ship it so the UI can show
            # "using X" immediately — but for a user-reply tool drop it:
            # that reply already streams live via reply deltas, and an
            # empty-argument reply frame would inject a stray empty
            # bubble into the turn's content.
            pending = bool(item.get("pending"))
            if pending and _looks_like_user_reply_tool(tool_name):
                return
            # Strip OpenAI Responses-API citation tokens from reply
            # tools' content args. This is the LIVE-STREAMING path —
            # the cleaned arguments end up in the ProgressMessage we
            # ship to the frontend, so users see clean text in the
            # chat bubble as the tool call appears. The persist/IM
            # paths run their own strip via ``extract_reply_text``;
            # both are needed because they're independent consumers.
            if _looks_like_user_reply_tool(tool_name):
                arguments = _clean_reply_args_in_place(arguments)
            tool_count = state.tool_call_count + 1  # Next tool sequence number
            logger.info(f"Tool call: {tool_name}")

            # User-friendly display
            tool_display = format_tool_call_for_display(
                tool_name=tool_name,
                arguments=arguments,
                is_completed=False
            )

            yield ProcessedResponse(
                type=ResponseType.TOOL_CALL,
                message=ProgressMessage(
                    step=f"3.4.{tool_count}",
                    title=f"{tool_display['icon']} {tool_display['name']}",
                    description=tool_display['desc'] or "Executing...",
                    status=ProgressStatus.RUNNING,
                    details={
                        "display": tool_display,
                        "tool_name": tool_name,
                        "arguments": arguments,
                        # The frontend replaces a pending row in place when
                        # the completed call lands, keyed by tool_call_id —
                        # so both frames must carry it.
                        "tool_call_id": tool_call_id,
                        "pending": pending,
                    }
                ),
                # Only the completed call records: the pending frame is a
                # display-only preview, and recording both would double
                # the step count and the persisted timeline.
                state_update=None if pending else {
                    "method": "record_tool_call",
                    "args": {
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "arguments": arguments
                    }
                }
            )
            return

        if item_type == ITEM_TYPE_TOOL_CALL_OUTPUT:
            # Tool call result - update the corresponding tool call status to completed
            # 使用 tool_output_count + 1 作为 step ID（与 tool_call 的序号一一对应）
            # 不能用 tool_call_count，因为并行工具调用时所有 call 先到达，
            # tool_call_count 已经递增到最终值，与第一个 output 的序号不匹配。
            output = item.get("output", "")
            tool_call_id = str(item.get("tool_call_id") or "")
            tool_output_num = state.tool_output_count + 1
            logger.info(f"Tool output #{tool_output_num} received: {len(output)} chars")

            # Find the tool_call this output answers, for the display row.
            # Prefer the id when the driver reported one: the positional rule
            # below ("Nth output belongs to the Nth call") is wrong for PARALLEL
            # calls, where every call arrives before any output and the outputs
            # come back in completion order. Positional stays as the fallback
            # for drivers that report no id.
            matching_tool_name = ""
            matching_arguments = {}
            if tool_call_id:
                for step in state.all_steps:
                    if (step.get("type") == "tool_call"
                            and step.get("tool_call_id") == tool_call_id):
                        matching_tool_name = step.get("tool_name", "")
                        matching_arguments = step.get("arguments", {})
                        break
            if not matching_tool_name:
                tool_calls_seen = 0
                for step in state.all_steps:
                    if step.get("type") == "tool_call":
                        tool_calls_seen += 1
                        if tool_calls_seen == tool_output_num:
                            matching_tool_name = step.get("tool_name", "")
                            matching_arguments = step.get("arguments", {})
                            break

            # User-friendly display
            tool_display = format_tool_call_for_display(
                tool_name=matching_tool_name,
                arguments=matching_arguments,
                output=output,
                is_completed=True
            )

            yield ProcessedResponse(
                type=ResponseType.TOOL_OUTPUT,
                message=ProgressMessage(
                    step=f"3.4.{tool_output_num}",
                    title=f"{tool_display['icon']} {tool_display['name']}",
                    description=tool_display.get("result_summary", "✓ Execution completed"),
                    status=ProgressStatus.COMPLETED,
                    details={
                        "display": tool_display,
                        "output": output[:500] if len(output) > 500 else output
                    }
                ),
                state_update={
                    "method": "record_tool_output",
                    "args": {"output": output, "tool_call_id": tool_call_id}
                }
            )
            return

        # Other types of items (NOTE: thinking_item is handled at the top
        # of this method via the _ThinkingBatcher path — the legacy
        # branch is intentionally removed)
        yield ProcessedResponse(
            type=ResponseType.OTHER,
            message=response,
            state_update={"method": "increment_response", "args": {}}
        )
