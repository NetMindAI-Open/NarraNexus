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


def test_real_resolver_messages_still_pause_jobs():
    """The message-substring layer must catch the resolver's ACTUAL strings.

    The test above pins a hand-written literal, so rewording the real
    exceptions cannot break it — which is exactly the hole this closes. A job
    whose failure carries only the message (no error_type, e.g. serialized
    across a subprocess boundary) relies on this layer to reach
    PAUSED_NO_QUOTA; miss it and the job retry-storms instead (upstream
    incident: 9 users / 14 days / 390 retries).

    So: build the errors for real and assert the classifier still fires.
    Anyone rewording them has to keep the markers intact or turn this red.
    """
    from xyz_agent_context.agent_framework.provider_resolver import (
        NoProviderConfiguredError,
        QuotaExceededError,
    )

    for exc in (QuotaExceededError("u1"), NoProviderConfiguredError("u1")):
        assert _is_no_quota_failure({"success": False, "error": str(exc)}), (
            f"{type(exc).__name__} message no longer matches any "
            f"_NO_QUOTA_ERROR_MARKERS entry: {str(exc)!r}"
        )


def test_is_no_quota_failure_false_for_success_and_transient():
    assert not _is_no_quota_failure({"success": True})
    assert not _is_no_quota_failure({"success": False, "error_type": "TimeoutError", "error": "read timed out"})
    assert not _is_no_quota_failure({"success": False, "error_type": "ConnectionError", "error": "boom"})


def test_is_no_quota_failure_by_self_serviceable():
    """A background job must pause (not storm-retry) on any deterministic
    self-serviceable failure — provider-agnostic balance/quota, context-window,
    model-not-found (reuses #110's classify_self_serviceable). This is the
    upstream 390-retry-storm fix."""
    # balance / quota, across providers
    assert _is_no_quota_failure({"success": False, "error": "Error code: 402 - Insufficient Balance"})
    assert _is_no_quota_failure({"success": False, "error": "insufficient_quota"})
    assert _is_no_quota_failure({"success": False, "error": "Your credit balance is too low to access the Claude API"})
    assert _is_no_quota_failure({"success": False, "error": "You exceeded your current quota, please check your plan and billing"})
    # context window too small
    assert _is_no_quota_failure({"success": False, "error": "This model's maximum context length is 8192 tokens"})
    # model not found
    assert _is_no_quota_failure({"success": False, "error": "The model `x` does not exist or you do not have access to it"})
    # exact SDK enum type still routes
    assert _is_no_quota_failure({"success": False, "error_type": "billing_error", "error": ""})


def test_is_no_quota_failure_false_for_bare_rate_limit():
    """A bare 429 / rate-limit is TRANSIENT (self-heals) — must NOT pause, or a
    momentary rate-limit would wrongly park the job as no-quota."""
    assert not _is_no_quota_failure({"success": False, "error": "429 Too Many Requests"})
    assert not _is_no_quota_failure({"success": False, "error_type": "RateLimitError", "error": "rate limit exceeded"})


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
async def test_finalize_pauses_on_balance_failure(db_client):
    """The upstream incident: a background job whose provider reports insufficient
    balance must PAUSE (not cycle COOLING/re-arm forever)."""
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_bal")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_bal")

    await trigger._finalize_job_execution(job, {
        "success": False,
        "error_type": "APIStatusError",
        "error": "Error code: 402 - {'error': 'Insufficient Balance'}",
        "event_id": None,
    })

    row = await db_client.get_one("instance_jobs", {"job_id": "job_bal"})
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
async def test_finalize_balance_pause_records_reason(db_client):
    """The pause must record WHY (insufficient_balance) so the resume path can
    tell a readiness-observable fix from one it can't (balance top-up)."""
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_reason")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_reason")

    await trigger._finalize_job_execution(job, {
        "success": False,
        "error_type": "APIStatusError",
        "error": "Error code: 402 - Insufficient Balance",
        "event_id": None,
    })

    row = await db_client.get_one("instance_jobs", {"job_id": "job_reason"})
    assert row["status"] == JobStatus.PAUSED_NO_QUOTA.value
    assert row["paused_reason"] == "insufficient_balance"


@pytest.mark.asyncio
async def test_backstop_does_not_blind_probe_balance_pause(db_client, monkeypatch):
    """Time backstop must NOT re-arm a balance/self-serviceable pause even when
    the user 'can run' (config complete) — readiness can't see a top-up, so
    re-arming is the retry storm. These resume only on the edge."""
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_bal_paused", status=JobStatus.PAUSED_NO_QUOTA.value)
    await db_client.update("instance_jobs", {"job_id": "job_bal_paused"},
                           {"paused_reason": "insufficient_balance"})
    trigger = JobTrigger(database_client=db_client)

    async def _can_run(_uid):
        return True  # balance-0 user still reads config-complete
    monkeypatch.setattr(trigger, "_user_can_run", _can_run)

    resumed = await trigger._resume_eligible_no_quota_jobs()
    assert resumed == 0
    row = await db_client.get_one("instance_jobs", {"job_id": "job_bal_paused"})
    assert row["status"] == JobStatus.PAUSED_NO_QUOTA.value  # still paused


@pytest.mark.asyncio
async def test_backstop_still_resumes_quota_pause(db_client, monkeypatch):
    """Non-self-serviceable reasons (legacy quota / auth) still resume via the
    time backstop — reconfiguring a provider changes config, which readiness
    DOES observe. (Guards that the skip is narrow.)"""
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_quota_paused", status=JobStatus.PAUSED_NO_QUOTA.value)
    await db_client.update("instance_jobs", {"job_id": "job_quota_paused"},
                           {"paused_reason": "no_quota"})
    trigger = JobTrigger(database_client=db_client)

    async def _can_run(_uid):
        return True
    monkeypatch.setattr(trigger, "_user_can_run", _can_run)

    resumed = await trigger._resume_eligible_no_quota_jobs()
    assert resumed == 1
    row = await db_client.get_one("instance_jobs", {"job_id": "job_quota_paused"})
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
