"""
@file_name: test_telegram_message_source.py
@date: 2026-05-13
@description: Tests for _extract_telegram_reply — the
MessageSourceRegistry extractor that lets ChatModule capture Telegram
agent replies into long-term memory instead of "Background activity
(telegram)" placeholders.

See the slack equivalent test file for the full rationale; this is the
parallel coverage for Telegram, which uses
``tg_cli(method="sendMessage", args={"text": "..."})`` as its reply
path. Same pattern, different tool name + method name.
"""
from __future__ import annotations

import json

from narranexus_plugins.telegram_module.telegram_module import (
    _extract_telegram_reply,
)


# ── canonical reply via tg_cli + sendMessage ───────────────────────────


def test_extracts_text_from_tg_cli_send_message():
    """The hot path: agent calls tg_cli with method=sendMessage and a
    text body. Reply text must come out verbatim."""
    out = _extract_telegram_reply(
        "mcp__telegram_module__tg_cli",
        {
            "agent_id": "agent_a",
            "method": "sendMessage",
            "args": {"chat_id": "8612707834", "text": "Hi there"},
        },
    )
    assert out == "Hi there"


def test_extracts_text_when_args_arrived_as_json_string():
    out = _extract_telegram_reply(
        "mcp__telegram_module__tg_cli",
        {
            "method": "sendMessage",
            "args": json.dumps({"chat_id": "8612707834", "text": "stringified"}),
        },
    )
    assert out == "stringified"


# ── notify_owner path ─────────────────────────────────


def test_extracts_content_from_notify_owner():
    out = _extract_telegram_reply(
        "mcp__chat_module__notify_owner",
        {"content": "echo to UI"},
    )
    assert out == "echo to UI"


# ── non-reply tool calls must NOT count ────────────────────────────────


def test_send_chat_action_is_not_a_user_reply():
    """sendChatAction is the typing indicator — emitted every 4s during
    a long agent run. If we count it as a reply, chat_module would
    persist multiple junk "replies" per turn (and the indicator has no
    text to extract anyway)."""
    out = _extract_telegram_reply(
        "mcp__telegram_module__tg_cli",
        {
            "method": "sendChatAction",
            "args": {"chat_id": "8612707834", "action": "typing"},
        },
    )
    assert out is None


def test_edit_message_text_is_not_a_user_reply():
    """Editing a prior message isn't a new reply. The original
    sendMessage already counted."""
    out = _extract_telegram_reply(
        "mcp__telegram_module__tg_cli",
        {
            "method": "editMessageText",
            "args": {"chat_id": "8612707834", "message_id": 7, "text": "edited"},
        },
    )
    assert out is None


def test_non_telegram_tool_returns_none():
    """A Slack tool call must not be misinterpreted as a Telegram reply."""
    out = _extract_telegram_reply(
        "mcp__slack_module__slack_cli",
        {"method": "chat.postMessage", "args": {"text": "from slack"}},
    )
    assert out is None


# ── defensive paths ────────────────────────────────────────────────────


def test_malformed_args_string_returns_none_gracefully():
    out = _extract_telegram_reply(
        "mcp__telegram_module__tg_cli",
        "not-valid-json-{",
    )
    assert out is None


def test_send_message_without_text_returns_placeholder():
    out = _extract_telegram_reply(
        "mcp__telegram_module__tg_cli",
        {"method": "sendMessage", "args": {"chat_id": "8612707834"}},
    )
    assert out == "(sent via tg_cli)"


def test_empty_tool_name_returns_none():
    out = _extract_telegram_reply(
        "",
        {"method": "sendMessage", "args": {"text": "x"}},
    )
    assert out is None


# ── registration is the critical "is the extractor wired in?" check ────


def test_telegram_handler_comes_from_the_channel_descriptor():
    """The Telegram message source is a fact of the DESCRIPTOR, resolved through the
    registry — not a module-level ``MessageSourceRegistry.register`` that only
    existed if something had imported telegram_module.

    Until 2026-09-07 this test had to ``importlib.reload`` the module to
    re-trigger that import-time call, which is the whole shape of the bug: the
    handler's existence depended on import order, so in a process where nothing
    had imported the module a delivered Telegram reply resolved the DEFAULT handler
    and was recorded as NO-REPLY. Now the descriptor is in ``ingress.channels``
    from boot and the view projects the handler from it.
    """
    from narranexus.platform.channel.message_source_handler import MessageSourceRegistry
    from narranexus_plugins.telegram_module.descriptor import DESCRIPTOR

    handler = MessageSourceRegistry.get("telegram")
    assert handler.name == "telegram", (
        f"Expected handler.name='telegram', got {handler.name!r}. "
        f"If this is 'default', telegram's ChannelDescriptor is not in ingress.channels "
        f"(did this process boot the plugin platform?) or declares no reply_tools."
    )
    assert "tg_cli" in handler.user_reply_tool_names
    assert handler.user_reply_tool_names == DESCRIPTOR.reply_tools
    assert handler.row_prefix_template == DESCRIPTOR.row_prefix_template
