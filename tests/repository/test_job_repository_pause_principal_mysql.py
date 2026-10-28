"""
@file_name: test_job_repository_pause_principal_mysql.py
@author: Bin Liang
@date: 2026-09-10
@description: Real-MySQL twin for `JobRepository.pause_jobs_for_execution_principal`
(B-13, review I2/I3) and its inverse read
`get_jobs_paused_for_execution_principal` (review r2 I-B): the OR/IS NULL
principal predicate, the DATETIME(6) literal for paused_at/updated_at, the
UPDATE rowcount and the `paused_reason IN (...)` filter on the real dialect. Enable with NARRANEXUS_MYSQL_TEST_URL.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from narranexus.platform.repository import JobRepository
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate
from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "that the batched principal-scoped UPDATE behind an account suspension "
        "(OR / IS NULL predicate, DATETIME(6) literals, rowcount) and the reinstate read "
        "behaves on the real MySQL dialect"
    ),
)

_PREFIX = "pauseprincipal"
BANNED = f"{_PREFIX}_banned"
OTHER = f"{_PREFIX}_other"
SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)

    async def _cleanup():
        await client.execute(
            "DELETE FROM instance_jobs WHERE job_id LIKE %s", (f"{_PREFIX}%",), fetch=False,
        )

    await _cleanup()
    yield client
    await _cleanup()
    await client.close()


async def _seed_job(db, job_id, user_id, *, status="active", related_entity_id=None,
                    paused_reason=None):
    row = {
        "job_id": f"{_PREFIX}_{job_id}", "instance_id": f"ins_{_PREFIX}_{job_id}",
        "agent_id": "agent_1", "user_id": user_id, "title": "t", "description": "d",
        "payload": "p", "job_type": "scheduled", "trigger_config": SCHEDULED_TRIGGER,
        "status": status, "notification_method": "inbox",
    }
    if related_entity_id is not None:
        row["related_entity_id"] = related_entity_id
    if paused_reason is not None:
        row["paused_reason"] = paused_reason
    await db.insert("instance_jobs", row)


async def _status(db, job_id):
    row = await db.get_one("instance_jobs", {"job_id": f"{_PREFIX}_{job_id}"})
    return row["status"], row["paused_reason"]


@pytest.mark.asyncio
async def test_principal_predicate_and_rowcount_on_mysql(mysql_client):
    await _seed_job(mysql_client, "own", BANNED, status="active")
    await _seed_job(mysql_client, "delegated", OTHER, status="active", related_entity_id=BANNED)
    await _seed_job(mysql_client, "runs_as_other", BANNED, status="active", related_entity_id=OTHER)
    await _seed_job(mysql_client, "done", BANNED, status="completed")
    await _seed_job(mysql_client, "null_reason", BANNED, status="paused")
    await _seed_job(mysql_client, "already", BANNED, status="paused", paused_reason="banned")
    await _seed_job(mysql_client, "other", OTHER, status="active")
    await _seed_job(mysql_client, "cooling", BANNED, status="cooling")
    for untouched in ("blocked", "blocked_failed", "running", "paused_no_quota", "paused_spend_cap"):
        await _seed_job(mysql_client, untouched, BANNED, status=untouched)

    paused = await JobRepository(mysql_client).pause_jobs_for_execution_principal(BANNED, "banned")

    # own + delegated + cooling; already-paused rows keep their reason and the
    # non-schedulable statuses are never flattened (review I1).
    assert paused == 3
    assert await _status(mysql_client, "cooling") == ("paused", "banned")
    for untouched in ("blocked", "blocked_failed", "running", "paused_no_quota", "paused_spend_cap"):
        assert await _status(mysql_client, untouched) == (untouched, None)
    assert await _status(mysql_client, "own") == ("paused", "banned")
    assert await _status(mysql_client, "delegated") == ("paused", "banned")
    assert await _status(mysql_client, "null_reason") == ("paused", None)
    assert await _status(mysql_client, "runs_as_other") == ("active", None)
    assert (await _status(mysql_client, "done"))[0] == "completed"
    assert await _status(mysql_client, "other") == ("active", None)
    row = await mysql_client.get_one("instance_jobs", {"job_id": f"{_PREFIX}_own"})
    assert row["paused_at"] is not None


@pytest.mark.asyncio
async def test_second_call_is_a_no_op_on_mysql(mysql_client):
    await _seed_job(mysql_client, "twice", BANNED, status="active")
    repo = JobRepository(mysql_client)

    assert await repo.pause_jobs_for_execution_principal(BANNED, "banned") == 1
    assert await repo.pause_jobs_for_execution_principal(BANNED, "banned") == 0


@pytest.mark.asyncio
async def test_reinstate_read_on_mysql(mysql_client):
    await _seed_job(mysql_client, "own", BANNED, status="paused", paused_reason="banned")
    await _seed_job(mysql_client, "own_empty_rel", BANNED, status="paused", paused_reason="banned",
                    related_entity_id="")
    await _seed_job(mysql_client, "delegated", OTHER, status="paused", paused_reason="banned",
                    related_entity_id=BANNED)
    await _seed_job(mysql_client, "gate_deleted", BANNED, status="paused", paused_reason="deleted")
    await _seed_job(mysql_client, "user_paused", BANNED, status="paused", paused_reason="user")
    await _seed_job(mysql_client, "null_reason", BANNED, status="paused")
    await _seed_job(mysql_client, "runs_as_other", BANNED, status="paused", paused_reason="banned",
                    related_entity_id=OTHER)
    await _seed_job(mysql_client, "active", BANNED, status="active")

    jobs = await JobRepository(mysql_client).get_jobs_paused_for_execution_principal(
        BANNED, ("banned", "blocked", "deleted"),
    )

    assert sorted(j.job_id for j in jobs) == sorted(
        f"{_PREFIX}_{n}" for n in ("own", "own_empty_rel", "delegated", "gate_deleted")
    )
