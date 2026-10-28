"""
@file_name: test_job_pause_inbox_notification.py
@author: Bin Liang
@date: 2026-09-09
@description: B-17 — a job paused for quota/auth exhaustion must notify the
owner via the existing inbox mechanism. Before this fix, `_finalize_job_execution`
only wrote a `logger.warning` on pause — the user had no in-product signal
that a scheduled job silently stopped running (only visible on the Jobs
panel if they happened to look).
"""
from __future__ import annotations

from datetime import datetime, timezone as dt_tz

import pytest

from datetime import timedelta

from narranexus.platform.agent_framework.llm.failure import (
    SELF_SERVICEABLE_REASON_CONTEXT_WINDOW,
    SELF_SERVICEABLE_REASON_FREE_TIER_EXHAUSTED,
    SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE,
    SELF_SERVICEABLE_REASON_INVALID_CREDENTIALS,
    SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND,
)
from narranexus.platform.message_bus.message_bus_trigger import NOTICE_COOLDOWN_RETENTION_DAYS
from narranexus.platform.repository import JobRepository
from narranexus.platform.repository.inbox_repository import InboxRepository
from narranexus.platform.repository.owner_notice_cooldown_repository import (
    OwnerNoticeCooldownRepository,
)
from narranexus.platform.schema.job_schema import JobStatus
from narranexus.platform.utils import utc_now
from narranexus_plugins.job_module.job_trigger import (
    _PAUSE_NOTICE_COOLDOWN_SECONDS,
    JobTrigger,
    _pause_notice_category,
)

SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'


async def _insert_job(db, job_id, user_id="user_1", status="active"):
    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=dt_tz.utc).isoformat().replace("+00:00", "Z")
    await db.insert("instance_jobs", {
        "job_id": job_id,
        "instance_id": f"ins_{job_id}",
        "agent_id": "agent_1",
        "user_id": user_id,
        "title": "Daily NetMind report", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": SCHEDULED_TRIGGER,
        "status": status,
        "notification_method": "inbox",
        "created_at": now,
        "updated_at": now,
    })


@pytest.mark.asyncio
async def test_quota_pause_writes_an_inbox_notice(db_client):
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_q_notify")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_q_notify")

    await trigger._finalize_job_execution(job, {
        "success": False,
        "error_type": "NoProviderConfiguredError",
        "error": "No provider configured.",
        "event_id": None,
    })

    row = await db_client.get_one("instance_jobs", {"job_id": "job_q_notify"})
    assert row["status"] == JobStatus.PAUSED_NO_QUOTA.value

    messages = await InboxRepository(db_client).get_messages("user_1")
    assert len(messages) == 1
    msg = messages[0]
    assert "Daily NetMind report" in msg.title
    assert "job_q_notify" in msg.source.id


@pytest.mark.asyncio
async def test_transient_cooling_does_not_notify(db_client):
    """A transient failure (COOLING, not paused) is not owner-actionable
    yet — no notification noise for something that self-heals."""
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_transient")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_transient")

    await trigger._finalize_job_execution(job, {
        "success": False,
        "error_type": "APITimeoutError",
        "error": "Read timed out",
        "event_id": None,
    })

    messages = await InboxRepository(db_client).get_messages("user_1")
    assert messages == []


@pytest.mark.asyncio
async def test_notification_failure_does_not_break_the_pause(db_client, monkeypatch):
    """Best-effort: an inbox write failure must not prevent the job from
    still being correctly paused."""
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_notify_fails")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_notify_fails")

    async def _boom(*args, **kwargs):
        raise RuntimeError("inbox db down")

    monkeypatch.setattr(InboxRepository, "create_message", _boom)

    await trigger._finalize_job_execution(job, {
        "success": False,
        "error_type": "NoProviderConfiguredError",
        "error": "No provider configured.",
        "event_id": None,
    })

    row = await db_client.get_one("instance_jobs", {"job_id": "job_notify_fails"})
    assert row["status"] == JobStatus.PAUSED_NO_QUOTA.value


# ── review I8: one notice per (job, reason) per window ───────────────────────

NO_PROVIDER = {
    "success": False,
    "error_type": "NoProviderConfiguredError",
    "error": "No provider configured.",
    "event_id": None,
}
AUTH_FAIL = {
    "success": False,
    "error_type": "AuthenticationError",
    "error": "Authentication failed: invalid api key",
    "event_id": None,
}


