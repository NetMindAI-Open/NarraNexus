"""
@file_name: test_owner_visible_delivery.py
@date: 2026-08-04
@description: "Delivered to whoever contacted you" and "visible in the
owner's web chat" are two different predicates, and the session anchor
must only follow the second.

PR #230 review finding: expanding the bus handler's
``user_reply_tool_names`` (the delivered-to-origin list) silently flowed
into ``step_4._turn_delivered_user_message``, whose contract is
owner-chat visibility — every agent-to-agent bus reply would re-anchor
the OWNER's session onto the bus errand's narrative and clear
``last_query``. These tests pin the split: the handler exposes an
owner-visible subset (``owner_visible_reply_tool_names``, defaulting to
the full reply list for handlers whose channel IS the owner's surface),
and step_4 consumes only that.
"""
from __future__ import annotations

import xyz_agent_context.message_bus  # noqa: F401 — registers the bus handler
from xyz_agent_context.agent_runtime._agent_runtime_steps.step_4_persist_results import (
    _turn_delivered_user_message,
)
from xyz_agent_context.channel.message_source_handler import MessageSourceRegistry
from xyz_agent_context.schema import ProgressMessage
from xyz_agent_context.schema.runtime_message import ProgressStatus


def _tool_progress(tool_name: str, content: str = "hello") -> ProgressMessage:
    return ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description=tool_name,
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": tool_name,
            "arguments": {"content": content},
        },
    )


# ---- handler-level contract ----------------------------------------------


def test_bus_handler_counts_bus_send_as_delivered_but_not_owner_visible():
    h = MessageSourceRegistry.get("message_bus")
    assert h.is_user_reply_tool("mcp__message_bus_module__bus_send_message")
    assert not h.is_owner_visible_reply_tool(
        "mcp__message_bus_module__bus_send_message"
    )
    assert h.is_owner_visible_reply_tool(
        "mcp__chat_module__send_message_to_user_directly"
    )


def test_owner_visible_defaults_to_full_reply_list_for_im_handlers():
    """For IM channels the conversation IS with the owner — their channel
    tools stay owner-visible via the None-fallback."""
    h = MessageSourceRegistry.get("wechat")
    assert h.is_owner_visible_reply_tool("mcp__wechat_module__wechat_send")


def test_extract_owner_visible_text_gates_on_owner_list():
    h = MessageSourceRegistry.get("message_bus")
    assert (
        h.extract_owner_visible_text(
            "mcp__message_bus_module__bus_send_to_agent", {"content": "peer reply"}
        )
        is None
    )
    assert (
        h.extract_owner_visible_text(
            "mcp__chat_module__send_message_to_user_directly", {"content": "hi owner"}
        )
        == "hi owner"
    )


# ---- step_4 anchor predicate ---------------------------------------------


def test_bus_only_delivery_does_not_count_as_user_message():
    """A bus turn whose only delivery went to a peer agent must NOT flip
    the proactive-delivery branch — the owner saw nothing."""
    responses = [
        _tool_progress("mcp__message_bus_module__bus_send_to_agent"),
        _tool_progress("mcp__message_bus_module__bus_send_message"),
    ]
    assert _turn_delivered_user_message(responses, "message_bus") is False


def test_owner_relay_still_counts_as_user_message():
    responses = [_tool_progress("mcp__chat_module__send_message_to_user_directly")]
    assert _turn_delivered_user_message(responses, "message_bus") is True
