"""
@file_name: test_message_source_handler.py
@author: Bin Liang
@date: 2026-05-11
@description: Contract tests for MessageSourceHandler + MessageSourceRegistry.

Behaviour pinned:
1. A handler can be registered against a working_source value.
2. Duplicate registration raises (force protects against accidental re-registration).
3. Unknown working_source falls back to the default handler.
4. The default handler covers `chat`/`a2a`/`callback`/`skill_study` triggers
   without explicit registration.
5. `format_row_prefix` substitutes meta_data + channel_tag fields into the template.
6. `is_user_reply_tool` matches tool names by `<pattern> in tool_name` so the
   MCP-prefixed form (`mcp__chat_module__send_message_to_user_directly`)
   still matches the pattern `send_message_to_user_directly`.
7. Registry dump returns a JSON-serializable snapshot for debugging.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_registry():
    """Each test gets a clean registry — registration is global state."""
    from xyz_agent_context.channel.message_source_handler import MessageSourceRegistry
    MessageSourceRegistry._handlers.clear()  # type: ignore[attr-defined]
    yield
    MessageSourceRegistry._handlers.clear()  # type: ignore[attr-defined]


def test_register_and_get_returns_handler():
    from xyz_agent_context.channel.message_source_handler import (
        MessageSourceHandler,
        MessageSourceRegistry,
    )

    h = MessageSourceHandler(
        name="lark",
        user_reply_tool_names=("send_message_to_user_directly", "lark_cli +messages-send"),
        row_prefix_template="[Lark · {sender_name}]",
    )
    MessageSourceRegistry.register(h)

    got = MessageSourceRegistry.get("lark")
    assert got is h


def test_get_unknown_source_returns_default_handler():
    from xyz_agent_context.channel.message_source_handler import MessageSourceRegistry

    default = MessageSourceRegistry.get("definitely_not_registered_xyz")
    # Default handler always recognises send_message_to_user_directly so
    # chat/a2a/callback/skill_study don't need explicit registration.
    assert "send_message_to_user_directly" in default.user_reply_tool_names


def test_duplicate_registration_raises():
    from xyz_agent_context.channel.message_source_handler import (
        MessageSourceHandler,
        MessageSourceRegistry,
    )

    h1 = MessageSourceHandler(name="lark", user_reply_tool_names=("a",))
    h2 = MessageSourceHandler(name="lark", user_reply_tool_names=("b",))
    MessageSourceRegistry.register(h1)
    with pytest.raises(ValueError, match="duplicate"):
        MessageSourceRegistry.register(h2)


def test_is_user_reply_tool_matches_mcp_prefixed_names():
    """Tool names from MCP arrive as e.g.
    `mcp__chat_module__send_message_to_user_directly`. The handler must
    match its registered short name as a substring so we don't have to
    enumerate every MCP-prefixed variant."""
    from xyz_agent_context.channel.message_source_handler import MessageSourceHandler

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("send_message_to_user_directly",),
    )
    assert h.is_user_reply_tool("mcp__chat_module__send_message_to_user_directly")
    assert h.is_user_reply_tool("send_message_to_user_directly")
    assert not h.is_user_reply_tool("get_chat_history")
    assert not h.is_user_reply_tool("")


def test_is_user_reply_tool_matches_multiple_patterns():
    from xyz_agent_context.channel.message_source_handler import MessageSourceHandler

    h = MessageSourceHandler(
        name="lark",
        user_reply_tool_names=(
            "send_message_to_user_directly",
            "lark_cli +messages-send",
            "lark_cli +messages-reply",
        ),
    )
    assert h.is_user_reply_tool("mcp__chat_module__send_message_to_user_directly")
    assert h.is_user_reply_tool("mcp__lark_module__lark_cli +messages-send")
    assert h.is_user_reply_tool("lark_cli +messages-reply")
    assert not h.is_user_reply_tool("lark_cli +messages-list")


def test_format_row_prefix_substitutes_meta_and_channel_tag():
    from xyz_agent_context.channel.message_source_handler import MessageSourceHandler

    h = MessageSourceHandler(
        name="lark",
        user_reply_tool_names=("send_message_to_user_directly",),
        row_prefix_template="[Lark · {sender_name} in {room_name}]",
    )
    msg = {
        "role": "assistant",
        "content": "hi",
        "meta_data": {
            "working_source": "lark",
            "channel_tag": {
                "sender_name": "Loki",
                "room_name": "顺风耳, Loki, 阿良",
            },
        },
    }
    out = h.format_row_prefix(msg)
    assert "Lark" in out
    assert "Loki" in out
    assert "顺风耳, Loki, 阿良" in out


def test_format_row_prefix_missing_fields_falls_back_gracefully():
    """Template references {sender_name} but channel_tag is missing —
    must not crash; should leave the placeholder empty or substitute a
    safe default."""
    from xyz_agent_context.channel.message_source_handler import MessageSourceHandler

    h = MessageSourceHandler(
        name="lark",
        user_reply_tool_names=("send_message_to_user_directly",),
        row_prefix_template="[Lark · {sender_name}]",
    )
    msg = {"role": "user", "content": "hi", "meta_data": {"working_source": "lark"}}
    out = h.format_row_prefix(msg)
    # Either the missing field becomes "" / "?" or the line still parses
    # — what we must NOT do is raise KeyError.
    assert "Lark" in out


def test_default_handler_renders_chat_ui_prefix():
    from xyz_agent_context.channel.message_source_handler import MessageSourceRegistry

    msg = {
        "role": "user",
        "content": "hi",
        "meta_data": {"working_source": "chat", "user_id": "binliang"},
    }
    h = MessageSourceRegistry.get("chat")
    out = h.format_row_prefix(msg)
    assert "binliang" in out or "NarraNexus" in out or "Chat" in out


def test_extract_reply_text_default_returns_content_arg():
    """Default extractor: tool_name matches user_reply_tool_names AND
    arguments has a `content` field → return that content."""
    from xyz_agent_context.channel.message_source_handler import MessageSourceHandler

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("send_message_to_user_directly",),
    )
    out = h.extract_reply_text(
        "mcp__chat_module__send_message_to_user_directly",
        {"content": "Hello user"},
    )
    assert out == "Hello user"


def test_extract_reply_text_default_returns_none_for_unmatched_tool():
    from xyz_agent_context.channel.message_source_handler import MessageSourceHandler

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("send_message_to_user_directly",),
    )
    assert h.extract_reply_text("get_chat_history", {"x": 1}) is None
    assert h.extract_reply_text("", {}) is None


def test_extract_reply_text_custom_fn_overrides_default():
    """A handler with `extract_reply_fn` can implement non-standard
    extraction. This is the Lark path: tool_name = 'lark_cli', the reply
    text sits inside `arguments['command']` as a `--markdown` flag."""
    from xyz_agent_context.channel.message_source_handler import MessageSourceHandler

    def lark_extract(tool_name, args):
        if "lark_cli" not in tool_name:
            return None
        cmd = args.get("command", "")
        if "+messages-send" not in cmd:
            return None
        # Toy parser: find the --markdown literal
        if "--markdown" in cmd:
            return cmd.split("--markdown", 1)[1].strip().strip('"')
        return None

    h = MessageSourceHandler(
        name="lark",
        user_reply_tool_names=("lark_cli",),
        extract_reply_fn=lark_extract,
    )
    # lark_cli send command → extracted
    out = h.extract_reply_text(
        "mcp__lark_module__lark_cli",
        {"command": 'im +messages-send --chat-id oc_x --markdown "你好啊"'},
    )
    assert out == '你好啊"'.rstrip('"')

    # lark_cli for a non-send command → no reply
    out2 = h.extract_reply_text(
        "lark_cli", {"command": "im +messages-list --chat-id oc_x"}
    )
    assert out2 is None


def test_dump_returns_serializable_snapshot():
    from xyz_agent_context.channel.message_source_handler import (
        MessageSourceHandler,
        MessageSourceRegistry,
    )
    import json

    MessageSourceRegistry.register(MessageSourceHandler(
        name="lark",
        user_reply_tool_names=("lark_cli +messages-send",),
        row_prefix_template="[Lark · {sender_name}]",
    ))
    snapshot = MessageSourceRegistry.dump()
    # Must be JSON-serialisable for debug logging.
    json.dumps(snapshot)
    assert "lark" in snapshot
