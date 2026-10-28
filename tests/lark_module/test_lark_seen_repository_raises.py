"""
@file_name: test_lark_seen_repository_raises.py
@author: Bin Liang
@date: 2026-04-21
@description: Pin the H-3 fix: repo raises on unknown backend errors.

Before the fix, `LarkSeenMessageRepository.mark_seen` caught ALL
exceptions and returned False. That swallowed transient DB failures
as "already seen" (fail-closed) — silently dropping fresh events, which
contradicted the trigger-layer intent of fail-OPEN on DB trouble ("silent
loss is worse than double-processing").

After the fix:
  - UNIQUE constraint violation → return False (genuine dup, as before)
  - Any other exception → RAISE so the trigger layer can decide.

The trigger's `_should_process_event` has its own try/except that turns
the raise into fail-open, which matches the documented design intent.
"""
from __future__ import annotations

import pytest

from narranexus.platform.repository.lark_seen_message_repository import (
    LarkSeenMessageRepository,
)


class _FakeSqliteUniqueBackend:
    """Mimics aiosqlite's unique-constraint exception text."""

    async def insert(self, _table, _row):
        raise Exception("UNIQUE constraint failed: lark_seen_messages.message_id")


class _FakeMysqlUniqueBackend:
    """Mimics aiomysql's unique-constraint exception text."""

    async def insert(self, _table, _row):
        raise Exception("(1062, \"Duplicate entry 'om_1' for key 'message_id'\")")


class _FakeConnectionLostBackend:
    """A non-UNIQUE exception — transient DB trouble."""

    async def insert(self, _table, _row):
        raise ConnectionError("Lost connection to MySQL server during query")


@pytest.mark.asyncio
async def test_unique_sqlite_returns_false():
    repo = LarkSeenMessageRepository(_FakeSqliteUniqueBackend())
    assert await repo.mark_seen("om_1") is False


@pytest.mark.asyncio
async def test_unique_mysql_returns_false():
    repo = LarkSeenMessageRepository(_FakeMysqlUniqueBackend())
    assert await repo.mark_seen("om_1") is False


@pytest.mark.asyncio
async def test_non_unique_error_raises():
    """Regression guard for H-3: transient backend errors must escape
    the repo so the trigger can fail-open instead of silently dropping
    fresh events."""
    repo = LarkSeenMessageRepository(_FakeConnectionLostBackend())
    with pytest.raises(ConnectionError):
        await repo.mark_seen("om_2")
