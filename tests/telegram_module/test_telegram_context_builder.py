"""
@file_name: test_telegram_context_builder.py
@date: 2026-05-09
@description: Tests for TelegramContextBuilder — message_info shape,
empty-history guarantee, and chat-id-sign room_type derivation.

Why this file exists:
    Telegram has no Bot-API equivalent of ``conversations.history``,
    so ``get_conversation_history`` MUST return ``[]`` unconditionally.
    The reply_instruction also needs ``thread_id`` baked in for forum
    topics; otherwise the agent's reply lands in the wrong place.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.telegram_module._telegram_credential_manager import (
    TelegramCredential,
)
from xyz_agent_context.module.telegram_module.telegram_context_builder import (
    TelegramContextBuilder,
)
from xyz_agent_context.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)


def _cred() -> TelegramCredential:
    return TelegramCredential(
        agent_id="agent_a",
        bot_token="1234:tok",
        bot_user_id="1001",
        bot_username="acme_bot",
    )


def _msg(
    chat_id: str = "99",
    thread_id: str | None = None,
    chat_type: ChatType = ChatType.PRIVATE,
    message_id: str = "7",
) -> ParsedMessage:
    return ParsedMessage(
        message_id=message_id,
        chat_id=chat_id,
        sender_id="42",
        sender_name="Ada Lovelace",
        content="hello there",
        content_type=MessageContentType.TEXT,
        chat_type=chat_type,
        timestamp_ms=1700000000000,
        thread_id=thread_id,
    )


@pytest.mark.asyncio
async def test_get_message_info_shape_for_dm():
    builder = TelegramContextBuilder(
        message=_msg(), credential=_cred(), agent_id="agent_a"
    )

    info = await builder.get_message_info()

    assert info["agent_id"] == "agent_a"
    assert info["channel_key"] == "telegram"
    assert info["channel_display_name"] == "Telegram"
    assert info["room_id"] == "99"
    assert info["sender_id"] == "42"
    assert info["sender_display_name"] == "Ada Lovelace"
    assert info["my_channel_id"] == "1001"
    assert info["message_body"] == "hello there"
    assert info["send_tool_name"] == "tg_cli"
    assert info["room_type"] == "Direct Message"
    # Reply instruction baked-in chat_id + sendMessage shape
    assert "sendMessage" in info["reply_instruction"]
    assert "99" in info["reply_instruction"]


@pytest.mark.asyncio
async def test_get_message_info_room_type_for_group():
    """Negative chat_id → group/supergroup/channel."""
    builder = TelegramContextBuilder(
        message=_msg(chat_id="-1001234567890", chat_type=ChatType.GROUP),
        credential=_cred(),
        agent_id="agent_a",
    )

    info = await builder.get_message_info()
    assert info["room_type"] == "Group Room"


@pytest.mark.asyncio
async def test_reply_instruction_includes_thread_id_when_set():
    builder = TelegramContextBuilder(
        message=_msg(thread_id="555"),
        credential=_cred(),
        agent_id="agent_a",
    )

    info = await builder.get_message_info()
    assert "message_thread_id" in info["reply_instruction"]
    assert "555" in info["reply_instruction"]


@pytest.mark.asyncio
async def test_reply_instruction_omits_thread_id_when_unset():
    builder = TelegramContextBuilder(
        message=_msg(thread_id=None),
        credential=_cred(),
        agent_id="agent_a",
    )

    info = await builder.get_message_info()
    assert "message_thread_id" not in info["reply_instruction"]


@pytest.mark.asyncio
async def test_get_conversation_history_empty_without_db():
    """No db_client → empty history. Falling back to nothing is the only
    safe default when the local store isn't available (tests, cold start
    before the trigger has wired its DB handle through)."""
    builder = TelegramContextBuilder(
        message=_msg(), credential=_cred(), agent_id="agent_a"
    )
    assert await builder.get_conversation_history(limit=50) == []


class _FakeDB:
    """Stand-in for AsyncDatabaseClient that returns canned bus_messages."""

    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    async def get(self, table, filters=None, limit=None, offset=None, order_by=None):
        self.calls.append((table, filters, limit, order_by))
        return list(self._rows)


@pytest.mark.asyncio
async def test_get_conversation_history_reads_bus_messages():
    """Production path: db_client + bus_messages rows → chronological history.

    Reproduces the 2026-05-13 bug where the user asked about weather,
    got "search unavailable", said "再试一下" two turns later, but the
    agent's prompt had ZERO history and reverted to an older "test the
    Telegram channel" task. With history loaded, the prompt sees the
    weather exchange and the retry intent is unambiguous.
    """
    chat_id = "8612707834"
    rows_newest_first = [
        {
            "message_id": "m_now",
            "channel_id": f"telegram_{chat_id}",
            "from_agent": "telegram_user_8612707834",
            "content": "再试一下",  # current trigger — must be filtered out
            "created_at": "2026-05-13 17:31:26",
        },
        {
            "message_id": "m_5",
            "channel_id": f"telegram_{chat_id}",
            "from_agent": "agent_a",
            "content": "抱歉，搜索功能暂时不可用",
            "created_at": "2026-05-13 17:08:54",
        },
        {
            "message_id": "m_4",
            "channel_id": f"telegram_{chat_id}",
            "from_agent": "telegram_user_8612707834",
            "content": "今天天气怎么样",
            "created_at": "2026-05-13 17:08:54",
        },
        {
            "message_id": "m_3",
            "channel_id": f"telegram_{chat_id}",
            "from_agent": "agent_a",
            "content": "在的，有什么需要帮忙的？",
            "created_at": "2026-05-13 17:06:57",
        },
        {
            "message_id": "m_2",
            "channel_id": f"telegram_{chat_id}",
            "from_agent": "telegram_user_8612707834",
            "content": "在吗",
            "created_at": "2026-05-13 17:06:57",
        },
    ]

    builder = TelegramContextBuilder(
        message=_msg(chat_id=chat_id, message_id="m_now"),
        credential=_cred(),
        agent_id="agent_a",
        db_client=_FakeDB(rows_newest_first),
    )

    history = await builder.get_conversation_history(limit=10)

    # Chronological order — oldest first
    bodies = [h["body"] for h in history]
    assert bodies == [
        "在吗",
        "在的，有什么需要帮忙的？",
        "今天天气怎么样",
        "抱歉，搜索功能暂时不可用",
    ]
    # Current trigger message filtered out
    assert "再试一下" not in bodies
    # Bot replies marked clearly so the LLM doesn't confuse roles
    senders = [h["sender"] for h in history]
    assert senders[1] == "Me (bot)"
    assert senders[3] == "Me (bot)"


@pytest.mark.asyncio
async def test_get_conversation_history_filters_to_chat_id():
    """The bus_messages filter MUST scope by channel_id — cross-chat
    bleed would let one user's history leak into another's prompt."""
    db = _FakeDB([])
    builder = TelegramContextBuilder(
        message=_msg(chat_id="8612707834", message_id="m_now"),
        credential=_cred(),
        agent_id="agent_a",
        db_client=db,
    )

    await builder.get_conversation_history(limit=10)

    assert db.calls, "db.get was never called"
    table, filters, _, _ = db.calls[0]
    assert table == "bus_messages"
    assert filters == {"channel_id": "telegram_8612707834"}


@pytest.mark.asyncio
async def test_get_conversation_history_caps_to_limit():
    """When the DB returns more rows than requested (we over-fetch by 5
    to avoid running short after filtering the current msg), the result
    is capped to the newest ``limit`` entries."""
    rows = [
        {
            "message_id": f"m_{i}",
            "channel_id": "telegram_42",
            "from_agent": "telegram_user_42",
            "content": f"msg #{i}",
            "created_at": f"2026-05-13 17:00:{i:02d}",
        }
        for i in range(20, 0, -1)  # newest-first, 20 rows
    ]
    builder = TelegramContextBuilder(
        message=_msg(chat_id="42", message_id="m_999"),
        credential=_cred(),
        agent_id="agent_a",
        db_client=_FakeDB(rows),
    )

    history = await builder.get_conversation_history(limit=3)
    assert len(history) == 3
    assert history[-1]["body"] == "msg #20"


@pytest.mark.asyncio
async def test_get_room_members_returns_empty():
    builder = TelegramContextBuilder(
        message=_msg(), credential=_cred(), agent_id="agent_a"
    )
    assert await builder.get_room_members() == []
