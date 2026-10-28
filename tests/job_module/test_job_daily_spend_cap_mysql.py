"""
@file_name: test_job_daily_spend_cap_mysql.py
@author: Bin Liang
@date: 2026-09-09
@description: Real-MySQL dialect twin for `_daily_spend_usd_for_user`'s raw
SQL (B-14). `%s` placeholders + an aggregate `COALESCE(SUM(...))` read
correctly under SQLite's rewritten-text execution path regardless of
dialect-specific quirks; this twin proves the same query against real MySQL,
including the string cutoff against a DATETIME(6) column and the local-day
boundary (review I6). It also pins the assumption the cutoff arithmetic rests
on: the DB session clock is UTC (`cost_records.created_at` is filled by
`CURRENT_TIMESTAMP(6)`, which is the SESSION time zone).

Enable with NARRANEXUS_MYSQL_TEST_URL (same convention as the project's other
*_mysql.py twins — see tests/mysql_dialect.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_tz

import pytest
import pytest_asyncio

from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate
from narranexus_plugins.job_module.job_trigger import _daily_spend_usd_for_user
from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "that the daily-spend-cap aggregate query "
        "(COALESCE(SUM(total_cost_usd)) WHERE user_id = %s AND created_at >= %s) "
        "runs correctly against the real MySQL dialect"
    ),
)

_PREFIX = "spendcap"


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    try:
        await client.execute(
            "DELETE FROM cost_records WHERE user_id LIKE %s", (f"{_PREFIX}%",),
            fetch=False,
        )
    except Exception:  # noqa: BLE001 — teardown must not mask a failure
        pass
    await client.close()


async def _insert_cost_record(db, user_id, cost_usd, created_at=None):
    await db.insert("cost_records", {
        "agent_id": "agent_1",
        "call_type": "agent_loop",
        "model": "test-model",
        "input_tokens": 1000,
        "output_tokens": 100,
        "total_cost_usd": cost_usd,
        "user_id": user_id,
        "created_at": created_at or datetime.now(dt_tz.utc),
    })


@pytest.mark.asyncio
async def test_sums_only_todays_records_for_the_user_on_mysql(mysql_client):
    user = f"{_PREFIX}_u1"
    await _insert_cost_record(mysql_client, user, 1.5)
    await _insert_cost_record(mysql_client, user, 2.25)
    yesterday = datetime.now(dt_tz.utc) - timedelta(days=1)
    await _insert_cost_record(mysql_client, user, 100.0, created_at=yesterday)
    await _insert_cost_record(mysql_client, f"{_PREFIX}_u2", 50.0)

    total = await _daily_spend_usd_for_user(mysql_client, user)

    assert total == pytest.approx(3.75)


@pytest.mark.asyncio
async def test_no_records_returns_zero_on_mysql(mysql_client):
    assert await _daily_spend_usd_for_user(mysql_client, f"{_PREFIX}_nobody") == 0.0


FIXED_NOW = datetime(2026, 9, 10, 20, 0, 0, tzinfo=dt_tz.utc)


@pytest.mark.asyncio
async def test_sums_from_the_local_midnight_of_the_jobs_timezone_on_mysql(mysql_client):
    """Asia/Shanghai's local midnight for FIXED_NOW is 16:00Z: a 15:59Z row is
    yesterday there and today in UTC. Proves the `YYYY-MM-DD HH:MM:SS` cutoff
    literal compares correctly against DATETIME(6) on the real dialect."""
    user = f"{_PREFIX}_tz"
    await _insert_cost_record(
        mysql_client, user, 1.0, created_at=datetime(2026, 9, 10, 15, 59, tzinfo=dt_tz.utc)
    )
    await _insert_cost_record(
        mysql_client, user, 2.0, created_at=datetime(2026, 9, 10, 16, 1, tzinfo=dt_tz.utc)
    )
    # Space-separated literal — the shape the column default writes.
    await _insert_cost_record(mysql_client, user, 3.0, created_at="2026-09-10 16:30:00")

    shanghai = await _daily_spend_usd_for_user(mysql_client, user, "Asia/Shanghai", now=FIXED_NOW)
    utc = await _daily_spend_usd_for_user(mysql_client, user, "UTC", now=FIXED_NOW)

    assert shanghai == pytest.approx(5.0)
    assert utc == pytest.approx(6.0)


@pytest.mark.asyncio
async def test_default_timestamp_rows_are_counted_on_mysql(mysql_client):
    """cost_tracker leaves created_at to the column default
    (CURRENT_TIMESTAMP(6)); such a row must count as today's spend."""
    user = f"{_PREFIX}_default_ts"
    await mysql_client.insert("cost_records", {
        "agent_id": "agent_1", "call_type": "agent_loop", "model": "m",
        "input_tokens": 1, "output_tokens": 1, "total_cost_usd": 4.0,
        "user_id": user,
    })
    assert await _daily_spend_usd_for_user(mysql_client, user, "UTC") == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_db_session_clock_is_utc(mysql_client):
    """The day-boundary arithmetic assumes `CURRENT_TIMESTAMP(6)` (the session
    clock behind `cost_records.created_at`'s default) is UTC. A server whose
    session time zone is not UTC shifts the whole window by its offset — this
    must fail HERE, not silently in production."""
    rows = await mysql_client.execute(
        "SELECT TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW()) AS skew, "
        "@@session.time_zone AS session_tz, @@system_time_zone AS system_tz",
        fetch=True,
    )
    assert abs(int(rows[0]["skew"])) < 5, (
        f"MySQL session clock is not UTC (session={rows[0]['session_tz']}, "
        f"system={rows[0]['system_tz']}); the spend-cap day window would be shifted"
    )
