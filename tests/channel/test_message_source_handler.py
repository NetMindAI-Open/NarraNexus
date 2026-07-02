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


def test_extract_reply_text_strips_citeturn_tokens():
    """OpenAI Responses-API ``citeturnNviewN`` / ``citeturnNnewsN``
    tokens that gpt-5.5 emits when WebSearch ran are stripped at the
    reply-extraction layer (so users see clean prose, not literal
    cryptic markers). Verified format from incident 2026-06-08:
    tokens are concatenated to sentence ends with no whitespace."""
    from xyz_agent_context.channel.message_source_handler import (
        MessageSourceHandler,
    )

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("send_message_to_user_directly",),
    )
    raw = "6月7日全国高考开考，今年报名人数1290万人。citeturn6view1"
    out = h.extract_reply_text(
        "mcp__chat_module__send_message_to_user_directly",
        {"content": raw},
    )
    assert out == "6月7日全国高考开考，今年报名人数1290万人。"


def test_extract_reply_text_strips_multiple_tokens_across_paragraphs():
    """Several tokens in one reply (with whitespace between them after
    strip) get the leftover spaces tidied up."""
    from xyz_agent_context.channel.message_source_handler import (
        MessageSourceHandler,
    )

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("send_message_to_user_directly",),
    )
    raw = "新华社/央视消息称习近平抵达平壤。citeturn6view0 citeturn2news12"
    out = h.extract_reply_text(
        "mcp__chat_module__send_message_to_user_directly",
        {"content": raw},
    )
    # Both tokens gone; the whitespace between them collapses and the
    # trailing space before the period (if any) is fixed.
    assert "citeturn" not in out
    assert out == "新华社/央视消息称习近平抵达平壤。"


def test_extract_reply_text_preserves_text_without_tokens():
    """Fast-path: if no ``cite`` substring appears at all, the text is
    returned unchanged (no regex sweep, no whitespace mutation)."""
    from xyz_agent_context.channel.message_source_handler import (
        MessageSourceHandler,
    )

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("send_message_to_user_directly",),
    )
    raw = "Hi there!  Multiple spaces  stay  intact."
    out = h.extract_reply_text(
        "mcp__chat_module__send_message_to_user_directly",
        {"content": raw},
    )
    assert out == raw  # NOT modified — fast-path kept doubled spaces


def test_extract_reply_text_does_not_match_word_cite():
    """The regex requires two alpha+digit cycles after ``cite``, so the
    English word "cite" used in ordinary prose (e.g. "Please cite the
    source") survives intact."""
    from xyz_agent_context.channel.message_source_handler import (
        MessageSourceHandler,
    )

    h = MessageSourceHandler(
        name="chat",
        user_reply_tool_names=("send_message_to_user_directly",),
    )
    raw = "Please cite the relevant section in your write-up."
    out = h.extract_reply_text(
        "mcp__chat_module__send_message_to_user_directly",
        {"content": raw},
    )
    assert out == raw


def test_extract_reply_text_strips_tokens_through_custom_extractor():
    """The strip applies AFTER any custom ``extract_reply_fn`` — so
    channels with non-standard reply tooling (Lark's --markdown flag,
    Slack/Telegram CLI wrappers, etc.) also get clean text without
    each having to implement the strip themselves."""
    from xyz_agent_context.channel.message_source_handler import (
        MessageSourceHandler,
    )

    def lark_extractor(tool_name, arguments):
        # Pretend this is Lark's --markdown extraction
        return arguments.get("markdown")

    h = MessageSourceHandler(
        name="lark",
        user_reply_tool_names=("lark_cli +messages-send",),
        extract_reply_fn=lark_extractor,
    )
    out = h.extract_reply_text(
        "lark_cli +messages-send",
        {"markdown": "中朝外交：习近平抵达平壤citeturn6view0"},
    )
    assert out == "中朝外交：习近平抵达平壤"


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
