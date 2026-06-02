"""
@file_name: test_edge_rearm.py
@author: Bin Liang
@date: 2026-06-01
@description: Batch ② — edge-triggered recovery of PAUSED_NO_QUOTA jobs.

A PAUSED_NO_QUOTA job's blocker (no usable provider) only clears on a user/admin
action — top up quota, configure an own provider, disable the free-tier toggle,
log in. So recovery is EVENT-driven, not time-driven: polling for it is wasted
(and high-frequency polling was the oscillation source). `rearm_user_no_quota_jobs`
is called from those mutation points; it validates readiness (live, via
ProviderReadiness) and flips the user's paused jobs back to ACTIVE only if ready.
"""
import pytest

from xyz_agent_context.repository import JobRepository
from xyz_agent_context.schema.job_schema import JobStatus
from xyz_agent_context.module.job_module.job_recovery import rearm_user_no_quota_jobs

SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'
_READINESS = "xyz_agent_context.module.job_module.job_recovery.ProviderReadiness"


async def _insert_paused(db, job_id, user_id="user_1"):
    import datetime
    now = datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    await db.insert("instance_jobs", {
        "job_id": job_id, "instance_id": f"ins_{job_id}",
        "agent_id": "agent_1", "user_id": user_id,
        "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled", "trigger_config": SCHEDULED_TRIGGER,
        "status": JobStatus.PAUSED_NO_QUOTA.value, "notification_method": "inbox",
        "paused_reason": "no_quota",
        "created_at": now, "updated_at": now,
    })


class _FakeReadiness:
    def __init__(self, ok):
        self._ok = ok

    async def validate(self, user_id, db):
        return (self._ok, "system_ok" if self._ok else "free_tier_exhausted")


@pytest.mark.asyncio
async def test_rearm_flips_paused_to_active_when_ready(db_client, monkeypatch):
    await _insert_paused(db_client, "job_e1", user_id="user_1")
    monkeypatch.setattr(_READINESS, _FakeReadiness(ok=True))

    rearmed = await rearm_user_no_quota_jobs("user_1", db_client)

    assert rearmed == 1
    row = await db_client.get_one("instance_jobs", {"job_id": "job_e1"})
    assert row["status"] == JobStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_rearm_leaves_paused_when_not_ready(db_client, monkeypatch):
    await _insert_paused(db_client, "job_e2", user_id="user_1")
    monkeypatch.setattr(_READINESS, _FakeReadiness(ok=False))

    rearmed = await rearm_user_no_quota_jobs("user_1", db_client)

    assert rearmed == 0
    row = await db_client.get_one("instance_jobs", {"job_id": "job_e2"})
    assert row["status"] == JobStatus.PAUSED_NO_QUOTA.value


@pytest.mark.asyncio
async def test_rearm_only_touches_target_user(db_client, monkeypatch):
    await _insert_paused(db_client, "job_e3", user_id="user_1")
    await _insert_paused(db_client, "job_e4", user_id="user_2")
    monkeypatch.setattr(_READINESS, _FakeReadiness(ok=True))

    rearmed = await rearm_user_no_quota_jobs("user_1", db_client)

    assert rearmed == 1
    assert (await db_client.get_one("instance_jobs", {"job_id": "job_e4"}))["status"] \
        == JobStatus.PAUSED_NO_QUOTA.value  # other user's job untouched
