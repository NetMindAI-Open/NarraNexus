"""
@file_name: test_claude_reply_reminder.py
@date: 2026-08-04
@description: The claude CLI adapter renders the platform-declared reply
surface as a short reminder at the end of the per-turn user message.

NexusPower repeats the reply rule near the generation point every step;
the CLI adapter has no per-step seam, so the end of the turn's user
message is the closest available position. Without it, the only reply
instruction a CLI-driven agent sees sits in the distant system prompt —
the exact far-from-generation failure the NexusPower tail was built to
fix. The reminder is rendered from TurnInput.expressive_tools (the same
declaration NexusPower consumes), so both frameworks speak from one
source of truth.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.adapters.claude.prompts import (
    append_reply_reminder,
)

TOOLS = (
    "mcp__message_bus_module__bus_send_message",
    "mcp__chat_module__send_message_to_user_directly",
)


def test_appends_reminder_with_all_tools_in_declared_order():
    out = append_reply_reminder("hello", TOOLS)
    assert out.startswith("hello")
    body = out[len("hello"):]
    first = body.index("mcp__message_bus_module__bus_send_message")
    second = body.index("mcp__chat_module__send_message_to_user_directly")
    assert first < second


def test_reminder_states_the_delivery_contract():
    out = append_reply_reminder("hello", TOOLS)
    assert "plain" in out.lower()  # plain text is not delivered
    assert "outrank" in out.lower()  # message-borne instruction wins


def test_no_declaration_leaves_message_untouched():
    assert append_reply_reminder("hello", ()) == "hello"
    assert append_reply_reminder("hello", None) == "hello"
