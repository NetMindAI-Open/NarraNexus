"""
@file_name: test_slack_message_source.py
@date: 2026-05-13
@description: Tests for _extract_slack_reply — the MessageSourceRegistry
extractor that lets ChatModule capture Slack agent replies into long-term
memory instead of writing a "Background activity (slack)" placeholder.

Why this matters:
    Without _extract_slack_reply, every Slack turn lands in
    ``instance_json_format_memory_chat`` as an activity row that
    hook_data_gathering then filters out — agents see zero history
    from prior Slack turns. Observed live 2026-05-13: 100% of slack
    rows in instance chat_93f340b8 were "Background activity (slack)"
    placeholders. These tests pin the extractor's contract so that
    regression can't recur silently.
"""
from __future__ import annotations

import json

from xyz_agent_context.module.slack_module.slack_module import _extract_slack_reply


# ── canonical reply via slack_cli + chat.postMessage ───────────────────


def test_extracts_text_from_slack_cli_chat_post_message():
    """The hot path: agent calls slack_cli with method=chat.postMessage
    and a text body. Reply text must come out verbatim."""
    out = _extract_slack_reply(
        "mcp__slack_module__slack_cli",
        {
            "agent_id": "agent_a",
            "method": "chat.postMessage",
            "args": {"channel": "C123", "text": "Hello world"},
        },
    )
    assert out == "Hello world"


def test_extracts_text_when_args_arrived_as_json_string():
    """Some MCP wire formats serialize args. Must still parse."""
    out = _extract_slack_reply(
        "mcp__slack_module__slack_cli",
        {
            "method": "chat.postMessage",
            "args": json.dumps({"channel": "C1", "text": "stringified args"}),
        },
    )
    assert out == "stringified args"


# ── send_message_to_user_directly path ─────────────────────────────────


def test_extracts_content_from_send_message_to_user_directly():
    """Slack agents may still echo to NarraNexus UI via the generic
    chat tool; the extractor recognises it and returns ``content``."""
    out = _extract_slack_reply(
        "mcp__chat_module__send_message_to_user_directly",
        {"content": "echo to UI"},
    )
    assert out == "echo to UI"


# ── non-reply tool calls must NOT count ────────────────────────────────


def test_reactions_add_is_not_a_user_reply():
    """``reactions.add`` is a side-effect on a message; not user-visible
    reply text. Must return None or chat_module will mis-classify the
    turn as having a reply."""
    out = _extract_slack_reply(
        "mcp__slack_module__slack_cli",
        {
            "method": "reactions.add",
            "args": {"channel": "C1", "timestamp": "1.0", "name": "thumbsup"},
        },
    )
    assert out is None


def test_chat_update_is_not_a_user_reply():
    """Editing an existing message is not a new reply. The original
    chat.postMessage already counted; editing must not double-count."""
    out = _extract_slack_reply(
        "mcp__slack_module__slack_cli",
        {
            "method": "chat.update",
            "args": {"channel": "C1", "ts": "1.0", "text": "edited"},
        },
    )
    assert out is None


def test_non_slack_tool_returns_none():
    """A tool unrelated to Slack (e.g. another channel's CLI) must not
    be misinterpreted as a Slack reply."""
    out = _extract_slack_reply(
        "mcp__telegram_module__tg_cli",
        {"method": "sendMessage", "args": {"text": "from telegram"}},
    )
    assert out is None


# ── defensive paths ────────────────────────────────────────────────────


def test_malformed_args_string_returns_none_gracefully():
    """args is a string but not valid JSON — must NOT raise; treat as
    no reply (chat_module would log [NO-REPLY-BG] and move on)."""
    out = _extract_slack_reply(
        "mcp__slack_module__slack_cli",
        "not-valid-json-{",
    )
    assert out is None


def test_chat_post_message_without_text_returns_placeholder():
    """Recognised as a send but the inner args have no text. Returning
    a placeholder over None means the turn is still classified as
    'agent replied' so chat_module doesn't drop the row entirely."""
    out = _extract_slack_reply(
        "mcp__slack_module__slack_cli",
        {"method": "chat.postMessage", "args": {"channel": "C1"}},
    )
    assert out == "(sent via slack_cli)"


def test_empty_tool_name_returns_none():
    out = _extract_slack_reply("", {"method": "chat.postMessage", "args": {"text": "x"}})
    assert out is None


# ── registration is the critical "is the extractor wired in?" check ────


def test_slack_handler_is_registered_in_message_source_registry():
    """Importing slack_module MUST register a handler keyed by 'slack'
    so chat_module._extract_user_visible_response picks it up. Without
    this registration the extractor function is dead code: ChatModule
    falls back to _DEFAULT_HANDLER, doesn't recognise slack_cli, and
    every Slack turn lands as a "Background activity (slack)"
    placeholder that the next turn's history loader then drops.

    Uses ``importlib.reload`` because another test file
    (``tests/channel/test_message_source_handler.py``) clears the
    registry between tests via an autouse fixture. Once cleared,
    ``import slack_module`` is a no-op for module-level side effects
    because Python caches the module in sys.modules — so we explicitly
    reload to re-trigger the top-level ``MessageSourceRegistry.register``
    call. This mirrors what happens on a fresh process boot.
    """
    import importlib
    from xyz_agent_context.channel.message_source_handler import (
        MessageSourceRegistry,
    )
    from xyz_agent_context.module.slack_module import slack_module

    if "slack" not in MessageSourceRegistry._handlers:  # type: ignore[attr-defined]
        importlib.reload(slack_module)

    handler = MessageSourceRegistry.get("slack")
    assert handler.name == "slack", (
        f"Expected handler.name='slack', got {handler.name!r}. "
        f"If this is 'default' it means slack_module's module-level "
        f"MessageSourceRegistry.register() call did not run."
    )
    assert "slack_cli" in handler.user_reply_tool_names
