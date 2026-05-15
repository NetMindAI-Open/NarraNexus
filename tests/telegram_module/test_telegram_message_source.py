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

from xyz_agent_context.module.telegram_module.telegram_module import (
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


# ── send_message_to_user_directly path ─────────────────────────────────


def test_extracts_content_from_send_message_to_user_directly():
    out = _extract_telegram_reply(
        "mcp__chat_module__send_message_to_user_directly",
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


def test_telegram_handler_is_registered_in_message_source_registry():
    """Importing telegram_module MUST register a handler keyed by
    'telegram'. Mirror of the slack registration test — see that test
    for the full failure-mode rationale (registry-clearing fixture in
    a sibling test file + Python module caching)."""
    import importlib
    from xyz_agent_context.channel.message_source_handler import (
        MessageSourceRegistry,
    )
    from xyz_agent_context.module.telegram_module import telegram_module

    if "telegram" not in MessageSourceRegistry._handlers:  # type: ignore[attr-defined]
        importlib.reload(telegram_module)

    handler = MessageSourceRegistry.get("telegram")
    assert handler.name == "telegram", (
        f"Expected handler.name='telegram', got {handler.name!r}. "
        f"If this is 'default' it means telegram_module's module-level "
        f"MessageSourceRegistry.register() call did not run."
    )
    assert "tg_cli" in handler.user_reply_tool_names
