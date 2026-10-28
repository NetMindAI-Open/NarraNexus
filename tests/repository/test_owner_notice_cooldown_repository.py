"""
@file_name: test_owner_notice_cooldown_repository.py
@date: 2026-09-09
@description: OwnerNoticeCooldownRepository — the persisted per-(agent, target,
category) window behind owner-facing SYSTEM_NOTICE writers. Covers arm →
cooling, expiry, and that neither a different target nor a different category
shares a window.
"""

from datetime import timedelta

import pytest

from narranexus.platform.repository.owner_notice_cooldown_repository import (
    OwnerNoticeCooldownRepository,
)
from narranexus.platform.utils.timezone import utc_now

WINDOW = 1800


@pytest.mark.asyncio
async def test_never_notified_is_not_cooling(db_client):
    repo = OwnerNoticeCooldownRepository(db_client)
    assert await repo.is_cooling("agent_a", "ch1", "generic", WINDOW) is False
    assert await repo.last_notified_at("agent_a", "ch1", "generic") is None


@pytest.mark.asyncio
async def test_arm_opens_a_window_that_survives_a_new_repository_instance(db_client):
    await OwnerNoticeCooldownRepository(db_client).arm("agent_a", "ch1", "generic")
    # A "restart": a fresh repository over the same database still sees it.
    assert await OwnerNoticeCooldownRepository(db_client).is_cooling(
        "agent_a", "ch1", "generic", WINDOW
    ) is True


@pytest.mark.asyncio
async def test_window_expires(db_client):
    repo = OwnerNoticeCooldownRepository(db_client)
    await repo.arm(
        "agent_a", "ch1", "generic", at=utc_now() - timedelta(seconds=WINDOW + 5)
    )
    assert await repo.is_cooling("agent_a", "ch1", "generic", WINDOW) is False
    # Re-arming refreshes the SAME row (update path), it does not fail on the key.
    await repo.arm("agent_a", "ch1", "generic")
    assert await repo.is_cooling("agent_a", "ch1", "generic", WINDOW) is True
    rows = await db_client.get(OwnerNoticeCooldownRepository.TABLE, {"agent_id": "agent_a"})
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_windows_are_per_target_and_per_category(db_client):
    repo = OwnerNoticeCooldownRepository(db_client)
    await repo.arm("agent_a", "ch1", "generic")
    assert await repo.is_cooling("agent_a", "ch2", "generic", WINDOW) is False
    assert await repo.is_cooling("agent_a", "ch1", "provider_credential", WINDOW) is False
    assert await repo.is_cooling("agent_b", "ch1", "generic", WINDOW) is False


@pytest.mark.asyncio
async def test_cleanup_removes_only_long_closed_windows(db_client):
    repo = OwnerNoticeCooldownRepository(db_client)
    await repo.arm("agent_a", "ch1", "generic", at=utc_now() - timedelta(days=3))
    await repo.arm("agent_a", "ch2", "generic")
    assert await repo.cleanup_older_than_days(2) == 1
    rows = await db_client.get(OwnerNoticeCooldownRepository.TABLE, {"agent_id": "agent_a"})
    assert [r["target"] for r in rows] == ["ch2"]


@pytest.mark.asyncio
async def test_cleanup_older_than_days_keeps_live_windows(db_client):
    """The sweep threshold must sit far above any window: a row still inside
    its window is exactly what the table exists to remember."""
    repo = OwnerNoticeCooldownRepository(db_client)
    await repo.arm("agent_a", "ch1", "generic", at=utc_now() - timedelta(days=10))
    await repo.arm("agent_a", "ch2", "generic", at=utc_now() - timedelta(seconds=WINDOW - 60))
    assert await repo.cleanup_older_than_days(2) == 1
    assert await repo.is_cooling("agent_a", "ch2", "generic", WINDOW) is True
    assert await repo.last_notified_at("agent_a", "ch1", "generic") is None
