"""
@file_name: test_job_pause_resume.py
@author: Bin Liang
@date: 2026-06-01
@description: Batch ③ — user pause / resume core logic
(job_recovery.pause_job / resume_job), called by the authed dashboard route.
Closes the gap where a paused job (incl. the 2026-05-31 bleed-stop jobs) had no
path back: the old dashboard resume only handled `paused` and used SQLite-only
SQL (broken on prod MySQL). The portable core handles all the auto-paused states
and recomputes next_run.
"""
from datetime import datetime, timezone as dt_tz

import pytest

from xyz_agent_context.schema.job_schema import JobStatus
from xyz_agent_context.module.job_module.job_recovery import pause_job, resume_job

SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'


async def _insert(db, job_id, status, failure_count=0):
    now = datetime(2026, 6, 1, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    await db.insert("instance_jobs", {
        "job_id": job_id, "instance_id": f"ins_{job_id}",
        "agent_id": "a", "user_id": "u", "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled", "trigger_config": SCHEDULED_TRIGGER,
        "status": status, "notification_method": "inbox",
        "consecutive_failure_count": failure_count,
        "created_at": now, "updated_at": now,
    })


@pytest.mark.asyncio
async def test_pause_active_job(db_client):
    await _insert(db_client, "job_p1", JobStatus.ACTIVE.value)
    ok, _ = await pause_job("job_p1", db_client)
    assert ok is True
    row = await db_client.get_one("instance_jobs", {"job_id": "job_p1"})
    assert row["status"] == JobStatus.PAUSED.value
    assert row["paused_reason"] == "user"


@pytest.mark.asyncio
async def test_cannot_pause_completed_job(db_client):
    await _insert(db_client, "job_p2", JobStatus.COMPLETED.value)
    ok, _ = await pause_job("job_p2", db_client)
    assert ok is False


@pytest.mark.asyncio
async def test_resume_paused_no_quota_job(db_client):
    """The bleed-stop scenario: a paused job becomes runnable again on resume."""
    await _insert(db_client, "job_p3", JobStatus.PAUSED_NO_QUOTA.value)
    ok, _ = await resume_job("job_p3", db_client)
    assert ok is True
    row = await db_client.get_one("instance_jobs", {"job_id": "job_p3"})
    assert row["status"] == JobStatus.ACTIVE.value
    assert row["paused_reason"] is None


@pytest.mark.asyncio
async def test_resume_cooling_job_clears_backoff(db_client):
    await _insert(db_client, "job_p4", JobStatus.COOLING.value, failure_count=4)
    ok, _ = await resume_job("job_p4", db_client)
    assert ok is True
    row = await db_client.get_one("instance_jobs", {"job_id": "job_p4"})
    assert row["status"] == JobStatus.ACTIVE.value
    assert (row["consecutive_failure_count"] or 0) == 0


@pytest.mark.asyncio
async def test_cannot_resume_running_job(db_client):
    await _insert(db_client, "job_p5", JobStatus.RUNNING.value)
    ok, _ = await resume_job("job_p5", db_client)
    assert ok is False
