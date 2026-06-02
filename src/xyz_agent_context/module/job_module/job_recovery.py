"""
@file_name: job_recovery.py
@author: Bin Liang
@date: 2026-06-01
@description: Edge-triggered recovery of a single user's PAUSED_NO_QUOTA jobs.

PAUSED_NO_QUOTA is EVENT-recovered, not time-recovered: the blocker (no usable
provider) only clears when the user/admin acts — tops up quota, configures an
own provider, disables the free-tier toggle, or logs in. So instead of scanning
every poll cycle (the oscillation source), the backend routes that perform those
mutations call `rearm_user_no_quota_jobs(user_id, db)` after committing. It runs
a live readiness check and flips that user's paused jobs back to ACTIVE only if
ready. Cross-process safe: it writes job.status (the single authority) directly,
and the jobs poller picks the re-armed jobs up on its next cycle.

This is the PRIMARY recovery path; JobTrigger keeps a low-frequency scan as a
backstop for missed edges.
"""
from __future__ import annotations

import asyncio

from loguru import logger

from xyz_agent_context.repository import JobRepository
from xyz_agent_context.schema.job_schema import JobStatus
from xyz_agent_context.agent_framework.provider_readiness import ProviderReadiness
from xyz_agent_context.module.job_module._job_scheduling import compute_next_run
from xyz_agent_context.utils import utc_now


async def rearm_user_no_quota_jobs(user_id: str, db) -> int:
    """Re-arm `user_id`'s PAUSED_NO_QUOTA jobs to ACTIVE iff the user is now
    provider-ready (live check). Returns the count re-armed. Best-effort: never
    raises into the calling route.

    `user_id` matches both the job owner (`user_id`) and the execution principal
    (`related_entity_id`), since a quota/provider change for a user should revive
    jobs that run *as* that user.
    """
    try:
        repo = JobRepository(db)
        paused = await repo.get_jobs_by_status(JobStatus.PAUSED_NO_QUOTA)
        mine = [
            j for j in paused
            if (j.related_entity_id or j.user_id) == user_id
        ]
        if not mine:
            return 0

        ready, reason = await ProviderReadiness.validate(user_id, db)
        if not ready:
            logger.debug(
                f"rearm_user_no_quota_jobs: {user_id} still not ready ({reason}), "
                f"leaving {len(mine)} job(s) paused"
            )
            return 0

        rearmed = 0
        for job in mine:
            next_run = compute_next_run(
                job_type=job.job_type,
                trigger_config=job.trigger_config,
                last_run_utc=utc_now(),
            )
            if next_run:
                await repo.update_next_run(job.job_id, next_run)
            await repo.update_job(job.job_id, {
                "status": JobStatus.ACTIVE.value,
                "paused_reason": None,
            })
            rearmed += 1
        logger.info(
            f"Edge re-arm: {rearmed} PAUSED_NO_QUOTA job(s) for {user_id} "
            f"→ ACTIVE (ready: {reason})"
        )
        return rearmed
    except Exception as e:  # noqa: BLE001 — recovery must never break the caller
        logger.warning(f"rearm_user_no_quota_jobs failed for {user_id}: {e}")
        return 0


_RESUMABLE_STATUSES = (
    JobStatus.PAUSED, JobStatus.PAUSED_NO_QUOTA,
    JobStatus.COOLING, JobStatus.BLOCKED_FAILED,
)


async def pause_job(job_id: str, db) -> tuple[bool, str]:
    """User-initiated pause. Returns (ok, detail). A `paused` job stays put —
    excluded from the due-poll AND the auto-resume/cooling re-arm scans. Terminal
    jobs (completed/cancelled/failed) can't be paused. Portable (repository) — no
    backend-specific SQL."""
    repo = JobRepository(db)
    job = await repo.get_job(job_id)
    if not job:
        return False, "job not found"
    if job.status in (JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED):
        return False, f"cannot pause from status={job.status.value}"
    await repo.update_job(job_id, {
        "status": JobStatus.PAUSED.value,
        "paused_reason": "user",
        "paused_at": utc_now(),
    })
    return True, job.status.value


async def resume_job(job_id: str, db) -> tuple[bool, str]:
    """Resume a paused / no-quota / cooling / dependency-blocked-failed job:
    recompute next_run from now, clear backoff/pause state, flip to ACTIVE. If
    the underlying blocker is still unresolved the next run simply re-pauses."""
    repo = JobRepository(db)
    job = await repo.get_job(job_id)
    if not job:
        return False, "job not found"
    if job.status not in _RESUMABLE_STATUSES:
        return False, f"cannot resume from status={job.status.value}"
    next_run = compute_next_run(
        job_type=job.job_type,
        trigger_config=job.trigger_config,
        last_run_utc=utc_now(),
    )
    if next_run:
        await repo.update_next_run(job_id, next_run)
    await repo.update_job(job_id, {
        "status": JobStatus.ACTIVE.value,
        "paused_reason": None,
        "cooldown_until": None,
        "consecutive_failure_count": 0,
    })
    return True, job.status.value


# Keep references to in-flight background tasks so they aren't garbage-collected
# mid-run (incident lesson #2: an un-referenced create_task can be reclaimed).
_bg_rearm_tasks: set = set()


def schedule_user_no_quota_rearm(user_id: str) -> None:
    """Fire-and-forget edge re-arm — called from backend mutation routes
    (login / quota grant / preference toggle / provider save). Non-blocking so
    it never adds latency to the user's request (e.g. login returns immediately;
    the re-arm runs in the background and the jobs poller picks the revived jobs
    up next cycle). Uses the global db client, not a request-scoped one, since
    the task outlives the request.
    """
    if not user_id:
        return

    async def _run():
        try:
            from xyz_agent_context.utils import get_db_client
            db = await get_db_client()
            await rearm_user_no_quota_jobs(user_id, db)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"schedule_user_no_quota_rearm failed for {user_id}: {e}")

    try:
        task = asyncio.create_task(_run())
        _bg_rearm_tasks.add(task)
        task.add_done_callback(_bg_rearm_tasks.discard)
    except RuntimeError:
        # No running loop (e.g. called from sync context) — skip; the poller
        # backstop will still recover the jobs eventually.
        logger.debug(f"schedule_user_no_quota_rearm: no running loop for {user_id}")
