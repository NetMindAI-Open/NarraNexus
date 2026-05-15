"""
ChannelSeenMessageRepository — atomic INSERT-or-UNIQUE + per-channel cleanup.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.repository.channel_seen_message_repository import (
    ChannelSeenMessageRepository,
)


@pytest.mark.asyncio
async def test_mark_seen_first_time_returns_true(db_client):
    repo = ChannelSeenMessageRepository("lark", db_client)
    assert await repo.mark_seen("om_1") is True

    rows = await db_client.get("channel_seen_messages", {"channel": "lark"})
    assert len(rows) == 1
    assert rows[0]["message_id"] == "om_1"


@pytest.mark.asyncio
async def test_mark_seen_duplicate_returns_false(db_client):
    repo = ChannelSeenMessageRepository("lark", db_client)
    assert await repo.mark_seen("om_1") is True
    assert await repo.mark_seen("om_1") is False
    rows = await db_client.get("channel_seen_messages", {"channel": "lark"})
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_mark_seen_different_channels_dedup_independently(db_client):
    """Same message_id in different channels MUST both insert."""
    lark_repo = ChannelSeenMessageRepository("lark", db_client)
    slack_repo = ChannelSeenMessageRepository("slack", db_client)

    assert await lark_repo.mark_seen("shared_id") is True
    assert await slack_repo.mark_seen("shared_id") is True

    # Each channel re-mark is a duplicate
    assert await lark_repo.mark_seen("shared_id") is False
    assert await slack_repo.mark_seen("shared_id") is False

    rows = await db_client.get("channel_seen_messages", {})
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_mark_seen_empty_id_returns_true(db_client):
    """Empty id can't dedup; defensive process."""
    repo = ChannelSeenMessageRepository("lark", db_client)
    assert await repo.mark_seen("") is True
    rows = await db_client.get("channel_seen_messages", {})
    assert rows == []  # nothing inserted


@pytest.mark.asyncio
async def test_mark_seen_propagates_non_unique_error(db_client, monkeypatch):
    """DB hiccups must not be silently treated as duplicates."""
    repo = ChannelSeenMessageRepository("lark", db_client)

    async def boom(*args, **kwargs):
        raise ConnectionError("transient backend failure")

    monkeypatch.setattr(db_client, "insert", boom)

    with pytest.raises(ConnectionError):
        await repo.mark_seen("om_x")


@pytest.mark.asyncio
async def test_cleanup_filters_by_channel(db_client):
    """Cleanup of channel A must not touch channel B."""
    import datetime as dt

    lark_repo = ChannelSeenMessageRepository("lark", db_client)
    slack_repo = ChannelSeenMessageRepository("slack", db_client)

    await lark_repo.mark_seen("old_lark")
    await slack_repo.mark_seen("old_slack")

    # Backdate both rows by 30 days
    old_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).isoformat(sep=" ")
    rows = await db_client.get("channel_seen_messages", {})
    for row in rows:
        await db_client.update(
            "channel_seen_messages",
            {"id": row["id"]},
            {"seen_at": old_ts},
        )

    deleted = await lark_repo.cleanup_older_than_days(days=7)
    assert deleted == 1
    remaining = await db_client.get("channel_seen_messages", {})
    assert len(remaining) == 1
    assert remaining[0]["channel"] == "slack"


@pytest.mark.asyncio
async def test_cleanup_returns_zero_on_no_rows(db_client):
    repo = ChannelSeenMessageRepository("lark", db_client)
    assert await repo.cleanup_older_than_days(7) == 0


def test_repo_rejects_empty_channel():
    with pytest.raises(ValueError):
        ChannelSeenMessageRepository("", None)
