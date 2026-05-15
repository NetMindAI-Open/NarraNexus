"""
@file_name: test_slack_context_builder.py
@date: 2026-05-08
@description: Tests for SlackContextBuilder — message_info shape +
conversation history reversal + thread vs channel branching.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.slack_module._slack_credential_manager import (
    SlackCredential,
)
from xyz_agent_context.module.slack_module.slack_context_builder import (
    SlackContextBuilder,
)
from xyz_agent_context.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)


def _cred() -> SlackCredential:
    return SlackCredential(
        agent_id="agent_a",
        bot_token="xoxb-test",
        app_token="xapp-test",
        bot_user_id="U0BOT",
        team_id="T1",
        team_name="Team",
    )


def _msg(thread_id: str | None = None, chat_id: str = "C100") -> ParsedMessage:
    return ParsedMessage(
        message_id="msg-1",
        chat_id=chat_id,
        sender_id="UHUMAN",
        sender_name="Ada",
        content="hello there",
        content_type=MessageContentType.TEXT,
        chat_type=ChatType.GROUP,
        timestamp_ms=1700000000000,
        thread_id=thread_id,
    )


class _StubClient:
    """Stand-in SlackSDKClient — returns canned history/replies."""

    def __init__(
        self,
        history: list[dict] | None = None,
        replies: list[dict] | None = None,
    ):
        self._history = history or []
        self._replies = replies or []
        self.history_calls: list[tuple[str, int]] = []
        self.replies_calls: list[tuple[str, str, int]] = []

    async def get_conversation_history(self, channel: str, limit: int):
        self.history_calls.append((channel, limit))
        return self._history

    async def get_conversation_replies(self, channel: str, ts: str, limit: int):
        self.replies_calls.append((channel, ts, limit))
        return self._replies


@pytest.mark.asyncio
async def test_get_message_info_shape():
    builder = SlackContextBuilder(
        message=_msg(), credential=_cred(), agent_id="agent_a"
    )

    info = await builder.get_message_info()

    assert info["agent_id"] == "agent_a"
    assert info["channel_key"] == "slack"
    assert info["channel_display_name"] == "Slack"
    assert info["room_id"] == "C100"
    assert info["sender_id"] == "UHUMAN"
    assert info["sender_display_name"] == "Ada"
    assert info["my_channel_id"] == "U0BOT"
    assert info["message_body"] == "hello there"
    assert info["send_tool_name"] == "slack_cli"
    assert "chat.postMessage" in info["reply_instruction"]
    assert "C100" in info["reply_instruction"]


@pytest.mark.asyncio
async def test_get_message_info_thread_id_baked_into_reply_instruction():
    builder = SlackContextBuilder(
        message=_msg(thread_id="1699999999.000000"),
        credential=_cred(),
        agent_id="agent_a",
    )
    info = await builder.get_message_info()
    assert "thread_ts" in info["reply_instruction"]
    assert "1699999999.000000" in info["reply_instruction"]


@pytest.mark.asyncio
async def test_get_message_info_no_thread_omits_thread_ts():
    builder = SlackContextBuilder(
        message=_msg(thread_id=None), credential=_cred(), agent_id="agent_a"
    )
    info = await builder.get_message_info()
    assert "thread_ts" not in info["reply_instruction"]


@pytest.mark.asyncio
async def test_get_conversation_history_reverses_to_chronological(
    monkeypatch: pytest.MonkeyPatch,
):
    # Slack returns newest-first. Builder must hand back chronological.
    history_newest_first = [
        {"ts": "3.0", "user": "U1", "text": "third"},
        {"ts": "2.0", "user": "U1", "text": "second"},
        {"ts": "1.0", "user": "U1", "text": "first"},
    ]
    stub = _StubClient(history=history_newest_first)
    builder = SlackContextBuilder(
        message=_msg(), credential=_cred(), agent_id="agent_a"
    )
    builder._client = stub  # type: ignore[assignment]

    out = await builder.get_conversation_history(limit=10)

    assert [m["body"] for m in out] == ["first", "second", "third"]
    assert stub.history_calls == [("C100", 10)]
    assert stub.replies_calls == []


@pytest.mark.asyncio
async def test_get_conversation_history_uses_replies_for_thread():
    replies = [
        {"ts": "100.0", "user": "U1", "text": "root"},
        {"ts": "100.5", "user": "U2", "text": "reply 1"},
        {"ts": "101.0", "user": "U1", "text": "reply 2"},
    ]
    stub = _StubClient(replies=replies)
    builder = SlackContextBuilder(
        message=_msg(thread_id="100.0"),
        credential=_cred(),
        agent_id="agent_a",
    )
    builder._client = stub  # type: ignore[assignment]

    out = await builder.get_conversation_history(limit=20)

    # replies path called, history path NOT called
    assert stub.replies_calls == [("C100", "100.0", 20)]
    assert stub.history_calls == []
    # Reversed-from-newest: original list was already
    # chronological; reversed() → newest first → ["reply 2", "reply 1", "root"]
    assert out[0]["body"] == "reply 2"
    assert out[-1]["body"] == "root"


@pytest.mark.asyncio
async def test_get_conversation_history_empty_when_chat_id_missing():
    builder = SlackContextBuilder(
        message=_msg(chat_id=""), credential=_cred(), agent_id="agent_a"
    )
    out = await builder.get_conversation_history(limit=10)
    assert out == []


@pytest.mark.asyncio
async def test_get_room_members_returns_empty():
    builder = SlackContextBuilder(
        message=_msg(), credential=_cred(), agent_id="agent_a"
    )
    assert await builder.get_room_members() == []
