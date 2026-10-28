"""
@file_name: test_job_repository_live_statuses_mysql.py
@author: Bin Liang
@date: 2026-09-10
@description: Real-MySQL twin for the four LIVE_JOB_STATUSES reads in
JobRepository (review I5): the parameterised `status IN (%s, ...)` clause
spliced into each query, plus the user_clause variant of
get_active_jobs_by_agent. Enable with NARRANEXUS_MYSQL_TEST_URL.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from narranexus.platform.repository import JobRepository
from narranexus.platform.schema.job_schema import LIVE_JOB_STATUSES, JobStatus
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate
from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "that the four LIVE_JOB_STATUSES reads (duplicate title, per-narrative, "
        "per-agent, summary) select blocked/cooling and skip paused/terminal on MySQL"
    ),
)

_PREFIX = "livestatus"
AGENT = f"{_PREFIX}_agent"
USER = f"{_PREFIX}_user"
NARRATIVE = f"{_PREFIX}_nar"
SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)

    async def _cleanup():
        await client.execute(
            "DELETE FROM instance_jobs WHERE agent_id = %s", (AGENT,), fetch=False,
        )

    await _cleanup()
    yield client
    await _cleanup()
    await client.close()


async def _seed(db, job_id, status):
    await db.insert("instance_jobs", {
        "job_id": f"{_PREFIX}_{job_id}", "instance_id": f"ins_{_PREFIX}_{job_id}",
        "agent_id": AGENT, "user_id": USER, "title": f"t_{job_id}", "description": "d",
        "payload": "p", "job_type": "scheduled", "trigger_config": SCHEDULED_TRIGGER,
        "status": status, "notification_method": "inbox", "narrative_id": NARRATIVE,
        "next_run_time": "2030-01-01 00:00:00",
    })


@pytest.mark.asyncio
async def test_the_four_reads_agree_on_the_live_set_on_mysql(mysql_client):
    visible = [s.value for s in LIVE_JOB_STATUSES]
    hidden = [s.value for s in JobStatus if s not in LIVE_JOB_STATUSES]
    for status in visible + hidden:
        await _seed(mysql_client, status, status)
    repo = JobRepository(mysql_client)

    expected = {f"{_PREFIX}_{s}" for s in visible}
    assert {j.job_id for j in await repo.get_active_jobs_by_narrative(NARRATIVE)} == expected
    assert {j.job_id for j in await repo.get_active_jobs_by_agent(AGENT, user_id=USER)} == expected
    assert {j.job_id for j in await repo.get_active_jobs_by_agent(AGENT)} == expected
    assert {r["job_id"] for r in await repo.get_active_jobs_summary(AGENT, USER, limit=50)} == expected
    for status in visible:
        found = await repo.find_active_by_title(AGENT, USER, f"t_{status}")
        assert found is not None and found.job_id == f"{_PREFIX}_{status}"
    for status in hidden:
        assert await repo.find_active_by_title(AGENT, USER, f"t_{status}") is None
