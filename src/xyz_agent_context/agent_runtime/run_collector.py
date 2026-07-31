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

from loguru import logger

from xyz_agent_context.agent_framework.loop.events import ITEM_TYPE_TOOL_CALL
from xyz_agent_context.schema.runtime_message import MessageType


@dataclass(frozen=True)
class RunError:
    """A failure surfaced by AgentRuntime via a ``MessageType.ERROR`` event.

    Attributes:
        error_type: Concrete class name of the underlying exception,
            preserved by AgentRuntime so consumers can branch on it
            (e.g. show a friendlier text for
            ``LLMConfigNotConfigured`` than for a generic CLI crash).
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
    """What the agent said, in arrival order: every ``AGENT_RESPONSE.delta``,
    plus — only when the caller opted in via ``include_monologue`` — every
    ``AGENT_THINKING.monologue`` segment (NexusPower's assistant plain text,
    which streams as thinking under the monologue contract)."""

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

    event_id: Optional[str] = None
    """``events`` row id of this turn, captured from the Step-0 progress
    message (``details.event_id``). None if the run died before Step 0
    completed. Lets bus consumers link the turn to its persisted
    event_log without touching the runtime."""

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
    on_event_id: Optional[Callable[[str], Awaitable[None]]] = None,
    include_monologue: bool = False,
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

    ``on_event_id(event_id)`` — optional, opt-in — is awaited at most once, as
    soon as the Step-0 progress message's ``details.event_id`` is observed.
    Used to bind the turn's events-row id onto a live status row (e.g.
    ``TurnActivity.note_event_id``) as soon as it's known, rather than waiting
    for the whole run to finish. Like ``on_progress``, it must never raise;
    any exception is swallowed so status reporting can't break the run.

    ``include_monologue`` — opt-in — folds NexusPower monologue segments
    (``AGENT_THINKING.monologue``) into ``output_text``. ONLY for callers
    whose prompt tells the agent its plain text is delivered (today: bus
    team rooms, whose replies auto-post to the shared room). Everywhere
    else the monologue contract promises the agent its plain text is
    private; relaying it to an inbox or an A2A response would leak
    deliberation the agent never addressed to anyone.
    """
    text_parts: list[str] = []
    tool_calls: list[str] = []
    raw_items: list[Any] = []
    error: Optional[RunError] = None
    event_id: Optional[str] = None
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
        # NexusPower: the agent's plain text streams as thinking with the
        # ``monologue`` subset set — the assistant text the claude driver
        # would emit as AGENT_RESPONSE. Only meaningful when the caller
        # opted in (see include_monologue docstring); provider CoT arrives
        # with monologue="" and never counts.
        monologue = (
            getattr(msg, "monologue", "")
            if include_monologue and mt == MessageType.AGENT_THINKING
            else ""
        )
        if mt == MessageType.AGENT_RESPONSE:
            delta = getattr(msg, "delta", None)
            if delta:
                text_parts.append(delta)
        elif mt == MessageType.AGENT_THINKING:
            if monologue:
                text_parts.append(monologue)
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
                        "type": ITEM_TYPE_TOOL_CALL,
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
            # A monologue-carrying thinking frame is the agent SPEAKING —
            # but only on opted-in surfaces, where that text really is
            # delivered (``monologue`` is already "" otherwise). Report it
            # as "response" so activity views don't show "thinking" while
            # the room reply is being written.
            kind = (
                "tool" if _tool
                else "response" if (
                    mt == MessageType.AGENT_RESPONSE or monologue
                )
                else "thinking" if mt == MessageType.AGENT_THINKING
                else "error" if mt == MessageType.ERROR
                else None
            )
            if kind:
                try:
                    await on_progress(kind, _tool)
                except Exception:  # noqa: BLE001 — status must never break the run
                    pass

        # Step-0 event_id capture (opt-in via on_event_id). Fires at most once —
        # the first Step-0 completion wins, and it's the only place this id
        # is ever surfaced.
        if event_id is None:
            details = getattr(msg, "details", None)
            candidate = details.get("event_id") if isinstance(details, dict) else None
            if candidate:
                event_id = str(candidate)
                if on_event_id is not None:
                    try:
                        await on_event_id(event_id)
                    except Exception:  # noqa: BLE001 — status must never break the run
                        logger.opt(exception=True).warning("on_event_id callback failed")

    return RunCollection(
        output_text="".join(text_parts),
        tool_calls=tool_calls,
        raw_items=raw_items,
        error=error,
        event_id=event_id,
    )
