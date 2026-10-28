"""
@file_name: test_dashboard_live_job_states.py
@author: Bin Liang
@date: 2026-09-10
@description: Every non-terminal JobStatus must be visible to the dashboard.

`_LIVE_JOB_STATES` is the WHERE filter behind `fetch_jobs`; a status missing
from it is silently dropped from queue counts and the pending list —
`paused_no_quota` fell through that gap in 2026-06 and `paused_spend_cap`
did it again in review C1 of the 2026-09 job batch. This pins the tuple to
"all JobStatus values except the two silent terminals", and pins the two
hand-maintained schema surfaces (PendingJob.queue_status / QueueCounts) to it,
so the next status added to JobStatus fails here instead of vanishing.
"""
from __future__ import annotations

import typing

import pytest

from backend.routes.dashboard._helpers import _LIVE_JOB_STATES, _QUEUED_JOB_STATES, fetch_jobs
from backend.routes.dashboard._schema import PendingJob, QueueCounts
from narranexus.platform.schema.job_schema import JobStatus

SILENT_TERMINALS = {JobStatus.COMPLETED.value, JobStatus.CANCELLED.value}


def test_live_states_cover_every_non_terminal_job_status():
    assert set(_LIVE_JOB_STATES) == {s.value for s in JobStatus} - SILENT_TERMINALS
    assert len(set(_LIVE_JOB_STATES)) == len(_LIVE_JOB_STATES)


def test_queued_states_are_the_live_states_minus_running():
    assert set(_QUEUED_JOB_STATES) == set(_LIVE_JOB_STATES) - {"running"}
    # pending_jobs emission order predates the shared tuple (review r2 M-a).
    assert _QUEUED_JOB_STATES[:2] == ("pending", "active")


def test_queue_counts_schema_has_a_field_per_live_state():
    assert set(QueueCounts.model_fields) - {"total"} == set(_LIVE_JOB_STATES)


def test_pending_job_queue_status_literal_matches_queued_states():
    literal = PendingJob.model_fields["queue_status"].annotation
    assert set(typing.get_args(literal)) == set(_QUEUED_JOB_STATES)


@pytest.mark.asyncio
async def test_fetch_jobs_surfaces_a_spend_capped_job(db_client, monkeypatch):
    async def _db():
        return db_client

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", _db)
    await db_client.insert("instance_jobs", {
        "job_id": "job_capped", "instance_id": "ins_capped", "agent_id": "agent_1",
        "user_id": "u1", "title": "t", "description": "d", "payload": "p",
        "job_type": "scheduled",
        "trigger_config": '{"cron":"0 8 * * *","timezone":"UTC"}',
        "status": JobStatus.PAUSED_SPEND_CAP.value, "notification_method": "inbox",
    })

    per_agent = await fetch_jobs(["agent_1"])

    ids = [j["job_id"] for j in per_agent["agent_1"]["paused_spend_cap"]]
    assert ids == ["job_capped"]


# ── review M2: banners / health see every live state ─────────────────────────

def test_every_live_state_has_exactly_one_attention_group():
    from backend.routes.dashboard import _helpers as h

    groups = (
        h._FAILED_JOB_STATES, h._BLOCKED_JOB_STATES,
        h._PAUSED_JOB_STATES, h._NOMINAL_JOB_STATES,
    )
    flat = [s for g in groups for s in g]
    assert sorted(flat) == sorted(_LIVE_JOB_STATES)


@pytest.mark.parametrize("state", ["paused", "paused_no_quota", "paused_spend_cap"])
def test_paused_family_raises_the_paused_banner_and_rail(state):
    from backend.routes.dashboard._helpers import derive_attention_banners, derive_health

    queue = {state: 2}
    assert [b["kind"] for b in derive_attention_banners(queue)] == ["jobs_paused"]
    assert derive_attention_banners(queue)[0]["message"] == "2 jobs paused"
    assert derive_health("idle", queue, None, 0) == "paused"


@pytest.mark.parametrize("state", ["blocked", "blocked_failed"])
def test_blocked_family_raises_the_blocked_banner_and_warning_rail(state):
    from backend.routes.dashboard._helpers import derive_attention_banners, derive_health

    queue = {state: 1}
    assert [b["kind"] for b in derive_attention_banners(queue)] == ["job_blocked"]
    assert derive_health("idle", queue, None, 0) == "warning"


def test_nominal_states_raise_nothing():
    from backend.routes.dashboard._helpers import derive_attention_banners, derive_health

    queue = {"running": 1, "pending": 1, "active": 1, "cooling": 1}
    assert derive_attention_banners(queue) == []
    assert derive_health("idle", queue, None, 0) == "healthy_idle"
