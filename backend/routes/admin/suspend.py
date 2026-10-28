"""
@file_name: suspend.py
@author: Bin Liang
@date: 2026-08-13
@description: Admin-only account-suspension mechanism.

A generic, reusable switch over a user's account state, driven by a private
caller. This module holds NO policy: it does not decide who should be
suspended or why. It exposes three self-credentialed operations —

    POST /api/admin/suspend           set account state to suspended
    POST /api/admin/reinstate         return account state to active (and resume
                                      the jobs the suspension paused)
    GET  /api/admin/account-state/{user_id}   read current account state

— each gated on the platform ``admin_secret_key`` via an ``X-Admin-Secret``
header (never a user JWT, never open), exactly like migrate-identity. The
``reason`` and ``evidence_ref`` fields are OPAQUE free text recorded verbatim
in the ``ban_audit`` trail; this layer never interprets or categorises them.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

# Re-exported so the admin secret can be overridden per-test via
# ``mod.settings`` (this module's namespace); the shared ``require_admin_secret``
# helper reads the same ``settings`` singleton object.
from narranexus.platform.settings import settings  # noqa: F401
from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils.plugin_services import try_job_resume_for_principal
from narranexus.platform.utils.timezone import utc_now
from narranexus.platform.repository.user_repository import UserRepository
from narranexus.platform.repository.job_repository import JobRepository
from narranexus.platform.repository.ban_audit_repository import (
    ACTION_REINSTATE,
    ACTION_SUSPEND,
    BanAuditRepository,
)
from narranexus.platform.schema import NON_TRANSACTING_USER_STATUSES, UserStatus

from ._admin_secret import admin_secret_dependency

router = APIRouter(prefix="/api/admin", tags=["admin"])

# The account states that count as "suspended" for the idempotency check: the
# shared non-transacting set (single source of truth with the auth middleware /
# WS gate / login gate). A suspend applied to an already non-transacting account
# is a no-op success.
_SUSPENDED_STATES = NON_TRANSACTING_USER_STATUSES

# paused_reason written on the jobs of a suspended account. The same literal
# JobTrigger records when it catches a non-transacting principal itself
# (it writes the account's actual status; this route only ever sets BANNED).
_PAUSED_REASON_BANNED = UserStatus.BANNED.value


async def _pause_jobs_for_suspended_principal(db, user_id: str) -> tuple[int, Optional[str]]:
    """Best-effort: pause every schedulable job (PENDING / ACTIVE / COOLING,
    `SUSPENDABLE_JOB_STATUSES`) that would EXECUTE as `user_id` (B-13).
    Returns `(paused_count, error)`; never raises. BLOCKED / BLOCKED_FAILED /
    RUNNING and the auto-paused states are left as they are (review I1):
    reinstate restores what this pauses to ACTIVE, which would start a
    dependency-blocked job before its upstream finished.

    Runs in the same request as the `users.status` flip so a suspended
    account's scheduled jobs stop being enqueued immediately, instead of
    waiting for the job poller's own (separate) account check on its next
    cycle. It is deliberately the LAST step and wrapped (review I4): the
    account flip is the source of truth, the audit row and the cache
    invalidation must not be lost to a job-table hiccup, and the poller's
    per-job gate (`JobTrigger._non_transacting_status`) is the durable
    backstop for anything this misses. The count / error are surfaced in
    the response so a partial pause is never silent.

    One batched UPDATE selected by execution principal
    (`JobRepository.pause_jobs_for_execution_principal`, review I2/I3) —
    the identity the run would use, which is what the poller judges too.
    `paused_reason="banned"` marks these as never auto-resumable by the
    poller; `POST /api/admin/reinstate` is the way back and resumes exactly
    this population (`_resume_jobs_for_reinstated_principal`, review r2 I-B).

    Only called when THIS request moved the account into a suspended state.
    Re-suspending an account that is already non-transacting pauses nothing
    here; any of its jobs still live are caught by the poller's per-job gate
    on their next due cycle.
    """
    try:
        paused = await JobRepository(db).pause_jobs_for_execution_principal(
            user_id, _PAUSED_REASON_BANNED, paused_at=utc_now()
        )
        return paused, None
    except Exception as e:  # noqa: BLE001 — the poller gate is the durable backstop
        err = f"{type(e).__name__}: {e}"
        logger.warning(f"[suspend] job pause failed for {user_id}: {err}")
        return 0, err


async def _resume_jobs_for_reinstated_principal(db, user_id: str) -> tuple[int, Optional[str]]:
    """Best-effort: resume the jobs an account suspension paused (review r2
    I-B). Returns `(resumed_count, error)`; never raises.

    The exact inverse of `_pause_jobs_for_suspended_principal`: jobs that
    execute as `user_id`, in `status='paused'`, whose `paused_reason` is one
    of the non-transacting account states — the only reasons a suspension
    writes (this route writes "banned"; the poller's own account gate writes
    the account's actual status). A job the user paused themselves, or one
    paused for quota / spend / auth, is never touched.

    Resumed through builtin.job's job-layer resume (the
    ``jobs.resume_for_principal`` service), not a status flip: next_run is
    recomputed from now (the fires missed while suspended are not replayed)
    and a recurring job already past its end_at horizon is completed instead.
    Last step of the request and wrapped, for the same reason as the pause:
    the account flip, audit row and cache invalidation must never be lost to
    a job-table error; the count / error are returned in the response so a
    partial resume is visible. Jobs left paused here can still be resumed
    one by one from the Jobs panel.
    """
    resume = try_job_resume_for_principal()
    if resume is None:
        err = "builtin.job is not loaded; suspension-paused jobs left paused"
        logger.warning(f"[reinstate] job resume skipped for {user_id}: {err}")
        return 0, err
    try:
        resumed = await resume(db, user_id, NON_TRANSACTING_USER_STATUSES)
        return resumed, None
    except Exception as e:  # noqa: BLE001 — the account is reinstated either way
        err = f"{type(e).__name__}: {e}"
        logger.warning(f"[reinstate] job resume failed for {user_id}: {err}")
        return 0, err


class SuspendRequest(BaseModel):
    user_id: str
    # reason / evidence_ref are OPAQUE free text recorded verbatim; bounded only
    # to keep a stray unbounded payload out of the audit row (the backing column
    # is MEDIUMTEXT — the bound is a request-side guardrail, not a storage cap).
    reason: Optional[str] = Field(default=None, max_length=4096)
    evidence_ref: Optional[str] = Field(default=None, max_length=4096)
    actor: Optional[str] = None


class SuspendResponse(BaseModel):
    suspended: bool
    already: bool
    # B-13 / review I4: how many of the account's jobs this call paused, and
    # why the pause step failed if it did (the account is suspended either
    # way; the job poller re-checks account state on its own).
    jobs_paused: int = 0
    jobs_pause_error: Optional[str] = None


class ReinstateRequest(BaseModel):
    user_id: str
    actor: Optional[str] = None


class ReinstateResponse(BaseModel):
    reinstated: bool
    # review r2 I-B: how many suspension-paused jobs this call resumed, and
    # why the resume step failed if it did (the account is reinstated either
    # way).
    jobs_resumed: int = 0
    jobs_resume_error: Optional[str] = None


class AccountStateResponse(BaseModel):
    user_id: str
    status: str


def _invalidate_cache(user_id: str) -> None:
    """Best-effort drop of the middleware's cached account state.

    Imported lazily so this route module never pulls the auth middleware in at
    import time (and so tests can exercise the route without the full app).
    """
    try:
        from backend.auth import invalidate_account_state

        invalidate_account_state(user_id)
    except Exception as e:  # noqa: BLE001 — cache invalidation is best-effort
        logger.warning(
            f"[suspend] cache invalidation skipped for {user_id}: "
            f"{type(e).__name__}: {e}"
        )


@router.post("/suspend", response_model=SuspendResponse)
async def suspend_account(
    request: SuspendRequest,
    _: None = Depends(admin_secret_dependency),
) -> SuspendResponse:
    db = await get_db_client()
    user_repo = UserRepository(db)
    user = await user_repo.get_user(request.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    prev_status = user.status.value
    already = prev_status in _SUSPENDED_STATES
    if not already:
        await user_repo.update_user(
            request.user_id, {"status": UserStatus.BANNED}
        )

    # Audit every call (including the idempotent no-op) so the trail records
    # who asked and when, even when the state did not change. prev_status is the
    # state the account was in before this call.
    await BanAuditRepository(db).record(
        request.user_id,
        ACTION_SUSPEND,
        reason=request.reason,
        evidence_ref=request.evidence_ref,
        actor=request.actor,
        prev_status=prev_status,
    )
    _invalidate_cache(request.user_id)

    # Last, and best-effort (see the helper): audit + cache are already safe.
    jobs_paused, jobs_pause_error = (0, None)
    if not already:
        jobs_paused, jobs_pause_error = await _pause_jobs_for_suspended_principal(
            db, request.user_id
        )

    logger.info(
        f"[suspend] user={request.user_id} already={already} "
        f"jobs_paused={jobs_paused} actor={request.actor or '-'}"
        + (f" jobs_pause_error={jobs_pause_error}" if jobs_pause_error else "")
    )
    return SuspendResponse(
        suspended=True,
        already=already,
        jobs_paused=jobs_paused,
        jobs_pause_error=jobs_pause_error,
    )


@router.post("/reinstate", response_model=ReinstateResponse)
async def reinstate_account(
    request: ReinstateRequest,
    _: None = Depends(admin_secret_dependency),
) -> ReinstateResponse:
    db = await get_db_client()
    user_repo = UserRepository(db)
    user = await user_repo.get_user(request.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    prev_status = user.status.value

    # Only reinstate an account THIS mechanism suspended, i.e. one whose state
    # is exactly BANNED. `blocked` / `deleted` are pre-existing terminal states
    # this switch never set and must not silently revive — reinstating one would
    # be a cross-mechanism side effect. Record the attempt in the audit trail
    # either way (prev_status shows what it was), then refuse.
    if prev_status != UserStatus.BANNED.value:
        await BanAuditRepository(db).record(
            request.user_id,
            ACTION_REINSTATE,
            actor=request.actor,
            prev_status=prev_status,
        )
        logger.info(
            f"[reinstate] refused (not banned) user={request.user_id} "
            f"status={prev_status} actor={request.actor or '-'}"
        )
        raise HTTPException(
            status_code=409,
            detail={
                "reinstated": False,
                "not_suspended_by_this_mechanism": True,
                "status": prev_status,
            },
        )

    await user_repo.update_user(request.user_id, {"status": UserStatus.ACTIVE})
    await BanAuditRepository(db).record(
        request.user_id,
        ACTION_REINSTATE,
        actor=request.actor,
        prev_status=prev_status,
    )
    _invalidate_cache(request.user_id)

    # Last, and best-effort (see the helper): account, audit and cache are safe.
    jobs_resumed, jobs_resume_error = await _resume_jobs_for_reinstated_principal(
        db, request.user_id
    )

    logger.info(
        f"[reinstate] user={request.user_id} jobs_resumed={jobs_resumed} "
        f"actor={request.actor or '-'}"
        + (f" jobs_resume_error={jobs_resume_error}" if jobs_resume_error else "")
    )
    return ReinstateResponse(
        reinstated=True,
        jobs_resumed=jobs_resumed,
        jobs_resume_error=jobs_resume_error,
    )


@router.get("/account-state/{user_id}", response_model=AccountStateResponse)
async def get_account_state(
    user_id: str,
    _: None = Depends(admin_secret_dependency),
) -> AccountStateResponse:
    db = await get_db_client()
    user = await UserRepository(db).get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    return AccountStateResponse(user_id=user_id, status=user.status.value)
