"""
@file_name: test_slack_trigger.py
@date: 2026-05-08
@description: Tests for SlackTrigger event parsing + identity helpers.

Why this file exists:
    SlackTrigger sits between Slack's Socket Mode and the channel
    pipeline. The hot decisions — "is this an event we care about?",
    "is it our own bot's echo?", "what's the human display name?" —
    all live in narrow, pure-ish methods that are easy to cover
    without standing up a real Socket Mode session.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.slack_module import slack_trigger as st_mod
from xyz_agent_context.module.slack_module._slack_credential_manager import (
    SlackCredential,
)
from xyz_agent_context.module.slack_module.slack_trigger import SlackTrigger
from xyz_agent_context.schema.parsed_message import ChatType


def _cred(bot_user_id: str = "U0BOT") -> SlackCredential:
    return SlackCredential(
        agent_id="agent_a",
        bot_token="xoxb-test",
        app_token="xapp-test",
        bot_user_id=bot_user_id,
        team_id="T1",
        team_name="Team",
    )


class _StubSDK:
    """Replacement for SlackSDKClient for sender-name resolution."""

    def __init__(self, bot_token: str, *, user_payload: dict | None = None):
        self._user_payload = user_payload or {}
        self.calls: list[str] = []

    async def get_user_info(self, user_id: str) -> dict:
        self.calls.append(user_id)
        return self._user_payload


def test_instantiates_without_db():
    trigger = SlackTrigger(max_workers=3)
    assert trigger.channel_name == "slack"
    assert trigger.brand_display == "Slack"
    assert trigger._socket_clients == {}


# ── parse_event ─────────────────────────────────────────────────────────


def test_parse_event_message_returns_parsed_message():
    """Regular ``message`` events are accepted in DM context (im)."""
    trigger = SlackTrigger()
    raw = {
        "type": "message",
        "user": "U123",
        "channel": "D456",
        "channel_type": "im",
        "text": "hello world",
        "ts": "1700000000.000100",
        "client_msg_id": "msg-uuid-1",
    }
    parsed = trigger.parse_event(raw)

    assert parsed is not None
    assert parsed.message_id == "msg-uuid-1"
    assert parsed.sender_id == "U123"
    assert parsed.chat_id == "D456"
    assert parsed.content == "hello world"
    assert parsed.timestamp_ms == 1700000000000  # ts * 1000
    assert parsed.chat_type == ChatType.PRIVATE  # D-prefixed DM
    assert parsed.thread_id is None


def test_parse_event_app_mention_accepted():
    trigger = SlackTrigger()
    raw = {
        "type": "app_mention",
        "user": "U999",
        "channel": "C100",
        "text": "<@U0BOT> ping",
        "ts": "1700000001.000000",
    }
    parsed = trigger.parse_event(raw)

    assert parsed is not None
    assert parsed.sender_id == "U999"
    assert "U0BOT" in parsed.mentions


def test_parse_event_subtype_channel_join_returns_none():
    trigger = SlackTrigger()
    raw = {
        "type": "message",
        "subtype": "channel_join",
        "user": "U1",
        "channel": "C1",
        "ts": "1.0",
    }
    assert trigger.parse_event(raw) is None


def test_parse_event_message_changed_subtype_returns_none():
    trigger = SlackTrigger()
    assert (
        trigger.parse_event(
            {
                "type": "message",
                "subtype": "message_changed",
                "user": "U1",
                "channel": "C1",
                "ts": "1.0",
            }
        )
        is None
    )


def test_parse_event_missing_user_returns_none():
    """Tombstone-y events with no `user` should be skipped."""
    trigger = SlackTrigger()
    raw = {
        "type": "message",
        "channel": "C1",
        "ts": "1.0",
    }
    assert trigger.parse_event(raw) is None


def test_parse_event_preserves_thread_ts():
    """thread_ts must propagate to ``ParsedMessage.thread_id``. Uses DM
    context (channel_type=im) so the Phase 5 filter doesn't drop the
    event — separately, thread replies in channels arrive as
    ``app_mention`` which is filter-exempt anyway."""
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U2",
            "channel": "D2",
            "channel_type": "im",
            "text": "in thread",
            "ts": "1700000002.000200",
            "thread_ts": "1700000000.000000",
        }
    )
    assert parsed is not None
    assert parsed.thread_id == "1700000000.000000"


def test_parse_event_dm_channel_classified_private():
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U2",
            "channel": "D123",  # DM
            "channel_type": "im",
            "text": "secret",
            "ts": "1.0",
        }
    )
    assert parsed is not None
    assert parsed.chat_type == ChatType.PRIVATE


def test_parse_event_falls_back_to_ts_when_no_client_msg_id():
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U2",
            "channel": "D2",
            "channel_type": "im",
            "text": "hi",
            "ts": "1700000003.000300",
        }
    )
    assert parsed is not None
    assert parsed.message_id == "1700000003.000300"


# ── Phase 5: channel_type filter ────────────────────────────────────────


def test_parse_event_dm_message_accepted():
    """``channel_type='im'`` → accepted (1:1 DM)."""
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U1",
            "channel": "D1",
            "channel_type": "im",
            "text": "hi",
            "ts": "1.0",
        }
    )
    assert parsed is not None


def test_parse_event_mpim_message_accepted():
    """``channel_type='mpim'`` → accepted (multi-party DM)."""
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U1",
            "channel": "G1",
            "channel_type": "mpim",
            "text": "hi all",
            "ts": "1.0",
        }
    )
    assert parsed is not None


def test_parse_event_channel_message_dropped():
    """Public channel ``message`` event with no @-mention → dropped.

    Phase 5 reply policy: in channels we only honour ``app_mention``;
    everything else from ``channel_type='channel'`` is filtered out at the
    trigger boundary so the agent never sees it.
    """
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U1",
            "channel": "C1",
            "channel_type": "channel",
            "text": "general chatter",
            "ts": "1.0",
        }
    )
    assert parsed is None


def test_parse_event_group_message_dropped():
    """Private channel (``channel_type='group'``) ``message`` event → dropped."""
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U1",
            "channel": "G1",
            "channel_type": "group",
            "text": "private chatter",
            "ts": "1.0",
        }
    )
    assert parsed is None


def test_parse_event_app_mention_in_channel_accepted():
    """``app_mention`` events are exempt from the channel_type filter.

    The whole point of Phase 5 is "only reply when @-mentioned in
    channels", and ``app_mention`` is precisely how Slack delivers an
    @-mention. It must still come through even though channel-message
    events do not.
    """
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "app_mention",
            "user": "U1",
            "channel": "C1",
            # No channel_type field — app_mention doesn't carry one and
            # the filter must not apply to this event type.
            "text": "<@U0BOT> ping",
            "ts": "1.0",
        }
    )
    assert parsed is not None
    assert parsed.sender_id == "U1"


# ── is_echo ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_echo_detects_bot_user_id():
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U0BOT",
            "channel": "D1",
            "channel_type": "im",
            "text": "from bot",
            "ts": "1.0",
        }
    )
    assert parsed is not None
    assert await trigger.is_echo(parsed, _cred("U0BOT")) is True


@pytest.mark.asyncio
async def test_is_echo_false_for_human_sender():
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "UHUMAN",
            "channel": "D1",
            "channel_type": "im",
            "text": "hi bot",
            "ts": "1.0",
        }
    )
    assert parsed is not None
    assert await trigger.is_echo(parsed, _cred("U0BOT")) is False


@pytest.mark.asyncio
async def test_is_echo_false_when_credential_has_no_bot_user_id():
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U0BOT",
            "channel": "D1",
            "channel_type": "im",
            "text": "x",
            "ts": "1.0",
        }
    )
    assert parsed is not None
    assert await trigger.is_echo(parsed, _cred("")) is False


# ── resolve_sender_name ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_sender_name_uses_real_name(monkeypatch: pytest.MonkeyPatch):
    trigger = SlackTrigger()
    stub = _StubSDK("xoxb-test", user_payload={"real_name": "Ada Lovelace"})
    monkeypatch.setattr(st_mod, "SlackSDKClient", lambda _t: stub)

    name = await trigger.resolve_sender_name("U42", _cred())
    assert name == "Ada Lovelace"
    assert stub.calls == ["U42"]


@pytest.mark.asyncio
async def test_resolve_sender_name_caches_result(monkeypatch: pytest.MonkeyPatch):
    trigger = SlackTrigger()
    stub = _StubSDK("xoxb-test", user_payload={"real_name": "Ada"})
    monkeypatch.setattr(st_mod, "SlackSDKClient", lambda _t: stub)

    first = await trigger.resolve_sender_name("U42", _cred())
    second = await trigger.resolve_sender_name("U42", _cred())

    assert first == second == "Ada"
    # Cache hit on second call — only one upstream call.
    assert stub.calls == ["U42"]


@pytest.mark.asyncio
async def test_resolve_sender_name_falls_back_to_user_id(
    monkeypatch: pytest.MonkeyPatch,
):
    trigger = SlackTrigger()
    stub = _StubSDK("xoxb-test", user_payload={})
    monkeypatch.setattr(st_mod, "SlackSDKClient", lambda _t: stub)

    name = await trigger.resolve_sender_name("U_NONAME", _cred())
    assert name == "U_NONAME"


def test_extract_output_returns_silent_sentinel_for_empty_text():
    trigger = SlackTrigger()

    class _R:
        output_text = ""
        raw_items: list = []

    out = trigger.extract_output(_R(), None, _cred())
    assert out == "(stayed silent)"


def test_extract_output_reads_slack_cli_args_not_output_text():
    """Inbox should show what was sent to Slack, not the agent's reasoning."""
    trigger = SlackTrigger()

    class _R:
        # Agent's reasoning — this MUST NOT end up in the inbox.
        output_text = "My thought process: User asked X, I should reply Y"
        raw_items = [
            {
                "item": {
                    "type": "tool_call_item",
                    "tool_name": "mcp__slack_module__slack_cli",
                    "arguments": {
                        "agent_id": "agent_a",
                        "method": "chat.postMessage",
                        "args": {
                            "channel": "D0B2AR5MC3Z",
                            "text": "Hello, I'm here to help!",
                        },
                    },
                }
            }
        ]

    out = trigger.extract_output(_R(), None, _cred())
    assert out == "Hello, I'm here to help!"
    assert "thought process" not in out


