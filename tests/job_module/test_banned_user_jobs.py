"""
@file_name: test_banned_user_jobs.py
@author: Bin Liang
@date: 2026-09-09
@description: B-13 — a banned user's scheduled jobs must not keep running.

Root cause: neither `JobTrigger._user_can_run` (the PAUSED_NO_QUOTA resume
gate) nor `_poll_and_enqueue` (the due-job scan) ever consulted `users.status`.
A banned user whose job kept a valid provider config re-fired every interval
straight into `Key is blocked` 401s. Fix: both paths must treat a
non-transacting owner (banned / blocked / deleted — the shared
NON_TRANSACTING_USER_STATUSES, review I1) as "cannot run" — the poll path
additionally pauses the job (`paused_reason=<the status>`) so it stops
appearing in `get_due_jobs()` at all.
"""
from datetime import datetime, timezone as dt_tz

import pytest

from narranexus.platform.repository import JobRepository
from narranexus.platform.schema.job_schema import JobStatus
from narranexus_plugins.job_module.job_trigger import JobTrigger

SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'


async def _seed_user(db, user_id, status="active"):
    await db.insert("users", {"user_id": user_id, "user_type": "individual", "status": status})


async def _insert_job(db, job_id, user_id, status="active", related_entity_id=None):
    created = datetime(2026, 9, 9, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    due = "2020-01-01T00:00:00Z"  # safely in the past — always "due" for get_due_jobs
    row = {
        "job_id": job_id,
        "instance_id": f"ins_{job_id}",
        "agent_id": "agent_1",
        "user_id": user_id,
        "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": SCHEDULED_TRIGGER,
        "status": status,
        "notification_method": "inbox",
        "next_run_time": due,
        "created_at": created,
        "updated_at": created,
    }
    if related_entity_id:
        row["related_entity_id"] = related_entity_id
    await db.insert("instance_jobs", row)


# ─────────────────────────── _user_can_run ────────────────────────────────

@pytest.mark.asyncio
async def test_user_can_run_is_false_for_banned_user(db_client, monkeypatch):
    await _seed_user(db_client, "u_banned", status="banned")
    trigger = JobTrigger(database_client=db_client)

    # Even if the provider classifier would say OK, a banned owner must never
    # be allowed to resume — the classifier must not even be consulted since
    # provider readiness is irrelevant once the account itself is banned.
    # (Using a call counter rather than raising inside the fake: `_user_can_run`
    # catches broad exceptions and would otherwise turn a wrongly-reached
    # classifier into an accidental false-negative pass.)
    calls = []

    async def _spy(uid, db):
        calls.append(uid)
        from narranexus.platform.agent_framework.providers.resolver import ProviderAvailability
        return ProviderAvailability.USER_OK

    monkeypatch.setattr(
        "narranexus.platform.agent_framework.providers.resolver.classify_provider_for_user",
        _spy,
    )
    assert await trigger._user_can_run("u_banned") is False
    assert calls == []


@pytest.mark.asyncio
async def test_user_can_run_is_unaffected_for_active_user(db_client, monkeypatch):
    from narranexus.platform.agent_framework.providers.resolver import ProviderAvailability

    await _seed_user(db_client, "u_active", status="active")

    async def _fake(uid, db):
        return ProviderAvailability.USER_OK

    monkeypatch.setattr(
        "narranexus.platform.agent_framework.providers.resolver.classify_provider_for_user",
        _fake,
    )
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._user_can_run("u_active") is True


@pytest.mark.asyncio
async def test_user_can_run_defaults_to_active_when_user_row_missing(db_client, monkeypatch):
    """No users row (legacy data / test seams) must not spuriously block a run
    — banned is an explicit opt-in state, not the absence of a row."""
    from narranexus.platform.agent_framework.providers.resolver import ProviderAvailability

    async def _fake(uid, db):
        return ProviderAvailability.USER_OK

    monkeypatch.setattr(
        "narranexus.platform.agent_framework.providers.resolver.classify_provider_for_user",
        _fake,
    )
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._user_can_run("u_no_row") is True


# ─────────────────────────── poll-and-enqueue ─────────────────────────────

@pytest.mark.asyncio
async def test_poll_and_enqueue_pauses_banned_users_due_job(db_client):
    await _seed_user(db_client, "u_banned2", status="banned")
    await _insert_job(db_client, "job_banned", "u_banned2", status="active")

    trigger = JobTrigger(database_client=db_client)
    await trigger._poll_and_enqueue()

    assert trigger._job_queue.qsize() == 0
    assert "job_banned" not in trigger._running_jobs

    repo = JobRepository(db_client)
    row = await repo.get_job("job_banned")
    assert row.status == JobStatus.PAUSED
    assert row.paused_reason == "banned"


@pytest.mark.asyncio
async def test_poll_and_enqueue_still_enqueues_active_users_due_job(db_client):
    await _seed_user(db_client, "u_active2", status="active")
    await _insert_job(db_client, "job_active", "u_active2", status="active")

    trigger = JobTrigger(database_client=db_client)
    await trigger._poll_and_enqueue()

    assert trigger._job_queue.qsize() == 1
    assert "job_active" in trigger._running_jobs

    repo = JobRepository(db_client)
    row = await repo.get_job("job_active")
    assert row.status == JobStatus.ACTIVE


@pytest.mark.asyncio
async def test_poll_and_enqueue_checks_related_entity_id_not_owner(db_client):
    """A job executed as a different principal (`related_entity_id`) must be
    gated on THAT user's ban state, not the owning `user_id`'s."""
    await _seed_user(db_client, "owner_active", status="active")
    await _seed_user(db_client, "principal_banned", status="banned")
    await _insert_job(
        db_client, "job_delegated", "owner_active",
        status="active", related_entity_id="principal_banned",
    )

    trigger = JobTrigger(database_client=db_client)
    await trigger._poll_and_enqueue()

    assert trigger._job_queue.qsize() == 0
    repo = JobRepository(db_client)
    row = await repo.get_job("job_delegated")
    assert row.status == JobStatus.PAUSED
    assert row.paused_reason == "banned"


# ─────────────── review I1: every non-transacting status, not just banned ──────

@pytest.mark.parametrize("status", ["banned", "blocked", "deleted"])
@pytest.mark.asyncio
async def test_poll_and_enqueue_pauses_every_non_transacting_status(db_client, status):
    await _seed_user(db_client, f"u_{status}", status=status)
    await _insert_job(db_client, f"job_{status}", f"u_{status}", status="active")

    trigger = JobTrigger(database_client=db_client)
    await trigger._poll_and_enqueue()

    assert trigger._job_queue.qsize() == 0
    row = await JobRepository(db_client).get_job(f"job_{status}")
    assert row.status == JobStatus.PAUSED
    # The real status is recorded, not a blanket "banned".
    assert row.paused_reason == status


@pytest.mark.parametrize("status", ["blocked", "deleted"])
@pytest.mark.asyncio
async def test_user_can_run_is_false_for_blocked_and_deleted(db_client, monkeypatch, status):
    await _seed_user(db_client, f"u_{status}_run", status=status)
    calls = []

    async def _spy(uid, db):
        calls.append(uid)
        from narranexus.platform.agent_framework.providers.resolver import ProviderAvailability
        return ProviderAvailability.USER_OK

    monkeypatch.setattr(
        "narranexus.platform.agent_framework.providers.resolver.classify_provider_for_user",
        _spy,
    )
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._user_can_run(f"u_{status}_run") is False
    assert calls == []


@pytest.mark.asyncio
async def test_inactive_is_a_benign_state_and_still_runs(db_client, monkeypatch):
    """`inactive` (never logged in / dormant) is deliberately NOT in
    NON_TRANSACTING_USER_STATUSES — it must not pause jobs."""
    from narranexus.platform.agent_framework.providers.resolver import ProviderAvailability

    await _seed_user(db_client, "u_inactive", status="inactive")
    await _insert_job(db_client, "job_inactive", "u_inactive", status="active")

    async def _fake(uid, db):
        return ProviderAvailability.USER_OK

    monkeypatch.setattr(
        "narranexus.platform.agent_framework.providers.resolver.classify_provider_for_user",
        _fake,
    )
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._user_can_run("u_inactive") is True
    await trigger._poll_and_enqueue()
    assert trigger._job_queue.qsize() == 1


# ── review M1: one account lookup per principal per poll cycle ───────────────

@pytest.mark.asyncio
async def test_poll_and_enqueue_looks_each_principal_up_once(db_client, monkeypatch):
    await _seed_user(db_client, "u_many_banned", status="banned")
    await _seed_user(db_client, "u_many_ok", status="active")
    for i in range(3):
        await _insert_job(db_client, f"job_banned_{i}", "u_many_banned")
        await _insert_job(db_client, f"job_ok_{i}", "u_many_ok")

    calls = []
    real = JobTrigger._non_transacting_status

    async def _spy(self, user_id):
        calls.append(user_id)
        return await real(self, user_id)

    monkeypatch.setattr(JobTrigger, "_non_transacting_status", _spy)
    trigger = JobTrigger(database_client=db_client)
    await trigger._poll_and_enqueue()

    assert sorted(calls) == ["u_many_banned", "u_many_ok"]
    assert trigger._job_queue.qsize() == 3
    for i in range(3):
        row = await JobRepository(db_client).get_job(f"job_banned_{i}")
        assert (row.status, row.paused_reason) == (JobStatus.PAUSED, "banned")
