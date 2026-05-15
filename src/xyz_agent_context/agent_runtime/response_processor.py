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
    AgentTextDelta,
    AgentThinking,
    AgentToolCall,
    ErrorMessage,
)
from ._thinking_batcher import _ThinkingBatcher
from .execution_state import ExecutionState
from ._agent_runtime_steps.step_display import (
    format_tool_call_for_display,
    format_thinking_for_display,
)


class ResponseType(str, Enum):
    """Response type enum"""
    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    TOOL_OUTPUT = "tool_output"
    THINKING = "thinking"
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
        if response_type == "raw_response_event":
            # Non-thinking event arriving — flush any residual thinking
            # FIRST so the front-end sees thinking → text in the actual
            # order the LLM produced it.
            yield from self._flush_thinking_residual(state)
            yield self._handle_raw_response_event(response, state)
            return

        # Handle run_item_stream_event (tool calls, tool results, etc.)
        if response_type == "run_item_stream_event":
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
        yield ProcessedResponse(
            type=ResponseType.THINKING,
            message=AgentThinking(thinking_content=residual),
            state_update={
                "method": "record_thinking",
                "args": {
                    "content": residual,
                    "display": thinking_display,
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

        if data_type == "response.text.delta":
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

        if data_type == "response.error":
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

        if data_type == "response.done":
            # Agent Loop completion marker — extract token usage for cost tracking
            # Claude Agent SDK puts usage in ResultMessage; model is not available,
            # so we default to the model configured in settings
            usage = data.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            model = data.get("model", "")
            total_cost_usd = data.get("total_cost_usd")  # SDK-calculated cost
            stop_reason = data.get("stop_reason", "unknown")
            logger.info(
                f"Agent done: {stop_reason} model={model or '(sdk)'} "
                f"(tokens: {input_tokens}+{output_tokens}"
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

        if item_type == "thinking_item":
            # Buffer into the WS-tier batcher. May or may not produce
            # an emission this round. The DB-tier (per-segment) flush
            # is added in Phase C alongside event_stream persistence.
            thinking_content = item.get("content", "")
            coalesced = self._thinking_batcher.append_thinking(thinking_content)
            if coalesced is None:
                return  # still buffering
            thinking_display = format_thinking_for_display(coalesced)
            logger.info(f"  💭 Thinking flush: {len(coalesced)} chars (coalesced)")
            yield ProcessedResponse(
                type=ResponseType.THINKING,
                message=AgentThinking(thinking_content=coalesced),
                state_update={
                    "method": "record_thinking",
                    "args": {
                        "content": coalesced,
                        "display": thinking_display,
                    },
                },
            )
            return

        # Any non-thinking item — flush thinking residual FIRST so the
        # user sees thinking → tool_call in the correct order.
        yield from self._flush_thinking_residual(state)

        if item_type == "tool_call_item":
            # Tool call - use ProgressMessage to display in the step panel
            # Step numbering uses 3.4.x format (sub-steps of Step 3.4 Agent Loop)
            tool_name = item.get("tool_name", "unknown")
            tool_call_id = item.get("tool_call_id", "")
            arguments = item.get("arguments", {})
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
                        "arguments": arguments
                    }
                ),
                state_update={
                    "method": "record_tool_call",
                    "args": {
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "arguments": arguments
                    }
                }
            )
            return

        if item_type == "tool_call_output_item":
            # Tool call result - update the corresponding tool call status to completed
            # 使用 tool_output_count + 1 作为 step ID（与 tool_call 的序号一一对应）
            # 不能用 tool_call_count，因为并行工具调用时所有 call 先到达，
            # tool_call_count 已经递增到最终值，与第一个 output 的序号不匹配。
            output = item.get("output", "")
            tool_output_num = state.tool_output_count + 1
            logger.info(f"Tool output #{tool_output_num} received: {len(output)} chars")

            # 查找对应的 tool_call 信息用于展示
            # tool_output 按顺序到达，第 N 个 output 对应第 N 个 call
            matching_tool_name = ""
            matching_arguments = {}
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
                    "args": {"output": output}
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
