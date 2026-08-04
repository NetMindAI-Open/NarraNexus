"""
@file_name: test_bus_reply_judgment.py
@date: 2026-08-04
@description: "Did this turn deliver to whoever contacted the agent?" —
pinned at its REAL consumer, not just the handler data.

2026-08-01 briefing-squad incident: agents that DID deliver via
``bus_send_message`` were recorded NO-REPLY. The handler's
``user_reply_tool_names`` now carries the bus delivery tools, and the
live consumer is ChatModule's background-persistence split: a bus turn
that delivered to its origin writes a [DELIVERED-BG] activity row (and
an honest summary), only a genuinely silent turn writes [NO-REPLY-BG].
That split is the no-reply metric the fallback decision reads — round-2
review caught that after the owner-visible separation the expanded list
briefly had NO live consumer and the metric stayed poisoned.
"""
from __future__ import annotations

import xyz_agent_context.message_bus  # noqa: F401 — triggers registration
from xyz_agent_context.channel.message_source_handler import MessageSourceRegistry
from xyz_agent_context.module.chat_module.chat_module import ChatModule
from xyz_agent_context.schema import ProgressMessage
from xyz_agent_context.schema.runtime_message import ProgressStatus


def _handler():
    return MessageSourceRegistry.get("message_bus")


def _tool_progress(tool_name: str, content: str = "hello") -> ProgressMessage:
    return ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description=tool_name,
        status=ProgressStatus.COMPLETED,
        details={"tool_name": tool_name, "arguments": {"content": content}},
    )


# ---- the data: handler reply list -----------------------------------------


def test_bus_sends_count_as_delivery_tools():
    assert _handler().is_user_reply_tool("mcp__message_bus_module__bus_send_message")
    assert _handler().is_user_reply_tool("mcp__message_bus_module__bus_send_to_agent")
    assert _handler().is_user_reply_tool(
        "mcp__chat_module__send_message_to_user_directly"
    )


def test_non_delivery_bus_tools_do_not_count():
    assert not _handler().is_user_reply_tool(
        "mcp__message_bus_module__bus_get_messages"
    )


# ---- the live consumer: delivered-to-origin split -------------------------


def test_bus_delivery_is_recognized_at_the_persistence_split():
    delivered = ChatModule._delivered_to_origin(
        "message_bus",
        [_tool_progress("mcp__message_bus_module__bus_send_message", "peer reply")],
    )
    assert delivered is True


def test_silent_bus_turn_is_not_delivered():
    delivered = ChatModule._delivered_to_origin(
        "message_bus",
        [_tool_progress("mcp__message_bus_module__bus_get_messages", "")],
    )
    assert delivered is False


def test_activity_summary_is_honest_about_delivery():
    meta = {"channel_tag": {"sender_name": "Maestro"}}
    delivered = ChatModule._build_activity_summary(
        "message_bus", meta, delivered_to_origin=True
    )
    silent = ChatModule._build_activity_summary(
        "message_bus", meta, delivered_to_origin=False
    )
    assert "Replied" in delivered
    assert "Replied" not in silent  # the old unconditional "Replied to X" lied
    assert "no reply" in silent.lower()
