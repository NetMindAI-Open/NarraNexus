"""
@file_name: event_adapter.py
@author: Bin Liang
@date: 2026-07-29
@description: LegacyEventAdapter — the ONE place that speaks the legacy
dict event contract (``agent_framework.loop.events``).

Inside the framework everything is a typed ``LoopEvent``; the legacy
shapes are produced here and nowhere else, so grey-release coexistence
with the claude/codex drivers costs the platform zero changes.

Semantic mapping — this is where the monologue/expression contract
becomes visible to users:

  text/thinking deltas   -> thinking_item
      Our plain text is PRIVATE reasoning, never a reply. Mapping it to
      the legacy "assistant text" channel would show the user raw
      internal monologue as if it were an answer.
  expression arg deltas  -> response.reply.delta
      The reply lives in an expression tool's argument, so streaming
      that argument IS streaming the reply — the user reads the answer
      as the model writes it, before the tool call completes.
  other arg deltas       -> dropped (internal presentation detail)
  plan events            -> plan_item

The completed ``tool_call_item`` still carries the authoritative final
text (idempotent by call_id), so a consumer that ignores the streaming
shapes loses nothing.
"""

from __future__ import annotations

from typing import Any

from xyz_agent_context.agent_framework.loop.events import (
    DATA_TYPE_DONE,
    DATA_TYPE_ERROR,
    DATA_TYPE_REPLY_DELTA,
    DATA_TYPE_USAGE,
    ITEM_TYPE_PLAN,
    ITEM_TYPE_THINKING,
    ITEM_TYPE_TOOL_CALL,
    ITEM_TYPE_TOOL_CALL_OUTPUT,
    TYPE_RAW_RESPONSE_EVENT,
    TYPE_RUN_ITEM_STREAM_EVENT,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.errors import (
    LEGACY_SAFE_ERROR_TYPES,
    ErrorType,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.events import (
    TYPE_ERROR,
    TYPE_PLAN,
    TYPE_STEP_DONE,
    TYPE_TEXT_DELTA,
    TYPE_THINKING_DELTA,
    TYPE_TOOL_ARG_DELTA,
    TYPE_TOOL_RESULT,
    TYPE_TOOL_USE,
    TYPE_TOOL_USE_START,
    TYPE_TURN_DONE,
    LoopEvent,
)


class LegacyEventAdapter:
    """LoopEvent → 0..N legacy dict events."""

    def translate(self, event: LoopEvent) -> list[dict[str, Any]]:
        etype = event.type
        payload = event.payload
        if etype in (TYPE_TEXT_DELTA, TYPE_THINKING_DELTA):
            # Monologue: the user watches the agent think, not speak.
            item: dict[str, Any] = {
                "type": ITEM_TYPE_THINKING,
                "content": payload["text"],
            }
            if etype == TYPE_TEXT_DELTA:
                # Monologue text is this framework's analogue of the
                # claude drivers' assistant text, and the platform's
                # reasoning channel (final_output -> meta_data.reasoning
                # -> next turn's <my_reasoning>) is fed from that
                # analogue. The flag lets the response processor route
                # monologue into final_output while it still DISPLAYS
                # as thinking. Provider CoT (thinking_delta) stays
                # unstamped — CoT never enters final_output on any
                # driver.
                item["monologue"] = True
            return [{"type": TYPE_RUN_ITEM_STREAM_EVENT, "item": item}]
        if etype == TYPE_TOOL_ARG_DELTA:
            if not payload.get("expressive"):
                return []  # non-reply arguments stay internal
            return [
                {
                    "type": TYPE_RAW_RESPONSE_EVENT,
                    "data": {
                        "type": DATA_TYPE_REPLY_DELTA,
                        "delta": payload["text"],
                        "call_id": payload.get("call_id", ""),
                        "tool_name": payload.get("tool_name", ""),
                    },
                }
            ]
        if etype == TYPE_TOOL_USE_START:
            # Name-first pending call: same legacy item shape (and the
            # same "arguments" key) as the completed call, so consumers
            # replace it in place by tool_call_id instead of learning a
            # new message type.
            return [
                {
                    "type": TYPE_RUN_ITEM_STREAM_EVENT,
                    "item": {
                        "type": ITEM_TYPE_TOOL_CALL,
                        "tool_call_id": payload.get("call_id", ""),
                        "tool_name": payload.get("tool_name", ""),
                        "arguments": {},
                        "pending": True,
                    },
                }
            ]
        if etype == TYPE_TOOL_USE:
            return [
                {
                    "type": TYPE_RUN_ITEM_STREAM_EVENT,
                    "item": {
                        "type": ITEM_TYPE_TOOL_CALL,
                        "tool_call_id": payload["call_id"],
                        "tool_name": payload["tool_name"],
                        "arguments": payload.get("args") or {},
                    },
                }
            ]
        if etype == TYPE_TOOL_RESULT:
            return [
                {
                    "type": TYPE_RUN_ITEM_STREAM_EVENT,
                    "item": {
                        "type": ITEM_TYPE_TOOL_CALL_OUTPUT,
                        "tool_call_id": payload["call_id"],
                        "output": payload.get("content") or "",
                        "status": "completed" if payload.get("ok") else "failed",
                    },
                }
            ]
        if etype == TYPE_PLAN:
            return [
                {
                    "type": TYPE_RUN_ITEM_STREAM_EVENT,
                    "item": {
                        "type": ITEM_TYPE_PLAN,
                        "steps": payload.get("steps") or [],
                        "note": payload.get("note", ""),
                    },
                }
            ]
        if etype == TYPE_STEP_DONE:
            if event.usage is None:
                return []
            return [
                {
                    "type": TYPE_RAW_RESPONSE_EVENT,
                    "data": {
                        "type": DATA_TYPE_USAGE,
                        "usage": event.usage.as_legacy_dict(),
                    },
                }
            ]
        if etype == TYPE_ERROR:
            error_type = str(payload.get("error_type", ErrorType.UNKNOWN.value))
            if error_type not in LEGACY_SAFE_ERROR_TYPES:
                error_type = ErrorType.INVALID_REQUEST.value
            return [
                {
                    "type": TYPE_RAW_RESPONSE_EVENT,
                    "data": {
                        "type": DATA_TYPE_ERROR,
                        "error_message": str(payload.get("message", "")),
                        "error_type": error_type,
                    },
                }
            ]
        if etype == TYPE_TURN_DONE:
            usage = event.usage.as_legacy_dict() if event.usage else {}
            return [
                {
                    "type": TYPE_RAW_RESPONSE_EVENT,
                    "data": {
                        "type": DATA_TYPE_DONE,
                        "usage": usage,
                        "stop_reason": str(payload.get("end_reason", "")).lower(),
                        "model": payload.get("model", ""),
                        "num_turns": payload.get("num_steps", 0),
                        # Priced by the loop (litellm's cost map); absent
                        # when the model has no known price, which the
                        # platform records as "no price" rather than $0.
                        **(
                            {"total_cost_usd": payload["cost_usd"]}
                            if payload.get("cost_usd") is not None
                            else {}
                        ),
                    },
                }
            ]
        # compaction has no legacy shape (typed-stream consumers only).
        return []