@pytest.mark.asyncio
async def test_repeated_pause_for_the_same_reason_notifies_once(db_client):
    """The 15-min backstop re-arms an auth/no_quota pause, the run fails
    again, the job pauses again — the owner must not get a fresh inbox row
    per schedule tick."""
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_repeat")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_repeat")

    await trigger._finalize_job_execution(job, NO_PROVIDER)
    await trigger._finalize_job_execution(job, NO_PROVIDER)
    await trigger._finalize_job_execution(job, NO_PROVIDER)

    messages = await InboxRepository(db_client).get_messages("user_1")
    assert len(messages) == 1
    row = await db_client.get_one("instance_jobs", {"job_id": "job_repeat"})
    assert row["status"] == JobStatus.PAUSED_NO_QUOTA.value


@pytest.mark.asyncio
async def test_a_different_pause_reason_notifies_again(db_client):
    """no_quota -> auth is a new fact for the owner; the window is per reason."""
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_reason_change")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_reason_change")

    await trigger._finalize_job_execution(job, NO_PROVIDER)
    await trigger._finalize_job_execution(job, AUTH_FAIL)

    messages = await InboxRepository(db_client).get_messages("user_1")
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_notifies_again_once_the_window_has_expired(db_client):
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_expired")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_expired")

    await trigger._finalize_job_execution(job, NO_PROVIDER)
    # Age the window past its length, as if the last notice was long ago.
    await OwnerNoticeCooldownRepository(db_client).arm(
        job.agent_id, job.job_id, _pause_notice_category("no_quota"),
        at=utc_now() - timedelta(seconds=_PAUSE_NOTICE_COOLDOWN_SECONDS + 60),
    )
    await trigger._finalize_job_execution(job, NO_PROVIDER)

    messages = await InboxRepository(db_client).get_messages("user_1")
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_window_is_not_armed_when_the_inbox_write_fails(db_client, monkeypatch):
    """A failed write must not open a window with nothing behind it — the
    next pause still gets its notice once the inbox is back."""
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_arm_after_write")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_arm_after_write")

    real_create = InboxRepository.create_message

    async def _boom(*args, **kwargs):
        raise RuntimeError("inbox db down")

    monkeypatch.setattr(InboxRepository, "create_message", _boom)
    await trigger._finalize_job_execution(job, NO_PROVIDER)
    monkeypatch.setattr(InboxRepository, "create_message", real_create)
    await trigger._finalize_job_execution(job, NO_PROVIDER)

    messages = await InboxRepository(db_client).get_messages("user_1")
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_cooldown_read_failure_still_notifies(db_client, monkeypatch):
    """Fail OPEN: an unreadable window must not silence a real pause."""
    repo = JobRepository(db_client)
    await _insert_job(db_client, "job_cooldown_down")
    trigger = JobTrigger(database_client=db_client)
    job = await repo.get_job("job_cooldown_down")

    async def _boom(*args, **kwargs):
        raise RuntimeError("cooldown table unreadable")

    monkeypatch.setattr(OwnerNoticeCooldownRepository, "is_cooling", _boom)
    await trigger._finalize_job_execution(job, NO_PROVIDER)

    messages = await InboxRepository(db_client).get_messages("user_1")
    assert len(messages) == 1


@pytest.mark.parametrize("reason", [
    "auth", "no_quota", "spend_cap",
    SELF_SERVICEABLE_REASON_CONTEXT_WINDOW,
    SELF_SERVICEABLE_REASON_FREE_TIER_EXHAUSTED,
    SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE,
    SELF_SERVICEABLE_REASON_INVALID_CREDENTIALS,
    SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND,
])
def test_every_pause_reason_fits_the_category_column(reason):
    """owner_notice_cooldowns.category is VARCHAR(32) on MySQL; an over-long
    category would be rejected (strict mode) and silently drop the dedup."""
    assert len(_pause_notice_category(reason)) <= 32


def test_pause_notice_window_stays_well_inside_the_cooldown_retention():
    """MessageBusTrigger sweeps owner_notice_cooldowns rows older than
    NOTICE_COOLDOWN_RETENTION_DAYS daily; a window approaching that length
    would be re-opened mid-flight by the sweep."""
    assert _PAUSE_NOTICE_COOLDOWN_SECONDS * 2 <= NOTICE_COOLDOWN_RETENTION_DAYS * 86400
