"""
@file_name: test_failure_backoff.py
@author: Bin Liang
@date: 2026-06-01
@description: Batch ② — transient-failure exponential backoff (COOLING) and
escalation to FAILED. A non-quota run failure no longer reschedules straight
back to ACTIVE (which let a persistently-failing job spin every interval).
It goes to COOLING with `cooldown_until = now + backoff`, the poller re-arms it
to ACTIVE once the cooldown elapses, and after a consecutive-failure cap it
escalates to FAILED instead of cooling forever. A success resets the counter.

铁律 #14: this is scheduler-level retry spacing, NOT an agent_loop time/iteration
cap — a long-running loop that eventually succeeds is unaffected; only a run
that finishes AND failed accrues backoff.
"""
from datetime import datetime, timedelta, timezone as dt_tz

import pytest

from xyz_agent_context.repository import JobRepository
from xyz_agent_context.schema.job_schema import JobStatus
from xyz_agent_context.module.job_module.job_trigger import (
    JobTrigger,
    _compute_cooldown_seconds,
    _MAX_CONSECUTIVE_FAILURES,
)

SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'


async def _insert_job(db, job_id, status="active", failure_count=0, cooldown_until=None):
    now = datetime(2026, 6, 1, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    row = {
        "job_id": job_id, "instance_id": f"ins_{job_id}",
        "agent_id": "agent_1", "user_id": "user_1",
        "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled", "trigger_config": SCHEDULED_TRIGGER,
        "status": status, "notification_method": "inbox",
        "consecutive_failure_count": failure_count,
        "created_at": now, "updated_at": now,
    }
    if cooldown_until is not None:
        row["cooldown_until"] = cooldown_until
    await db.insert("instance_jobs", row)


def _transient(msg="read timed out", etype="TimeoutError"):
    return {"success": False, "error_type": etype, "error": msg, "event_id": None}


# ── backoff curve ───────────────────────────────────────────────────────────

def test_cooldown_curve_is_exponential_and_capped():
    assert _compute_cooldown_seconds(1) == 60
    assert _compute_cooldown_seconds(2) == 120
    assert _compute_cooldown_seconds(3) == 240
    # grows ×2 then clamps at the 1h cap
    assert _compute_cooldown_seconds(99) == 3600


# ── transient → cooling ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transient_failure_goes_to_cooling_with_backoff(db_client):
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_c1", failure_count=0)
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_c1")

    await trigger._finalize_job_execution(job, _transient())

    row = await db_client.get_one("instance_jobs", {"job_id": "job_c1"})
    assert row["status"] == JobStatus.COOLING.value
    assert row["consecutive_failure_count"] == 1
    assert row["cooldown_until"] is not None      # retry scheduled
    assert row["next_run_time"] is not None        # due at the cooldown time


@pytest.mark.asyncio
async def test_consecutive_failures_escalate_to_failed_at_cap(db_client):
    repo = JobRepository(db_client)
    # already failed cap-1 times; this run is the cap-th
    await _insert_job(db_client, "job_c2", failure_count=_MAX_CONSECUTIVE_FAILURES - 1)
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_c2")

    await trigger._finalize_job_execution(job, _transient())

    row = await db_client.get_one("instance_jobs", {"job_id": "job_c2"})
    assert row["status"] == JobStatus.FAILED.value
    assert row["consecutive_failure_count"] == _MAX_CONSECUTIVE_FAILURES
    assert row["paused_reason"] == "repeated_failure"


@pytest.mark.asyncio
async def test_success_resets_failure_count(db_client):
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_c3", failure_count=3)
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_c3")

    await trigger._finalize_job_execution(job, {"success": True, "event_id": None, "content": "ok"})

    row = await db_client.get_one("instance_jobs", {"job_id": "job_c3"})
    assert row["consecutive_failure_count"] == 0
    assert row["status"] == JobStatus.ACTIVE.value  # scheduled → rescheduled normally


# ── cooling re-arm (time-based recovery) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_rearm_flips_cooling_to_active_when_cooldown_elapsed(db_client):
    past = (datetime.now(dt_tz.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    await _insert_job(db_client, "job_c4", status=JobStatus.COOLING.value,
                      failure_count=2, cooldown_until=past)
    trigger = JobTrigger(database_client=db_client)

    rearmed = await trigger._rearm_cooled_jobs()

    assert rearmed == 1
    row = await db_client.get_one("instance_jobs", {"job_id": "job_c4"})
    assert row["status"] == JobStatus.ACTIVE.value
    assert row["consecutive_failure_count"] == 2  # budget preserved across retries


@pytest.mark.asyncio
async def test_rearm_leaves_cooling_when_cooldown_in_future(db_client):
    future = (datetime.now(dt_tz.utc) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    await _insert_job(db_client, "job_c5", status=JobStatus.COOLING.value,
                      failure_count=1, cooldown_until=future)
    trigger = JobTrigger(database_client=db_client)

    rearmed = await trigger._rearm_cooled_jobs()

    assert rearmed == 0
    row = await db_client.get_one("instance_jobs", {"job_id": "job_c5"})
    assert row["status"] == JobStatus.COOLING.value
