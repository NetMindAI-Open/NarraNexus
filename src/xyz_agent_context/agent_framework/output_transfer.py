"""
@file_name: output_transfer.py
@author: NetMind.AI
@date: 2025-11-15
@description: 将不同 Agent SDK 的输出转换为统一的 OpenAI Agents SDK 格式。

返回值为 List[Dict]，因为一条 SDK 消息可能包含多个内容块（如多个 ToolUseBlock），
每个块对应一个独立事件。
"""

import json
from typing import Any, Dict, List

from loguru import logger


def _stringify_tool_result_content(content: Any) -> str:
    """Flatten a ToolResultBlock.content into the tool's plain-text payload.

    The Claude Agent SDK delivers a tool result either as a bare string or as
    a list of content blocks — dicts shaped like ``{"type": "text", "text":
    ...}`` or SDK block objects exposing a ``.text`` attribute. An MCP tool
    that returns a dict (e.g. ``register_artifact``) arrives as a single text
    block whose ``text`` is the JSON-encoded result.

    The previous implementation used ``str(block.content)``, which on a list
    produces a Python repr (``[{'type': 'text', 'text': '...'}]``) — NOT valid
    JSON. Every frontend consumer that ``JSON.parse``-s ``tool_output``
    (artifact discovery, quota-error detection) silently failed on it, so
    agent-created artifacts never surfaced until an unrelated reload. This
    flattens to the actual text payload so the result stays parseable.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                parts.append(
                    text if isinstance(text, str)
                    else json.dumps(item, ensure_ascii=False)
                )
            elif hasattr(item, "text"):
                parts.append(str(item.text))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def output_transfer(
    message: Any,
    transfer_type: str = "claude_agent_sdk",
    streaming: bool = True
) -> List[Dict[str, Any]]:
    """
    将 Agent SDK 输出转换为 OpenAI Agents SDK 格式。

    返回事件列表（一条 SDK 消息可能产生多个事件，如并行工具调用）。

    Args:
        message: Agent SDK 消息对象
        transfer_type: 转换类型
        streaming: 是否流式输出

    Returns:
        事件字典列表
    """
    if transfer_type == "claude_agent_sdk":
        return _claude_to_openai_agents(message, streaming=streaming)
    if transfer_type == "codex_cli":
        return _codex_to_openai_agents(message, streaming=streaming)
    if transfer_type == "codex_official":
        return _codex_official_to_openai_agents(message, streaming=streaming)
    else:
        raise ValueError(f"Unknown transfer type: {transfer_type}")


def _claude_to_openai_agents(message: Any, streaming: bool = True) -> List[Dict[str, Any]]:
    """
    将 Claude Agent SDK 消息转换为 OpenAI Agents SDK 格式。

    返回事件列表。大多数消息产生一个事件，但 AssistantMessage（多个 ToolUseBlock）
    和 UserMessage（多个 ToolResultBlock）可产生多个事件。
    """
    message_type = type(message).__name__

    if streaming:
        return _convert_to_streaming_events(message, message_type)
    else:
        return [_convert_to_non_streaming_result(message, message_type)]


def _convert_to_streaming_events(message: Any, message_type: str) -> List[Dict[str, Any]]:
    """将 Claude 消息转换为流式事件列表。"""

    if message_type == "AssistantMessage":
        return _convert_assistant_to_stream_events(message)
    elif message_type == "StreamEvent":
        return [_convert_stream_event_to_stream_event(message)]
    elif message_type == "ResultMessage":
        return [_convert_result_to_stream_event(message)]
    elif message_type == "SystemMessage":
        return [_convert_system_to_stream_event(message)]
    elif message_type == "UserMessage":
        return _convert_user_to_stream_events(message)
    else:
        return [{
            "type": "raw_response_event",
            "data": {
                "type": "response.text.delta",
                "delta": f"[Unknown message type: {message_type}]"
            }
        }]


def _convert_to_non_streaming_result(message: Any, message_type: str) -> Dict[str, Any]:
    """Convert Claude message to OpenAI Agents SDK non-streaming result format."""

    # For non-streaming, we accumulate all content and return a RunResult-like structure
    result = {
        "final_output": "",
        "new_items": [],
        "usage": {}
    }

    if message_type == "AssistantMessage":
        # Extract text content
        text_parts = []
        tool_calls = []

        if hasattr(message, 'content') and message.content:
            for block in message.content:
                block_type = type(block).__name__

                if block_type == "TextBlock" and hasattr(block, 'text'):
                    text_parts.append(block.text)
                    result["new_items"].append({
                        "type": "message_output_item",
                        "content": block.text
                    })
                elif block_type == "ThinkingBlock" and hasattr(block, 'thinking'):
                    # Optionally include thinking
                    result["new_items"].append({
                        "type": "thinking_item",
                        "content": block.thinking
                    })
                elif block_type == "ToolUseBlock":
                    if hasattr(block, 'id') and hasattr(block, 'name') and hasattr(block, 'input'):
                        tool_call = {
                            "type": "tool_call_item",
                            "tool_call_id": block.id,
                            "tool_name": block.name,
                            "arguments": block.input
                        }
                        tool_calls.append(tool_call)
                        result["new_items"].append(tool_call)

        result["final_output"] = "\n".join(text_parts) if text_parts else ""

    elif message_type == "ResultMessage":
        # Add usage information
        # ResultMessage.usage is dict[str, Any] | None (not an object with attributes)
        raw_usage = getattr(message, 'usage', None)
        if isinstance(raw_usage, dict):
            if "input_tokens" in raw_usage:
                result["usage"]["input_tokens"] = raw_usage["input_tokens"]
            if "output_tokens" in raw_usage:
                result["usage"]["output_tokens"] = raw_usage["output_tokens"]
            if result["usage"]:
                result["usage"]["total_tokens"] = (
                    result["usage"].get("input_tokens", 0) +
                    result["usage"].get("output_tokens", 0)
                )

        # Add stop reason
        if hasattr(message, 'stop_reason'):
            result["stop_reason"] = message.stop_reason

    return result


def _convert_assistant_to_stream_events(message: Any) -> List[Dict[str, Any]]:
    """将 Claude AssistantMessage 转换为流式事件列表。

    include_partial_messages=True 时，文本和思考内容会到达两次：
    先通过 StreamEvent（逐 token），再通过完整 AssistantMessage。
    因此这里跳过 TextBlock 和 ThinkingBlock（已经流式过了），
    只提取所有 ToolUseBlock 事件（用于 Steps 面板）。

    注意：partial AssistantMessage 也会携带 ToolUseBlock，导致同一个 tool_call_id
    出现多次。去重逻辑在 xyz_claude_agent_sdk.py 的 agent_loop 中处理。
    """

    # Check AssistantMessage.error field (auth failure, quota exhaustion, rate limit, etc.)
    # SDK defines: "authentication_failed" | "billing_error" | "rate_limit" |
    #              "invalid_request" | "server_error" | "unknown"
    if hasattr(message, 'error') and message.error is not None:
        error_type = str(message.error)  # Already a standardized literal from SDK

        # Human-readable messages for each error type
        error_messages = {
            "rate_limit": "Claude API rate limit reached. Please wait a moment and try again.",
            "authentication_failed": "Claude API authentication failed. Please check your API key.",
            "billing_error": "Claude API billing error. Please check your account credits.",
            "invalid_request": "Claude API received an invalid request.",
            "server_error": "Claude API server error. Please try again later.",
        }
        error_message = error_messages.get(error_type, f"Claude API error: {error_type}")

        return [{
            "type": "raw_response_event",
            "data": {
                "type": "response.error",
                "error_message": error_message,
                "error_type": error_type,
            }
        }]

    if not hasattr(message, 'content') or not message.content:
        return [_empty_delta()]

    # 提取所有 ToolUseBlock，每个生成一个 tool_call_item 事件
    events: List[Dict[str, Any]] = []
    for block in message.content:
        block_type = type(block).__name__

        if block_type == "ToolUseBlock":
            if hasattr(block, 'id') and hasattr(block, 'name') and hasattr(block, 'input'):
                events.append({
                    "type": "run_item_stream_event",
                    "item": {
                        "type": "tool_call_item",
                        "tool_call_id": block.id,
                        "tool_name": block.name,
                        "arguments": block.input
                    }
                })
        # TextBlock, ThinkingBlock → 跳过（已通过 StreamEvent 流式传输）

    # 没有 ToolUseBlock 时返回空 delta（内容已通过 StreamEvent 流式传输）
    return events if events else [_empty_delta()]


def _convert_stream_event_to_stream_event(message: Any) -> Dict[str, Any]:
    """Convert Claude StreamEvent to OpenAI Agents SDK stream event.

    With include_partial_messages=True, StreamEvent carries an `event` dict
    containing Anthropic API streaming events (content_block_delta, etc.).
    We extract text and thinking deltas and forward them to the frontend.
    """

    event = getattr(message, 'event', None)
    if not isinstance(event, dict):
        # Fallback for unexpected format
        return {
            "type": "raw_response_event",
            "data": {
                "type": "response.text.delta",
                "delta": ""
            }
        }

    event_type = event.get("type", "")

    if event_type == "content_block_delta":
        delta = event.get("delta", {})
        delta_type = delta.get("type", "")

        if delta_type == "text_delta":
            return {
                "type": "raw_response_event",
                "data": {
                    "type": "response.text.delta",
                    "delta": delta.get("text", "")
                }
            }

        if delta_type == "thinking_delta":
            return {
                "type": "run_item_stream_event",
                "item": {
                    "type": "thinking_item",
                    "content": delta.get("thinking", "")
                }
            }

        # input_json_delta, signature_delta → skip (empty content)
        return {
            "type": "raw_response_event",
            "data": {
                "type": "response.text.delta",
                "delta": ""
            }
        }

    # Structural events (content_block_start/stop, message_start/delta/stop) → skip
    return {
        "type": "raw_response_event",
        "data": {
            "type": "response.text.delta",
            "delta": ""
        }
    }


def _convert_result_to_stream_event(message: Any) -> Dict[str, Any]:
    """Convert Claude ResultMessage to OpenAI Agents SDK stream event (completion marker)."""

    # Result message typically marks the end of the stream
    # We represent this as a raw_response_event with type "response.done"
    data = {
        "type": "response.done",
    }

    # Add usage info if available
    # ResultMessage.usage is dict[str, Any] | None (not an object with attributes)
    raw_usage = getattr(message, 'usage', None)
    if isinstance(raw_usage, dict):
        usage = {}
        if "input_tokens" in raw_usage:
            usage["input_tokens"] = raw_usage["input_tokens"]
        if "output_tokens" in raw_usage:
            usage["output_tokens"] = raw_usage["output_tokens"]
        # Include cache tokens for accurate cost calculation
        if "cache_creation_input_tokens" in raw_usage:
            usage["cache_creation_input_tokens"] = raw_usage["cache_creation_input_tokens"]
        if "cache_read_input_tokens" in raw_usage:
            usage["cache_read_input_tokens"] = raw_usage["cache_read_input_tokens"]
        if usage:
            usage["total_tokens"] = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            data["usage"] = usage

        # Extract model name from usage dict if CLI provides it
        if "model" in raw_usage:
            data["model"] = raw_usage["model"]

    # Claude Agent SDK doesn't expose the actual model name.
    # Use "claude-code" as an honest label; cost comes from sdk_cost_usd, not price table.
    if "model" not in data:
        data["model"] = "claude-code"

    # Add SDK-calculated cost (ResultMessage.total_cost_usd)
    total_cost = getattr(message, 'total_cost_usd', None)
    if total_cost is not None:
        data["total_cost_usd"] = total_cost

    # Add stop reason
    if hasattr(message, 'stop_reason'):
        data["stop_reason"] = message.stop_reason

    return {
        "type": "raw_response_event",
        "data": data
    }


def _convert_system_to_stream_event(message: Any) -> Dict[str, Any]:
    """Convert Claude SystemMessage to OpenAI Agents SDK stream event."""

    content = ""
    if hasattr(message, 'metadata'):
        content = f"[System: {message.metadata}]"

    return {
        "type": "raw_response_event",
        "data": {
            "type": "response.text.delta",
            "delta": content
        }
    }


def _convert_user_to_stream_events(message: Any) -> List[Dict[str, Any]]:
    """将 Claude UserMessage 转换为流式事件列表。

    一条 UserMessage 可能包含多个 ToolResultBlock（并行工具调用的结果），
    每个 ToolResultBlock 生成一个独立的 tool_call_output_item 事件。
    """

    events: List[Dict[str, Any]] = []
    text_parts: list[str] = []

    if hasattr(message, 'content') and message.content:
        for block in message.content:
            block_type = type(block).__name__

            if block_type == "TextBlock" and hasattr(block, 'text'):
                text_parts.append(block.text)
            elif block_type == "ToolResultBlock" and hasattr(block, 'content'):
                events.append({
                    "type": "run_item_stream_event",
                    "item": {
                        "type": "tool_call_output_item",
                        "output": _stringify_tool_result_content(block.content)
                    }
                })

    # 如果有 ToolResultBlock 事件，直接返回（文本内容通常是内部消息，不需要展示）
    if events:
        return events

    # 没有 ToolResultBlock 时，返回文本内容
    content = "\n".join(text_parts) if text_parts else ""
    return [{
        "type": "raw_response_event",
        "data": {
            "type": "response.text.delta",
            "delta": content
        }
    }]


def _empty_delta() -> Dict[str, Any]:
    """返回空的 text delta 事件"""
    return {
        "type": "raw_response_event",
        "data": {
            "type": "response.text.delta",
            "delta": ""
        }
    }


# =============================================================================
# Codex CLI translator
# =============================================================================
#
# Codex CLI emits JSON Lines on stdout when invoked with --json. Each line
# is a complete JSON object with a ``type`` field. Event surface (from
# docs https://developers.openai.com/codex/noninteractive):
#
#   thread.started        — turn-bracket marker (info-only)
#   turn.started          — turn-bracket marker (info-only)
#   item.started          — agentic item begins (text, reasoning, tool call,
#                           file change, MCP call, web search)
#   item.completed        — item finishes
#   turn.completed        — usage payload arrives here
#   turn.failed           — non-recoverable turn error
#   error                 — top-level fatal error
#
# We translate these into the OpenAI Agents SDK event shape that
# response_processor.process already consumes, so downstream code is
# unchanged. Unknown event types are dropped (forward-compat) rather
# than raised — Codex may introduce new event types in future releases.

# Codex item ``type`` values we know how to translate. Anything not
# listed here is silently dropped with a debug log on the wrapper side.
_CODEX_ITEM_TYPES_TEXT = frozenset({"agent_message"})
_CODEX_ITEM_TYPES_THINKING = frozenset({"reasoning"})
_CODEX_ITEM_TYPES_TOOL = frozenset({"command_execution", "mcp_tool_call", "web_search"})


def _codex_to_openai_agents(
    event: Dict[str, Any], streaming: bool = True
) -> List[Dict[str, Any]]:
    """Translate one parsed Codex JSON Lines event into 0..N
    OpenAI-Agents-style events that ``response_processor`` consumes.

    Args:
        event: A dict parsed from a single line of ``codex exec --json``
            stdout. Must carry a ``type`` field; anything else is
            treated as unknown.
        streaming: Currently ignored — Codex's `item.started` /
            `item.completed` pair already encodes the streaming
            boundary. Kept for signature symmetry with the Claude
            translator.

    Returns:
        List of event dicts shaped like ``{"type": "raw_response_event"}``
        or ``{"type": "run_item_stream_event"}``. Empty list means
        "drop this event" (info-only or unknown).
    """
    del streaming  # signature-only — see note above

    if not isinstance(event, dict):
        return []

    event_type = event.get("type")

    # ---- turn / thread brackets — info only, drop ---------------------
    if event_type in {"thread.started", "turn.started"}:
        return []

    # ---- item.started / item.completed -------------------------------
    if event_type in {"item.started", "item.completed"}:
        return _translate_codex_item(event, is_completed=(event_type == "item.completed"))

    # ---- turn.completed — usage payload ------------------------------
    if event_type == "turn.completed":
        usage = event.get("usage") or {}
        # Mirror Claude's ResultMessage translation: response_processor
        # only folds usage from raw_response_event/response.done.
        return [{
            "type": "raw_response_event",
            "data": {
                "type": "response.done",
                "usage": {
                    "input_tokens": int(usage.get("input_tokens") or 0),
                    "output_tokens": int(usage.get("output_tokens") or 0),
                    "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
                },
                "stop_reason": event.get("reason") or "completed",
            },
        }]

    # ---- failures ----------------------------------------------------
    if event_type in {"turn.failed", "error"}:
        # response_processor doesn't have a dedicated error type; we
        # surface as a generic raw_response_event with the error text
        # so the user sees something. The wrapper also logs at ERROR
        # level when it sees these.
        err = event.get("error") or event.get("message") or "unknown"
        return [{
            "type": "raw_response_event",
            "data": {
                "type": "response.error",
                "error_message": str(err),
                "error_type": event_type,
            },
        }]

    # Unknown — drop with no noise (forward-compat with future Codex
    # event types). The wrapper logs at DEBUG so ops can spot novel
    # types if they ever matter.
    return []


def _translate_codex_item(
    event: Dict[str, Any], *, is_completed: bool
) -> List[Dict[str, Any]]:
    """Handle one ``item.started`` or ``item.completed`` Codex event."""
    item = event.get("item") or {}
    if not isinstance(item, dict):
        return []

    item_type = item.get("type")
    item_id = item.get("id") or ""

    # ---- text from the model -----------------------------------------
    if item_type in _CODEX_ITEM_TYPES_TEXT:
        text = item.get("text") or ""
        # Both started and completed carry the same delta text from
        # Codex's perspective — we only emit on completed to avoid
        # double-rendering (Codex's `item.started` for agent_message
        # arrives with empty text; the full text only lands on
        # `item.completed`).
        if not is_completed or not text:
            return []
        return [{
            "type": "raw_response_event",
            "data": {
                "type": "response.text.delta",
                "delta": text,
            },
        }]

    # ---- reasoning ("thinking") --------------------------------------
    if item_type in _CODEX_ITEM_TYPES_THINKING:
        text = item.get("text") or ""
        if not is_completed or not text:
            return []
        # Map to the same thinking item shape as Claude's StreamEvent
        # translator so response_processor's _ThinkingBatcher sees it.
        return [{
            "type": "run_item_stream_event",
            "item": {
                "type": "thinking_item",
                "content": text,
            },
        }]

    # ---- tool calls (command_execution / mcp_tool_call / web_search) -----
    if item_type in _CODEX_ITEM_TYPES_TOOL:
        # Codex emits item.started AND item.completed for tool items.
        # We translate BOTH so the frontend can show "tool running"
        # state on started + "tool output" on completed.
        if not is_completed:
            # Start: emit tool_call_item with args, no output yet.
            # IMPORTANT: response_processor._handle_run_item_stream_event
            # reads ``item.get("tool_name", "unknown")``, NOT
            # ``item.get("name", ...)``. Using the wrong key silently
            # records every tool call as "unknown" and the frontend
            # filters / hides them after the run completes.
            return [{
                "type": "run_item_stream_event",
                "item": {
                    "type": "tool_call_item",
                    "tool_call_id": item_id,
                    "tool_name": _codex_tool_name(item),
                    "arguments": _codex_tool_args(item),
                },
            }]
        # Completed: emit tool_call_output_item (deduped on
        # tool_call_id by the wrapper's seen_tool_call_ids set). The
        # output handler in response_processor looks up the matching
        # tool_name from the previously-recorded tool_call step in
        # state.all_steps, so consistency with the started side
        # matters for the post-run rendering path.
        return [{
            "type": "run_item_stream_event",
            "item": {
                "type": "tool_call_output_item",
                "tool_call_id": item_id,
                "tool_name": _codex_tool_name(item),
                "output": _codex_tool_output(item),
                "status": item.get("status") or "completed",
            },
        }]

    # Unknown item type — drop. Forward-compat.
    return []


def _codex_tool_name(item: Dict[str, Any]) -> str:
    """Name to surface for the tool_call_item, by Codex item subtype."""
    item_type = item.get("type") or ""
    if item_type == "command_execution":
        return "Bash"  # render as Bash to match CC's tool naming
    if item_type == "mcp_tool_call":
        server = item.get("server") or ""
        tool = item.get("tool") or ""
        return f"mcp__{server}__{tool}" if server and tool else "mcp"
    if item_type == "web_search":
        return "WebSearch"
    return item_type or "tool"


def _codex_tool_args(item: Dict[str, Any]) -> Dict[str, Any]:
    """Tool arguments shape for tool_call_item, by Codex item subtype."""
    item_type = item.get("type") or ""
    if item_type == "command_execution":
        return {"command": item.get("command") or ""}
    if item_type == "mcp_tool_call":
        # MCP tool args arrive as a dict; pass through.
        args = item.get("arguments")
        if isinstance(args, dict):
            return args
        return {}
    if item_type == "web_search":
        return {"query": item.get("query") or ""}
    return {}


def _codex_tool_output(item: Dict[str, Any]) -> str:
    """Tool output payload string for tool_call_output_item.

    Field names observed from real ``codex exec --json`` output
    (2026-06-01 log capture):

    - ``command_execution``: stdout+stderr is in **``aggregated_output``**
      (NOT ``output`` as the public docs suggest). ``exit_code`` is an
      integer on completed items; we surface it when the command failed
      so the agent can see the non-zero status alongside any captured
      text.
    - ``mcp_tool_call``: result payload in ``result`` (dict / list / string).
    - ``web_search``: ``results`` list of {title, url, snippet}.

    Fallback to ``output`` is kept for forward-compat: if a future
    Codex CLI version renames the field back, both keys will be tried.
    """
    item_type = item.get("type") or ""
    if item_type == "command_execution":
        text = item.get("aggregated_output")
        if not text:
            text = item.get("output", "")
        body = _stringify_tool_result_content(text)
        exit_code = item.get("exit_code")
        # Surface non-zero exit codes when output is otherwise quiet —
        # otherwise the agent sees an empty box and may retry the same
        # failing command.
        if isinstance(exit_code, int) and exit_code != 0:
            if body:
                body = f"{body}\n[exit code: {exit_code}]"
            else:
                body = f"[exit code: {exit_code}]"
        return body
    if item_type == "mcp_tool_call":
        result = item.get("result")
        if isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=False)
        return _stringify_tool_result_content(result)
    if item_type == "web_search":
        # web_search.results is usually a list of {title, url, snippet}
        results = item.get("results")
        if results is None:
            return ""
        return json.dumps(results, ensure_ascii=False)
    return ""


# =========================================================================
# Codex OFFICIAL SDK translator
# =========================================================================
#
# Translates ``openai_codex.models.Notification``-shaped dicts (as
# emitted by :class:`xyz_codex_official_sdk.CodexSDKv2.agent_loop`)
# into the same event shape ``response_processor`` consumes from v1
# and ClaudeAgentSDK.
#
# Input shape: dict produced by ``Notification.model_dump()`` (which
# is a dataclass with ``method: str`` + ``payload: NotificationPayload``)
# OR a manually-constructed dict that mirrors the same fields. For
# resilience the translator handles both ``method`` at top level AND
# embedded inside ``payload`` (some SDK paths emit the type
# discriminator at one level, some at the other).
#
# Forward-compat: novel notification types from future SDK releases
# are silently dropped with a DEBUG log — never raise. Same pattern
# as the v1 codex_cli translator's unknown-type branch.

# ThreadItem.type spelling normalizer: SDK v2 ships camelCase
# (``agentMessage``, ``mcpToolCall``, ``commandExecution``, ...) while
# the v1 ``codex exec`` JSON stream emitted snake_case
# (``agent_message``, ``mcp_tool_call``, ``command_execution``, ...).
# The shared v1 helper checks ``item.type`` against snake_case
# frozensets, so v2 items would silently drop without this mapping.
# Contract test ``test_v2_item_type_table_covers_known_sdk_types``
# (test_codex_sdk_v2_init.py) re-checks coverage against the SDK's
# ThreadItem variant Literals on every CI run.
_V2_ITEM_TYPE_TO_V1: Dict[str, str] = {
    "agentMessage": "agent_message",
    "userMessage": "user_message",
    "mcpToolCall": "mcp_tool_call",
    "commandExecution": "command_execution",
    "webSearch": "web_search",
    "reasoning": "reasoning",  # already snake-case shape; explicit for clarity
}


# Notification ``method`` strings the codex JSON-RPC server emits.
# CANONICAL source: ``openai_codex.generated.notification_registry.NOTIFICATION_MODELS``
# (a method-name → pydantic-class dict shipped with the SDK).
#
# These strings MUST exactly match the registry keys. The contract test
# ``test_method_constants_match_sdk_notification_registry`` re-checks
# this on every test run by importing the live registry — if the SDK
# renames a method in a future release we fail at CI, not on a user's
# first turn. Initial v2 commit had every "item/*" name on the
# (non-existent) "turn/*" namespace, which silently dropped every
# notification and made reasoning + tool calls leak as text. Burned
# 2026-06-08 — won't burn again.
_METHOD_THREAD_STARTED = "thread/started"
_METHOD_TURN_STARTED = "turn/started"
_METHOD_TURN_COMPLETED = "turn/completed"
# NOTE: there is NO "turn/failed" notification. Failed turns surface
# via ``turn/completed`` with ``turn.status == "failed"`` and
# ``turn.error`` populated — handled inline in the TURN_COMPLETED
# branch below.
_METHOD_ITEM_STARTED = "item/started"
_METHOD_ITEM_COMPLETED = "item/completed"
_METHOD_AGENT_MESSAGE_DELTA = "item/agentMessage/delta"
_METHOD_REASONING_TEXT_DELTA = "item/reasoning/textDelta"
_METHOD_REASONING_SUMMARY_DELTA = "item/reasoning/summaryTextDelta"
_METHOD_REASONING_SUMMARY_PART = "item/reasoning/summaryPartAdded"
_METHOD_COMMAND_OUTPUT_DELTA = "item/commandExecution/outputDelta"
_METHOD_MCP_TOOL_PROGRESS = "item/mcpToolCall/progress"
_METHOD_ERROR = "error"
_METHOD_CONFIG_WARNING = "configWarning"
_METHOD_CONTEXT_COMPACTED = "thread/compacted"
_METHOD_TOKEN_USAGE_UPDATED = "thread/tokenUsage/updated"


def _codex_error_fields(err_obj: Any) -> tuple[str | None, str | None]:
    """Extract ``(message, error_type)`` from a codex error object.

    The shape is NOT stable across codex builds. ``err_obj`` is usually a
    dict (TurnError) but can arrive as a bare string for transport-level
    failures. ``error_type`` prefers a structured top-level ``type``, then
    the ``codex_error_info`` category — which itself is a dict in some
    builds and a bare STRING (e.g. ``"unauthorized"``, ``"stream_error"``)
    in others. Returning the category string verbatim lets downstream
    classify auth failures (incident 2026-06-11: codex OAuth token expired
    → ``codex_error_info == "unauthorized"``). Both levels are type-checked
    so a string never reaches ``.get`` (the original AttributeError crash).

    Returns ``(None, None)`` for shapes we can't read; callers supply their
    own message/type defaults.
    """
    if isinstance(err_obj, str):
        return err_obj, None
    if not isinstance(err_obj, dict):
        return None, None
    msg = (
        err_obj.get("message")
        or err_obj.get("display_message")
        or err_obj.get("additional_details")
    )
    error_type = err_obj.get("type")
    if not error_type:
        info = err_obj.get("codex_error_info")
        if isinstance(info, dict):
            error_type = info.get("type")
        elif isinstance(info, str) and info:
            error_type = info
    return msg, error_type


def _codex_official_to_openai_agents(
    message: Any, streaming: bool = True
) -> List[Dict[str, Any]]:
    """Translate an official-SDK Notification dict into the internal
    event shape ``ResponseProcessor`` consumes.

    The SDK emits richer events than the v1 ``exec --json`` JSON Lines
    protocol — most notably ``ReasoningSummaryTextDeltaNotification``
    streams the Thinking-panel text token-by-token rather than landing
    as a single ``item.completed`` (which is v1's UX limitation).

    Args:
        message: Either a dict (Notification.model_dump output) or a
            pydantic Notification instance. Both shapes are handled.
        streaming: Whether the caller wants streaming events. Always
            True in production; False only in tests that want
            collapsed events.

    Returns:
        List of internal event dicts. Most notifications produce one
        event; some (item.completed for mcp_tool_call) reuse the v1
        codex_cli helpers and may produce zero events if the wrapped
        item lacks required fields.
    """
    # Accept both pydantic models and plain dicts.
    if hasattr(message, "model_dump"):
        msg = message.model_dump(mode="json", by_alias=False)
    elif isinstance(message, dict):
        msg = message
    else:
        logger.debug(
            f"_codex_official_to_openai_agents: non-dict, non-model input "
            f"{type(message).__name__} — dropping"
        )
        return []

    method = msg.get("method", "")
    payload = msg.get("payload") or {}

    # ----------------------------------------------------------------
    # Lifecycle: thread/turn start/done/fail
    # ----------------------------------------------------------------
    if method in (_METHOD_THREAD_STARTED, _METHOD_TURN_STARTED):
        # Info-only; the runtime doesn't need these to start streaming.
        return []

    if method == _METHOD_TURN_COMPLETED:
        # Payload shape per ``TurnCompletedNotification``: contains
        # ``turn: Turn`` (with ``status``, ``error``, and the items
        # list) plus the thread/turn ids. A failed turn surfaces here
        # — not on a separate "turn/failed" notification, which the
        # SDK does NOT emit.
        turn_obj = payload.get("turn") or {}
        status = turn_obj.get("status") or ""
        if status == "failed":
            # A failed turn's error carries the same TurnError shape as the
            # standalone ``error`` notification — including the bare-string
            # ``codex_error_info`` (e.g. "unauthorized") we must preserve as
            # error_type so the runtime can classify auth failures.
            err_msg, err_type = _codex_error_fields(turn_obj.get("error"))
            return [{
                "type": "raw_response_event",
                "data": {
                    "type": "response.error",
                    "error_message": str(err_msg or "turn failed"),
                    "error_type": err_type or "turn.failed",
                },
            }]
        # Healthy completion — emit response.done with usage.
        usage_src = (
            turn_obj.get("usage")
            or (turn_obj.get("token_usage") or {})
            or payload.get("usage")
            or {}
        )
        usage = {
            "input_tokens": usage_src.get("input_tokens", 0),
            "output_tokens": usage_src.get("output_tokens", 0),
            "cached_input_tokens": usage_src.get("cached_input_tokens", 0),
        }
        return [{
            "type": "raw_response_event",
            "data": {"type": "response.done", "usage": usage},
        }]

    if method == _METHOD_ERROR:
        # Transient retrying errors (codex reconnecting a dropped stream,
        # etc.) carry ``will_retry: True``. They are codex's own internal
        # retry chatter — e.g. "Reconnecting... 2/5" — NOT a final outcome.
        # Surfacing them spams the chat with bogus error bubbles (incident
        # 2026-06-11). Drop them; the real result still arrives via a
        # non-retrying ``error`` or ``turn/completed`` (status=failed).
        if isinstance(payload, dict) and payload.get("will_retry"):
            logger.debug(
                f"[codex_official translator] dropping transient retrying "
                f"error: {payload.get('error')!r}"
            )
            return []

        # ErrorNotification.error is a TurnError (dict) or — for some
        # transport failures — a bare string. ``codex_error_info`` is a
        # dict in some builds and a STRING (e.g. "unauthorized",
        # "stream_error") in others. ``_codex_error_fields`` type-checks
        # every level so a string never reaches ``.get`` — the original
        # AttributeError crash (incident 2026-06-11) that killed
        # CodexSDKv2.agent_loop and forced helper fallback every turn.
        # It also surfaces the category string as error_type so the
        # runtime can recognise an auth failure and prompt re-login.
        err_msg, err_type = _codex_error_fields(payload.get("error"))
        return [{
            "type": "raw_response_event",
            "data": {
                "type": "response.error",
                "error_message": str(err_msg or "unknown error"),
                "error_type": err_type or "error",
            },
        }]

    # ----------------------------------------------------------------
    # Streaming deltas (the v2 UX win — v1 doesn't have these)
    # ----------------------------------------------------------------
    if method == _METHOD_AGENT_MESSAGE_DELTA:
        delta = payload.get("delta") or ""
        if not delta:
            return []
        return [{
            "type": "raw_response_event",
            "data": {"type": "response.text.delta", "delta": delta},
        }]

    if method in (
        _METHOD_REASONING_TEXT_DELTA,        # raw ``reasoning_text`` delta
        _METHOD_REASONING_SUMMARY_DELTA,     # gated ``summary_text`` delta
    ):
        # Both land in the same visible Thinking panel. This is safe by an
        # invariant, NOT by luck: codex only streams raw ``textDelta``
        # (``reasoning_text``) when ``show_raw_agent_reasoning`` is enabled,
        # and we deliberately never set it (the config builder writes only
        # ``model_reasoning_summary="detailed"``). So for OpenAI's
        # gated-CoT models only ``summaryTextDelta`` fires — raw chain of
        # thought is never surfaced. ``textDelta`` carries content only for
        # providers that NATIVELY expose reasoning (DeepSeek-R1 and similar),
        # where showing it is the intended UX (matches CC/DeepSeek thinking
        # streaming). If you ever enable ``show_raw_agent_reasoning``, revisit
        # this branch — it would then leak OpenAI's raw CoT to the user.
        delta = payload.get("delta") or ""
        if not delta:
            return []
        # IMPORTANT: emit ``thinking_item`` (NOT ``thinking_delta``).
        # ``response_processor._handle_run_item_stream_event`` only
        # recognises ``thinking_item`` — it routes that into the
        # ``_ThinkingBatcher`` which coalesces consecutive chunks into
        # ~100 ms WebSocket frames. There is no ``thinking_delta``
        # handler; an earlier draft of this translator emitted that
        # invented type and every reasoning delta was silently dropped
        # into the response_processor's catch-all "OTHER" branch
        # (incident 2026-06-08: 296 reasoning summary deltas, zero
        # text visible in the Thinking panel). The batcher is designed
        # for repeated ``append_thinking`` calls — emitting one
        # thinking_item per delta is the streaming model.
        return [{
            "type": "run_item_stream_event",
            "item": {
                "type": "thinking_item",
                "content": delta,
            },
        }]

    if method == _METHOD_REASONING_SUMMARY_PART:
        # New "section header" added to the reasoning summary. Same
        # ``thinking_item`` event shape as the deltas above so the
        # batcher concatenates section headers inline with the
        # streamed text. Wrap in newlines so the UI separates
        # sections naturally.
        part = payload.get("text") or payload.get("part") or ""
        if not part:
            return []
        return [{
            "type": "run_item_stream_event",
            "item": {
                "type": "thinking_item",
                "content": f"\n{part}\n",
            },
        }]

    if method == _METHOD_COMMAND_OUTPUT_DELTA:
        # Bash output streams as it produces. v1 only delivers the
        # final aggregated_output at item.completed; v2 can additionally
        # surface live deltas. For now we drop these to keep the event
        # stream consistent with v1's shape — the completed-item still
        # carries the full output. Promote to a real event in a
        # follow-up commit once the frontend supports streaming tool
        # output rendering.
        return []

    if method == _METHOD_MCP_TOOL_PROGRESS:
        # MCP tool-call progress notifications. Same rationale as
        # command output — drop for now; the final result lands on
        # the item.completed event below.
        return []

    # ----------------------------------------------------------------
    # Item lifecycle — reuse v1 codex_cli translator's item helpers
    # since the ``item`` payload shape (``type``, ``id``, ``server``,
    # ``tool``, ``arguments``, ``result``, ``aggregated_output``, etc.)
    # is the same across exec mode and app-server mode — EXCEPT for
    # the ``type`` field's spelling: v1 emits snake_case
    # (``agent_message``, ``mcp_tool_call``, ``command_execution``),
    # v2 SDK emits camelCase (``agentMessage``, ``mcpToolCall``,
    # ``commandExecution``). Normalize at the boundary so the v1
    # helper's frozenset lookups still hit. Initial v2 commit shipped
    # without this normalizer → every item silently fell through to
    # "unknown — drop", so agent_message text never reached
    # response_processor (visible symptom: no_reply fallback every
    # turn even though the model produced output).
    # ----------------------------------------------------------------
    if method in (_METHOD_ITEM_STARTED, _METHOD_ITEM_COMPLETED):
        item = payload.get("item") or {}
        # ``item`` may be RootModel-serialized as nested ``{"root": {...}}``
        # depending on pydantic_dump options — unwrap once.
        if isinstance(item, dict) and "root" in item and isinstance(item["root"], dict):
            item = item["root"]
        # Normalize ``item.type`` camelCase → snake_case so the v1
        # helper's ``_CODEX_ITEM_TYPES_*`` frozensets match. Items
        # outside this table pass through unchanged (and v1's
        # forward-compat "unknown — drop" still applies).
        if isinstance(item, dict):
            raw_type = item.get("type")
            if isinstance(raw_type, str):
                normalized = _V2_ITEM_TYPE_TO_V1.get(raw_type, raw_type)
                if normalized != raw_type:
                    item = {**item, "type": normalized}
            # WebSearchThreadItem-specific normalization: SDK 0.1.0b3
            # emits ``item/started`` with ``query=""`` and only fills
            # the real search string on the inner ``action.query`` /
            # ``action.queries`` (i.e. the Responses-API action
            # object). The v1 helper reads top-level ``query`` so
            # the started-event tool_call_item renders ``{"query":""}``.
            # Hoist the search string up before the v1 helper sees
            # the item. ``action`` itself may be RootModel-wrapped
            # (``WebSearchAction.root: <variant>``); unwrap one level
            # if needed.
            if item.get("type") == "web_search" and not item.get("query"):
                action = item.get("action")
                if isinstance(action, dict):
                    inner = action.get("root", action)
                    if isinstance(inner, dict):
                        q = inner.get("query")
                        if not q:
                            queries = inner.get("queries")
                            if isinstance(queries, list) and queries:
                                q = ", ".join(str(x) for x in queries if x)
                        if q:
                            item = {**item, "query": q}
        # Reshape into the v1 codex_cli event shape and delegate to the
        # existing translator so we don't duplicate the per-item-type
        # branching (agent_message, reasoning, mcp_tool_call,
        # command_execution, web_search).
        if method == _METHOD_ITEM_STARTED:
            wrapped = {"type": "item.started", "item": item}
        else:
            wrapped = {"type": "item.completed", "item": item}
        return _codex_to_openai_agents(wrapped, streaming=streaming)

    # ----------------------------------------------------------------
    # Info-only notifications — log at DEBUG and drop.
    # Includes: config/warning, context-compacted, token-usage-updated,
    # account events, plan deltas (future), file changes (future),
    # terminal interaction (future), and unknown novel notifications.
    # ----------------------------------------------------------------
    logger.debug(
        f"[codex_official translator] dropping method={method!r} "
        f"(no UI mapping yet)"
    )
    return []