def test_extract_output_skips_non_chat_postMessage_calls():
    """reactions.add and friends are NOT user-visible reply text."""
    trigger = SlackTrigger()

    class _R:
        output_text = ""
        raw_items = [
            {
                "item": {
                    "type": "tool_call_item",
                    "tool_name": "mcp__slack_module__slack_cli",
                    "arguments": {
                        "method": "reactions.add",
                        "args": {"channel": "C1", "timestamp": "1.0", "name": "thumbsup"},
                    },
                }
            }
        ]

    # No chat.postMessage call → "stayed silent" sentinel
    out = trigger.extract_output(_R(), None, _cred())
    assert out == "(stayed silent)"


def test_extract_output_handles_args_as_json_string():
    """When `arguments` arrives as a serialized JSON string, parse it."""
    import json
    trigger = SlackTrigger()

    class _R:
        output_text = ""
        raw_items = [
            {
                "item": {
                    "type": "tool_call_item",
                    "tool_name": "mcp__slack_module__slack_cli",
                    "arguments": json.dumps({
                        "method": "chat.postMessage",
                        "args": {"channel": "C1", "text": "string-encoded reply"},
                    }),
                }
            }
        ]

    out = trigger.extract_output(_R(), None, _cred())
    assert out == "string-encoded reply"
