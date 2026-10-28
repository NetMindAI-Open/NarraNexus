"""
@file_name: test_owner_notice_cooldown_repository_mysql.py
@date: 2026-09-09
@description: Real-MySQL twin for OwnerNoticeCooldownRepository — the composite
primary key, the DATETIME(6) round-trip through `coerce_utc`, and the
update-then-insert arm on the real dialect. Enable with
NARRANEXUS_MYSQL_TEST_URL; skipped otherwise.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio

from narranexus.platform.repository.owner_notice_cooldown_repository import (
    OwnerNoticeCooldownRepository,
)
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate
from narranexus.platform.utils.timezone import utc_now
from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "that owner_notice_cooldowns' composite key, DATETIME(6) round-trip and "
        "update-then-insert arm behave on the real MySQL dialect"
    ),
)

AGENT = "mysqlcooldown_agent"
WINDOW = 1800


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)

    async def _cleanup():
        await client.delete(OwnerNoticeCooldownRepository.TABLE, {"agent_id": AGENT})

    await _cleanup()
    yield client
    await _cleanup()
    await client.close()


@pytest.mark.asyncio
async def test_arm_cool_expire_roundtrip(mysql_client):
    repo = OwnerNoticeCooldownRepository(mysql_client)
    assert await repo.is_cooling(AGENT, "ch1", "generic", WINDOW) is False

    await repo.arm(AGENT, "ch1", "generic")
    assert await repo.is_cooling(AGENT, "ch1", "generic", WINDOW) is True
    assert await repo.is_cooling(AGENT, "ch2", "generic", WINDOW) is False

    await repo.arm(AGENT, "ch1", "generic", at=utc_now() - timedelta(seconds=WINDOW + 5))
    assert await repo.is_cooling(AGENT, "ch1", "generic", WINDOW) is False
    rows = await mysql_client.get(OwnerNoticeCooldownRepository.TABLE, {"agent_id": AGENT})
    assert len(rows) == 1

    # Retention DELETE on the real dialect: the expired row above is well
    # inside 2 days, so nothing goes; backdate it past the bound and it does.
    assert await repo.cleanup_older_than_days(2) == 0
    await repo.arm(AGENT, "ch1", "generic", at=utc_now() - timedelta(days=3))
    assert await repo.cleanup_older_than_days(2) == 1
