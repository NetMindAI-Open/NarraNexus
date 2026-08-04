"""
@file_name: test_openai_compat_classify.py
@author: Bin Liang
@date: 2026-08-04
@description: _classify_event routing for reply tools whose text is blank.

The fifth consumer of extract_reply_text: once the blank guard makes an
all-citation reply extract to None, the reply-tool event must be DROPPED
— not fall through to the tool_call branch, which would leak the internal
MCP tool name (mcp__chat_module__send_message_to_user_directly) to
external OpenAI-compat clients as a fake tool call and trigger the
misleading "upstream LLM error" no-reply fallback.
"""
from __future__ import annotations

from backend.routes.openai_compat import _classify_event
from xyz_agent_context.channel.message_source_handler import (
    MessageSourceHandler,
)

HANDLER = MessageSourceHandler(
    name="chat",
    user_reply_tool_names=("send_message_to_user_directly",),
)


def _tool_event(tool_name: str, content: str) -> dict:
    return {
        "type": "progress",
        "details": {
            "tool_name": tool_name,
            "arguments": {"content": content},
        },
    }


def test_blank_reply_tool_event_is_dropped():
    """Reply tool fired but text strips to nothing (all-citation reply):
    the event maps to NO channel — neither content nor tool_call."""
    event = _tool_event(
        "mcp__chat_module__send_message_to_user_directly",
        "citeturn6view1\nciteturn6news2",
    )
    assert _classify_event(event, HANDLER) is None


def test_whitespace_reply_tool_event_is_dropped():
    event = _tool_event(
        "mcp__chat_module__send_message_to_user_directly", "\n"
    )
    assert _classify_event(event, HANDLER) is None


def test_real_reply_routes_to_content():
    event = _tool_event(
        "mcp__chat_module__send_message_to_user_directly", "Here you go."
    )
    assert _classify_event(event, HANDLER) == ("content", "Here you go.")


def test_non_reply_tool_still_routes_to_tool_call():
    event = _tool_event("mcp__skill_module__run_skill", "")
    kind, payload = _classify_event(event, HANDLER)
    assert kind == "tool_call"
    assert payload["name"] == "mcp__skill_module__run_skill"
