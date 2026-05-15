"""
ChannelInboxWriter — 5-row idempotent inbox bundle.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.channel.channel_inbox_writer import ChannelInboxWriter


async def _write_default(db, *, channel="slack", brand="Slack", agent_id="agent_a",
                         sender_id="U_alice", sender_name="Alice",
                         original="hello", response="hi back", chat_id="C_room"):
    writer = ChannelInboxWriter(channel, brand)
    await writer.write(
        db=db,
        agent_id=agent_id,
        sender_id=sender_id,
        sender_name=sender_name,
        original_message=original,
        agent_response=response,
        chat_id=chat_id,
    )


@pytest.mark.asyncio
async def test_write_creates_pseudo_agent_channel_and_member_first_time(db_client):
    await _write_default(db_client)

    pseudo = await db_client.get_one(
        "bus_agent_registry", {"agent_id": "slack_user_U_alice"}
    )
    assert pseudo is not None
    assert pseudo["description"] == "Alice"
    assert pseudo["capabilities"] == "Slack user"

    channel = await db_client.get_one(
        "bus_channels", {"channel_id": "slack_C_room"}
    )
    assert channel is not None
    assert channel["name"] == "Slack: Alice"
    assert channel["created_by"] == "agent_a"

    member = await db_client.get_one(
        "bus_channel_members",
        {"channel_id": "slack_C_room", "agent_id": "agent_a"},
    )
    assert member is not None

    msgs = await db_client.get(
        "bus_messages", {"channel_id": "slack_C_room"}
    )
    assert len(msgs) == 2
    inbound = [m for m in msgs if m["from_agent"] == "slack_user_U_alice"]
    outbound = [m for m in msgs if m["from_agent"] == "agent_a"]
    assert len(inbound) == 1 and inbound[0]["content"] == "hello"
    assert len(outbound) == 1 and outbound[0]["content"] == "hi back"


@pytest.mark.asyncio
async def test_write_idempotent_on_repeat(db_client):
    """Calling write twice MUST NOT duplicate registry/channel/member rows."""
    await _write_default(db_client)
    await _write_default(db_client)

    pseudo_rows = await db_client.get(
        "bus_agent_registry", {"agent_id": "slack_user_U_alice"}
    )
    assert len(pseudo_rows) == 1

    channel_rows = await db_client.get(
        "bus_channels", {"channel_id": "slack_C_room"}
    )
    assert len(channel_rows) == 1

    member_rows = await db_client.get(
        "bus_channel_members",
        {"channel_id": "slack_C_room", "agent_id": "agent_a"},
    )
    assert len(member_rows) == 1

    # Each call adds 2 message rows
    msg_rows = await db_client.get(
        "bus_messages", {"channel_id": "slack_C_room"}
    )
    assert len(msg_rows) == 4


@pytest.mark.asyncio
async def test_write_updates_pseudo_agent_description_on_known_sender_name(db_client):
    """First write with Unknown placeholder, then re-write with real name → row updated."""
    await _write_default(db_client, sender_name="Unknown")
    pseudo = await db_client.get_one(
        "bus_agent_registry", {"agent_id": "slack_user_U_alice"}
    )
    # When sender_name is "Unknown", display_name falls back to sender_id
    assert pseudo["description"] == "U_alice"

    await _write_default(db_client, sender_name="Alice Wonderland")
    pseudo = await db_client.get_one(
        "bus_agent_registry", {"agent_id": "slack_user_U_alice"}
    )
    assert pseudo["description"] == "Alice Wonderland"


@pytest.mark.asyncio
async def test_write_skips_outgoing_when_response_is_empty(db_client):
    await _write_default(db_client, response="")
    msgs = await db_client.get(
        "bus_messages", {"channel_id": "slack_C_room"}
    )
    assert len(msgs) == 1
    assert msgs[0]["from_agent"] == "slack_user_U_alice"  # only the inbound row


@pytest.mark.asyncio
async def test_write_uses_channel_specific_prefixes(db_client):
    """Different channels MUST namespace their synthetic IDs."""
    await _write_default(db_client, channel="lark", brand="Feishu")
    await _write_default(db_client, channel="telegram", brand="Telegram")

    lark_pseudo = await db_client.get_one(
        "bus_agent_registry", {"agent_id": "lark_user_U_alice"}
    )
    tg_pseudo = await db_client.get_one(
        "bus_agent_registry", {"agent_id": "telegram_user_U_alice"}
    )
    assert lark_pseudo is not None and lark_pseudo["capabilities"] == "Feishu user"
    assert tg_pseudo is not None and tg_pseudo["capabilities"] == "Telegram user"

    lark_channel = await db_client.get_one(
        "bus_channels", {"channel_id": "lark_C_room"}
    )
    tg_channel = await db_client.get_one(
        "bus_channels", {"channel_id": "telegram_C_room"}
    )
    assert lark_channel is not None and lark_channel["name"] == "Feishu: Alice"
    assert tg_channel is not None and tg_channel["name"] == "Telegram: Alice"


def test_writer_rejects_empty_channel_or_brand():
    with pytest.raises(ValueError):
        ChannelInboxWriter("", "Slack")
    with pytest.raises(ValueError):
        ChannelInboxWriter("slack", "")
