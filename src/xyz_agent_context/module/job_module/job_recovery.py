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
from pydantic import ValidationError

from xyz_agent_context.repository import JobRepository
from xyz_agent_context.schema.job_schema import JobStatus, JobType, TriggerConfig
from xyz_agent_context.agent_framework.providers.readiness import ProviderReadiness
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


# A reschedule may touch any editable job EXCEPT one that is mid-execution
# (running) or already terminal (completed/cancelled/failed). Everything else —
# active / pending / paused / paused_no_quota / cooling / blocked_failed — is
# a legitimate edit target; the status is left untouched (a paused job stays
# paused, and its later resume re-derives next_run from the new rule anyway).
_NON_EDITABLE_STATUSES = (
    JobStatus.RUNNING,
    JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED,
)
# Only the time-bearing trigger fields are user-editable via reschedule; the
# ONGOING semantics fields (end_condition / max_iterations) are out of scope.
_TIME_FIELDS = ("run_at", "cron", "interval_seconds", "timezone")


async def reschedule_job(job_id: str, new_fields: dict, db) -> tuple[bool, str]:
    """User-initiated reschedule ("edit execution time").

    Merges the provided time fields into the job's existing trigger_config,
    revalidates through TriggerConfig (naive run_at / IANA timezone / tz-required
    / interval clamp), recomputes next_run from now, and persists the new
    trigger_config followed by next_run (two writes: update_job_fields, then the
    mandatory update_next_run for the alpha+beta pair — NOT a single transaction).
    The job's status is intentionally left unchanged.

    Portable (repository only; no backend-specific SQL). Callers keep auth.

    Args:
        job_id: target job.
        new_fields: subset of {run_at, cron, interval_seconds, timezone}. A key
            present with value None clears that field (defense-in-depth; the
            route strips None so the UI never nulls a field).
        db: AsyncDatabaseClient.

    Returns:
        (ok, detail). detail is "ok" on success, else a human-readable reason.
    """
    repo = JobRepository(db)
    job = await repo.get_job(job_id)
    if not job:
        return False, "job not found"
    if job.status in _NON_EDITABLE_STATUSES:
        return False, f"cannot reschedule from status={job.status.value}"

    merged = job.trigger_config.model_dump()
    for k in _TIME_FIELDS:
        if k in new_fields:
            merged[k] = new_fields[k]

    # cron and interval_seconds are mutually exclusive triggering modes for a
    # scheduled/ongoing job. Switching between them (e.g. interval → cron) must
    # clear the sibling, or the stale field lingers in trigger_config and future
    # edits/reads get ambiguous input. compute_next_run prefers cron, so a
    # lingering interval would also be silently shadowed. Whichever mode the
    # caller just set wins; the other is cleared.
    if new_fields.get("cron"):
        merged["interval_seconds"] = None
    elif new_fields.get("interval_seconds"):
        merged["cron"] = None

    try:
        new_cfg = TriggerConfig(**merged)
    except ValidationError as e:
        return False, f"invalid schedule: {e}"

    # Guard: the job must still carry a fireable time field for its type, or it
    # would silently never fire again.
    if job.job_type == JobType.ONE_OFF and new_cfg.run_at is None:
        return False, "one_off job requires run_at"
    if job.job_type in (JobType.SCHEDULED, JobType.ONGOING) \
            and not new_cfg.cron and not new_cfg.interval_seconds:
        return False, "scheduled job requires cron or interval_seconds"

    next_run = compute_next_run(
        job_type=job.job_type,
        trigger_config=new_cfg,
        last_run_utc=utc_now(),
    )
    await repo.update_job_fields(job_id, {"trigger_config": new_cfg})
    if next_run:
        await repo.update_next_run(job_id, next_run)
    return True, "ok"


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
