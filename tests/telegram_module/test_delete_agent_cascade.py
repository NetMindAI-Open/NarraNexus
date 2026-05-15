"""
@file_name: test_delete_agent_cascade.py
@date: 2026-05-09
@description: REGRESSION test for the Phase 3 channel-cleanup-cascade fix,
extended to Telegram.

Why this file exists:
    Phase 3 introduced ``ChannelModuleBase.cleanup_for_agent`` so the
    auth.delete_agent route walks every channel module in MODULE_MAP
    and lets each one tear down its own state. We verify that when
    Telegram is the channel under test, calling
    ``TelegramModule().cleanup_for_agent(agent_id, db)``:

      1. Removes the row in ``channel_telegram_credentials``.
      2. Removes the agent's membership in any ``telegram_*`` inbox
         channels.
      3. Drops the inbox channel + messages when the agent was the
         only member.
      4. Leaves OTHER channels' state (e.g. ``slack_*`` inboxes) alone.

    If this regresses we'd silently leak credentials at delete-account
    time, which is a CRITICAL data-hygiene bug.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.telegram_module.telegram_module import TelegramModule


@pytest.mark.asyncio
async def test_cleanup_for_agent_removes_credentials_and_inbox(db_client):
    agent_id = "agent_a"

    # Arrange — credential row + telegram inbox + a foreign slack_ inbox
    await db_client.insert(
        "channel_telegram_credentials",
        {
            "agent_id": agent_id,
            "bot_token_encoded": "ZW5jb2RlZA==",  # base64("encoded")
            "bot_user_id": "1001",
            "bot_username": "acme_bot",
            "owner_username": "",
            "owner_user_id": "",
            "owner_name": "",
            "enabled": 1,
        },
    )
    # Telegram inbox channel + members + messages
    await db_client.insert(
        "bus_channels",
        {
            "channel_id": "telegram_inbox_99",
            "name": "Telegram inbox",
            "channel_type": "inbox",
            "created_by": agent_id,
        },
    )
    await db_client.insert(
        "bus_channel_members",
        {"channel_id": "telegram_inbox_99", "agent_id": agent_id},
    )
    await db_client.insert(
        "bus_messages",
        {
            "message_id": "msg_tg_1",
            "channel_id": "telegram_inbox_99",
            "from_agent": agent_id,
            "content": "hello tg",
            "msg_type": "text",
        },
    )
    # Foreign Slack inbox — must NOT be touched
    await db_client.insert(
        "bus_channels",
        {
            "channel_id": "slack_inbox_C1",
            "name": "Slack inbox",
            "channel_type": "inbox",
            "created_by": agent_id,
        },
    )
    await db_client.insert(
        "bus_channel_members",
        {"channel_id": "slack_inbox_C1", "agent_id": agent_id},
    )

    # Act — call cleanup directly (bypassing FastAPI delete_agent)
    module = TelegramModule(
        agent_id=agent_id, user_id=None, database_client=db_client
    )
    stats = await module.cleanup_for_agent(agent_id, db_client)

    # Assert — credential row gone
    cred_row = await db_client.get_one(
        "channel_telegram_credentials", {"agent_id": agent_id}
    )
    assert cred_row is None
    assert stats.get("channel_telegram_credentials", 0) >= 1

    # Telegram inbox member dropped; channel + messages dropped because
    # the agent was the only member.
    tg_member = await db_client.get_one(
        "bus_channel_members",
        {"channel_id": "telegram_inbox_99", "agent_id": agent_id},
    )
    assert tg_member is None
    tg_channel = await db_client.get_one(
        "bus_channels", {"channel_id": "telegram_inbox_99"}
    )
    assert tg_channel is None
    tg_msgs = await db_client.get(
        "bus_messages", {"channel_id": "telegram_inbox_99"}
    )
    assert tg_msgs == []

    # Foreign Slack inbox untouched — Telegram cleanup must scope strictly
    # to its own ``telegram_*`` channel_id namespace.
    slack_member = await db_client.get_one(
        "bus_channel_members",
        {"channel_id": "slack_inbox_C1", "agent_id": agent_id},
    )
    assert slack_member is not None
    slack_channel = await db_client.get_one(
        "bus_channels", {"channel_id": "slack_inbox_C1"}
    )
    assert slack_channel is not None


@pytest.mark.asyncio
async def test_cleanup_for_agent_no_credential_returns_empty_stats(db_client):
    """If the agent never bound Telegram, cleanup is a no-op (no error)."""
    module = TelegramModule(
        agent_id="ghost", user_id=None, database_client=db_client
    )
    stats = await module.cleanup_for_agent("ghost", db_client)
    assert stats == {}
