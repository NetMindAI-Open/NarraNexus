"""
@file_name: test_long_running_diagnostic.py
@author: Bin Liang
@date: 2026-06-01
@description: 铁律 #14 — a long-running agent_loop is a first-class scenario, not
an anomaly. The old poll step force-recovered any RUNNING job older than 30min
(recover_stuck_jobs), which would interrupt a legitimate long loop AND duplicate
its execution. Replaced with a DIAGNOSTIC-only scan: it surfaces long-runners
(for alerting) but never resets them. Orphan recovery still happens on process
start (recover_all_running_jobs), which is the only safe time to reset RUNNING.
"""
from datetime import datetime, timedelta, timezone as dt_tz

import pytest

from xyz_agent_context.repository import JobRepository
from xyz_agent_context.schema.job_schema import JobStatus
from xyz_agent_context.module.job_module.job_trigger import JobTrigger

SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'


async def _insert_running(db, job_id, started_minutes_ago):
    now = datetime.now(dt_tz.utc)
    # Pass started_at as a datetime (not a hand-formatted string) so the backend
    # serializes it the same way it serializes the comparison param.
    started = now - timedelta(minutes=started_minutes_ago)
    await db.insert("instance_jobs", {
        "job_id": job_id, "instance_id": f"ins_{job_id}",
        "agent_id": "a", "user_id": "u", "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled", "trigger_config": SCHEDULED_TRIGGER,
        "status": JobStatus.RUNNING.value, "notification_method": "inbox",
        "started_at": started,
        "created_at": now, "updated_at": now,
    })


@pytest.mark.asyncio
async def test_find_long_running_returns_old_excludes_fresh(db_client):
    repo = JobRepository(db_client)
    await _insert_running(db_client, "job_old", started_minutes_ago=120)
    await _insert_running(db_client, "job_fresh", started_minutes_ago=2)

    found = await repo.find_long_running_jobs(threshold_minutes=30)
    ids = {j.job_id for j in found}
    assert "job_old" in ids
    assert "job_fresh" not in ids


@pytest.mark.asyncio
async def test_diagnostic_does_not_reset_running(db_client):
    """The whole point: a long-running job is LEFT RUNNING (no force-recover)."""
    await _insert_running(db_client, "job_long", started_minutes_ago=120)
    trigger = JobTrigger(database_client=db_client)

    flagged = await trigger._diagnose_long_running_jobs()

    assert flagged == 1
    row = await db_client.get_one("instance_jobs", {"job_id": "job_long"})
    assert row["status"] == JobStatus.RUNNING.value  # untouched — 铁律 #14
