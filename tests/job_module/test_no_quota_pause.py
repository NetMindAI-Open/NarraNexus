"""
@file_name: test_no_quota_pause.py
@author: Bin Liang
@date: 2026-05-22
@description: #6 — no-quota auto-pause + resume for JobTrigger.

A recurring/ongoing job whose run fails because the owner's free-tier quota is
exhausted (and no own provider is configured) must NOT be rescheduled — that
re-fires every interval into the same wall (the infinite-loop bug). It is
paused (PAUSED_NO_QUOTA) and auto-resumed when the owner can run again. Transient
failures must still reschedule.
"""
from datetime import datetime, timezone as dt_tz

import pytest

from xyz_agent_context.repository import JobRepository
from xyz_agent_context.schema.job_schema import JobStatus
from xyz_agent_context.module.job_module.job_trigger import (
    JobTrigger,
    _is_no_quota_failure,
)

SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'


async def _insert_job(db, job_id, status="active"):
    now = datetime(2026, 5, 22, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    await db.insert("instance_jobs", {
        "job_id": job_id,
        "instance_id": f"ins_{job_id}",
        "agent_id": "agent_1",
        "user_id": "user_1",
        "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": SCHEDULED_TRIGGER,
        "status": status,
        "notification_method": "inbox",
        "created_at": now,
        "updated_at": now,
    })


# ── pure detection ────────────────────────────────────────────────────────────

def test_is_no_quota_failure_by_error_type():
    assert _is_no_quota_failure({"success": False, "error_type": "QuotaExceededError"})
    assert _is_no_quota_failure({"success": False, "error_type": "NoProviderConfiguredError"})
    assert _is_no_quota_failure({"success": False, "error_type": "FreeTierExhaustedError"})


def test_is_no_quota_failure_by_message_fallback():
    assert _is_no_quota_failure({"success": False, "error": "Free quota exhausted. Configure your own provider."})


def test_is_no_quota_failure_false_for_success_and_transient():
    assert not _is_no_quota_failure({"success": True})
    assert not _is_no_quota_failure({"success": False, "error_type": "TimeoutError", "error": "read timed out"})
    assert not _is_no_quota_failure({"success": False, "error_type": "ConnectionError", "error": "boom"})


# ── pause path ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finalize_pauses_on_quota_failure(db_client):
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_q1")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_q1")

    await trigger._finalize_job_execution(job, {
        "success": False,
        "error_type": "QuotaExceededError",
        "error": "Free quota exhausted.",
        "event_id": None,
    })

    row = await db_client.get_one("instance_jobs", {"job_id": "job_q1"})
    assert row["status"] == JobStatus.PAUSED_NO_QUOTA.value


@pytest.mark.asyncio
async def test_finalize_cools_on_transient_failure(db_client):
    """Transient (non-quota) failures must NOT pause for quota, and (since batch
    ②) no longer reschedule straight to ACTIVE either — they enter COOLING with
    exponential backoff so a persistently-failing job stops spinning every
    interval. See test_failure_backoff for the full backoff behavior."""
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_t1")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_t1")

    await trigger._finalize_job_execution(job, {
        "success": False,
        "error_type": "TimeoutError",
        "error": "read timed out",
        "event_id": None,
    })

    row = await db_client.get_one("instance_jobs", {"job_id": "job_t1"})
    assert row["status"] == JobStatus.COOLING.value  # backoff, not paused, not active


@pytest.mark.asyncio
async def test_paused_jobs_are_not_due(db_client):
    """get_due_jobs filters PENDING/ACTIVE, so a paused job never fires."""
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_p1", status=JobStatus.PAUSED_NO_QUOTA.value)
    # next_run in the past so it WOULD be due if status allowed it
    await db_client.update("instance_jobs", {"job_id": "job_p1"},
                           {"next_run_time": "2000-01-01T00:00:00Z"})
    due = await repo.get_due_jobs()
    assert all(j.job_id != "job_p1" for j in due)
    paused = await repo.get_jobs_by_status(JobStatus.PAUSED_NO_QUOTA)
    assert any(j.job_id == "job_p1" for j in paused)


# ── resume path ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resume_flips_paused_to_active_when_user_can_run(db_client, monkeypatch):
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_r1", status=JobStatus.PAUSED_NO_QUOTA.value)
    trigger = JobTrigger(database_client=db_client)

    async def _can_run(_uid):
        return True
    monkeypatch.setattr(trigger, "_user_can_run", _can_run)

    resumed = await trigger._resume_eligible_no_quota_jobs()
    assert resumed == 1
    row = await db_client.get_one("instance_jobs", {"job_id": "job_r1"})
    assert row["status"] == JobStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_resume_leaves_paused_when_user_still_cannot_run(db_client, monkeypatch):
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_r2", status=JobStatus.PAUSED_NO_QUOTA.value)
    trigger = JobTrigger(database_client=db_client)

    async def _cannot_run(_uid):
        return False
    monkeypatch.setattr(trigger, "_user_can_run", _cannot_run)

    resumed = await trigger._resume_eligible_no_quota_jobs()
    assert resumed == 0
    row = await db_client.get_one("instance_jobs", {"job_id": "job_r2"})
    assert row["status"] == JobStatus.PAUSED_NO_QUOTA.value
