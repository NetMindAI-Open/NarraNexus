"""
@file_name: run_collector.py
@author: Bin Liang
@date: 2026-04-20
@description: Shared collection helper for consumers of AgentRuntime.run().

Historically each trigger (LarkTrigger, JobTrigger, MessageBusTrigger,
ChatTrigger A2A) wrote its own ``async for msg in runtime.run(...)``
loop. That caused two bugs:

  1. Each loop only handled ``MessageType.AGENT_RESPONSE`` — ``ERROR``
     messages were silently dropped (Bug 2 surface symptom on Lark).
  2. Each loop re-implemented the same "accumulate deltas / track
     tool calls / capture raw payloads" logic slightly differently.

This module provides a single ``collect_run`` helper that reads every
message type once and returns a structured ``RunCollection``. Each
trigger only has to implement its own policy for displaying / logging
the error when ``result.is_error`` is true. A new trigger (Telegram,
Slack, Discord, ...) can adopt the same pattern with zero risk of
re-introducing the silent-drop bug.

Used by:
  - module/lark_module/lark_trigger.py (LarkTrigger._build_and_run_agent)
  - module/job_module/job_trigger.py (JobTrigger)
  - message_bus/message_bus_trigger.py (MessageBusTrigger)
  - module/chat_module/chat_trigger.py (ChatTrigger A2A handler)

Not used by the WebSocket route (``backend/routes/websocket.py``): that
path streams messages to the frontend live instead of collecting them,
and the frontend already knows how to render every message type.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from xyz_agent_context.schema.runtime_message import MessageType


@dataclass(frozen=True)
class RunError:
    """A failure surfaced by AgentRuntime via a ``MessageType.ERROR`` event.

    Attributes:
        error_type: Concrete class name of the underlying exception,
            preserved by AgentRuntime so consumers can branch on it
            (e.g. show a friendlier text for
            ``SystemDefaultUnavailable`` than for a generic CLI crash).
        error_message: Human-readable explanation. May be surfaced to
            the owner in web chat verbatim, or replaced by a friendlier
            text for IM channels where the sender is not the owner.
    """

    error_type: str
    error_message: str


@dataclass
class RunCollection:
    """Result of consuming one ``AgentRuntime.run()`` invocation."""

    output_text: str = ""
    """Concatenation of every ``AGENT_RESPONSE.delta`` in arrival order."""

    tool_calls: list[str] = field(default_factory=list)
    """Names of tools invoked by the agent, in arrival order."""

    raw_items: list[Any] = field(default_factory=list)
    """The ``.raw`` payload of every message that had one. LarkTrigger
    uses this to extract the exact text the agent sent via
    ``lark_cli im +messages-send``."""

    error: Optional[RunError] = None
    """``None`` when the run succeeded; a ``RunError`` when AgentRuntime
    yielded one or more ``ERROR`` messages. If multiple errors arrived
    the last one wins (callers get the most specific failure)."""

    @property
    def is_error(self) -> bool:
        return self.error is not None


async def collect_run(
    runtime,
    *,
    agent_id: str,
    user_id: str,
    input_content: str,
    working_source,
    on_progress: Optional[Callable[[str, Optional[str]], Awaitable[None]]] = None,
    **extra_kwargs,
) -> RunCollection:
    """Drive ``runtime.run(...)`` to completion and group its output.

    Any keyword argument accepted by ``AgentRuntime.run`` can be passed
    through ``extra_kwargs`` (e.g. ``trigger_extra_data``,
    ``job_instance_id``, ``forced_narrative_id``, ``pass_mcp_servers``,
    ``cancellation``).

    ``on_progress(kind, tool_name)`` — optional, opt-in — is awaited once per
    observed message with ``kind`` in {"thinking","tool","response","error"}
    (``tool_name`` set only for "tool"). Used to mirror a live "what is this
    agent doing" status (e.g. the team-chat activity view). It must never raise;
    any exception is swallowed so status reporting can't break the run.
    """
    text_parts: list[str] = []
    tool_calls: list[str] = []
    raw_items: list[Any] = []
    error: Optional[RunError] = None
    # Dedup synthesized tool_call_items by (tool_name, arguments_json). With
    # include_partial_messages=True the same ToolUseBlock can surface across
    # multiple AssistantMessage frames — the SDK dedups by tool_call_id, but
    # that id isn't propagated into the ProgressMessage we observe here.
    # Dedup defensively so Lark doesn't echo the same reply twice in the inbox.
    import json as _json
    seen_tool_calls: set[str] = set()

    async for msg in runtime.run(
        agent_id=agent_id,
        user_id=user_id,
        input_content=input_content,
        working_source=working_source,
        **extra_kwargs,
    ):
        mt = getattr(msg, "message_type", None)
        if mt == MessageType.AGENT_RESPONSE:
            delta = getattr(msg, "delta", None)
            if delta:
                text_parts.append(delta)
        elif mt == MessageType.TOOL_CALL:
            name = getattr(msg, "tool_name", None)
            if name:
                tool_calls.append(name)
        elif mt == MessageType.ERROR:
            # Last error wins — keep the most specific failure the run
            # reached (typically there's only one, but AgentRuntime may
            # yield a generic + specific pair in edge cases).
            error = RunError(
                error_type=getattr(msg, "error_type", "unknown"),
                error_message=getattr(msg, "error_message", str(msg)),
            )

        # Raw payload on any message type (Lark needs it from TOOL_CALL
        # events; other triggers simply ignore the list).
        raw = getattr(msg, "raw", None)
        if raw is not None:
            raw_items.append(raw)
        else:
            # Tool calls arrive as ProgressMessage with details.tool_name;
            # there's no raw attribute. Synthesize one in the shape Lark's
            # extractor expects so inbox rows get the real reply instead of
            # the "(Replied on Lark)" fallback.
            details = getattr(msg, "details", None)
            if isinstance(details, dict) and details.get("tool_name"):
                tool_name = details["tool_name"]
                arguments = details.get("arguments", {})
                try:
                    args_key = _json.dumps(arguments, sort_keys=True, default=str)
                except Exception:
                    args_key = repr(arguments)
                dedup_key = f"{tool_name}::{args_key}"
                if dedup_key in seen_tool_calls:
                    continue
                seen_tool_calls.add(dedup_key)
                raw_items.append({
                    "item": {
                        "type": "tool_call_item",
                        "tool_name": tool_name,
                        "arguments": arguments,
                    }
                })
                if tool_name not in tool_calls:
                    tool_calls.append(tool_name)

        # Live progress mirror (opt-in). Detect the tool name from either the
        # typed attr or the ProgressMessage details, then classify the phase.
        if on_progress is not None:
            _tool = getattr(msg, "tool_name", None)
            if not _tool:
                _d = getattr(msg, "details", None)
                if isinstance(_d, dict):
                    _tool = _d.get("tool_name")
            kind = (
                "tool" if _tool
                else "thinking" if mt == MessageType.AGENT_THINKING
                else "response" if mt == MessageType.AGENT_RESPONSE
                else "error" if mt == MessageType.ERROR
                else None
            )
            if kind:
                try:
                    await on_progress(kind, _tool)
                except Exception:  # noqa: BLE001 — status must never break the run
                    pass

    return RunCollection(
        output_text="".join(text_parts),
        tool_calls=tool_calls,
        raw_items=raw_items,
        error=error,
    )
