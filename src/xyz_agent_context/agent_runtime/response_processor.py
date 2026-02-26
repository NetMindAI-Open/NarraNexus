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

from typing import Union, Optional
from dataclasses import dataclass
from enum import Enum
from loguru import logger

from xyz_agent_context.schema import (
    ProgressMessage,
    ProgressStatus,
    AgentTextDelta,
    AgentThinking,
    AgentToolCall,
)
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
    message: Union[AgentTextDelta, AgentThinking, AgentToolCall, ProgressMessage, dict, None]
    state_update: Optional[dict] = None  # {"method": "append_text", "args": {"text": "..."}}


class ResponseProcessor:
    """
    Agent response processor

    Converts raw responses from ClaudeAgentSDK into typed messages.
    Extracted from AgentRuntime._process_agent_response.

    Usage:
        >>> processor = ResponseProcessor()
        >>> state = ExecutionState()
        >>> for raw_response in agent_loop():
        ...     result = processor.process(raw_response, state)
        ...     if result.message:
        ...         yield result.message
        ...     state = processor.apply_state_update(state, result)
    """

    def process(
        self,
        response: dict,
        state: ExecutionState
    ) -> ProcessedResponse:
        """
        Process a single Agent Loop response

        Args:
            response: Raw response from ClaudeAgentSDK
            state: Current execution state (used for count information)

        Returns:
            ProcessedResponse: Processing result
        """
        logger.debug(f"  📨 Response[{state.response_count + 1}]: {response}")

        if not isinstance(response, dict):
            return ProcessedResponse(
                type=ResponseType.OTHER,
                message=response,
                state_update={"method": "increment_response", "args": {}}
            )

        response_type = response.get("type")

        # Handle raw_response_event (text output, completion markers, etc.)
        if response_type == "raw_response_event":
            return self._handle_raw_response_event(response, state)

        # Handle run_item_stream_event (tool calls, tool results, etc.)
        if response_type == "run_item_stream_event":
            return self._handle_run_item_stream_event(response, state)

        # Other types of responses
        return ProcessedResponse(
            type=ResponseType.OTHER,
            message=response,
            state_update={"method": "increment_response", "args": {}}
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
            logger.debug(f"  💬 Text delta: {len(delta)} chars")
            return ProcessedResponse(
                type=ResponseType.TEXT_DELTA,
                message=AgentTextDelta(delta=delta),
                state_update={"method": "append_text", "args": {"text": delta}}
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
                f"  ✅ Agent done: {stop_reason} model={model or '(sdk)'} "
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
    ) -> ProcessedResponse:
        """Handle run_item_stream_event type responses"""
        item = response.get("item", {})
        item_type = item.get("type")

        if item_type == "tool_call_item":
            # Tool call - use ProgressMessage to display in the step panel
            # Step numbering uses 3.4.x format (sub-steps of Step 3.4 Agent Loop)
            tool_name = item.get("tool_name", "unknown")
            tool_call_id = item.get("tool_call_id", "")
            arguments = item.get("arguments", {})
            tool_count = state.tool_call_count + 1  # Next tool sequence number
            logger.info(f"  🔧 Tool call: {tool_name}")

            # User-friendly display
            tool_display = format_tool_call_for_display(
                tool_name=tool_name,
                arguments=arguments,
                is_completed=False
            )

            return ProcessedResponse(
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

        if item_type == "tool_call_output_item":
            # Tool call result - update the corresponding tool call status to completed
            # 使用 tool_output_count + 1 作为 step ID（与 tool_call 的序号一一对应）
            # 不能用 tool_call_count，因为并行工具调用时所有 call 先到达，
            # tool_call_count 已经递增到最终值，与第一个 output 的序号不匹配。
            output = item.get("output", "")
            tool_output_num = state.tool_output_count + 1
            logger.info(f"  ✅ Tool output #{tool_output_num} received: {len(output)} chars")

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

            return ProcessedResponse(
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

        if item_type == "thinking_item":
            # Thinking process - use AgentThinking type, matching the message format expected by the frontend
            thinking_content = item.get("content", "")
            logger.info(f"  💭 Thinking: {len(thinking_content)} chars")

            # User-friendly display
            thinking_display = format_thinking_for_display(thinking_content)

            return ProcessedResponse(
                type=ResponseType.THINKING,
                message=AgentThinking(thinking_content=thinking_content),
                state_update={
                    "method": "record_thinking",
                    "args": {
                        "content": thinking_content,
                    }
                }
            )

        # Other types of items
        return ProcessedResponse(
            type=ResponseType.OTHER,
            message=response,
            state_update={"method": "increment_response", "args": {}}
        )
