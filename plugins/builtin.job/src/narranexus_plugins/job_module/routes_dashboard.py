"""
@file_name: routes_dashboard.py
@author: Bin Liang
@date: 2026-09-04
@description: Dashboard job controls (pause / resume / reschedule) — builtin.job's router under /api/dashboard.

Split out of dashboard/routes.py in batch 3c.5: these three endpoints delegate
to the job module's portable core (job_recovery.pause_job / resume_job /
reschedule_job), so they belong to builtin.job and are mounted through
backend.routes; the dashboard's read endpoints and the SQL-only retry stay in
routes.py. Auth/ownership uses the same helpers as the rest of the dashboard.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from narranexus.sdk.web import agent_visible, resolve_viewer
from narranexus.contracts.route import RouterSpec
from narranexus.kernel.plugins.registry import Contribution

router = APIRouter()


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str, request: Request):
    """v2.1: pause an active/pending job."""
    viewer_id = await resolve_viewer(request)
    from narranexus.platform.utils.db.db_factory import get_db_client
    db = await get_db_client()
    rows = await db.execute(
        "SELECT agent_id, status FROM instance_jobs WHERE job_id=%s LIMIT 1",
        (job_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="job not found")
    agent = await agent_visible(viewer_id, rows[0]["agent_id"])
    if agent["created_by"] != viewer_id:
        raise HTTPException(status_code=403, detail="not owned")
    # Portable core (repository, not backend-specific SQL) — also keeps pause
    # semantics consistent with the JobTrigger state machine.
    from narranexus_plugins.job_module.job_recovery import pause_job as _pause
    ok, detail = await _pause(job_id, db)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"success": True, "job_id": job_id, "new_status": "paused"}


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str, request: Request):
    """v2.1: resume a paused job (back to pending so trigger can take it)."""
    viewer_id = await resolve_viewer(request)
    from narranexus.platform.utils.db.db_factory import get_db_client
    db = await get_db_client()
    rows = await db.execute(
        "SELECT agent_id, status FROM instance_jobs WHERE job_id=%s LIMIT 1",
        (job_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="job not found")
    agent = await agent_visible(viewer_id, rows[0]["agent_id"])
    if agent["created_by"] != viewer_id:
        raise HTTPException(status_code=403, detail="not owned")
    # Portable core: handles paused / paused_no_quota / cooling / blocked_failed,
    # recomputes next_run, clears backoff state, flips to ACTIVE.
    from narranexus_plugins.job_module.job_recovery import resume_job as _resume
    ok, detail = await _resume(job_id, db)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"success": True, "job_id": job_id, "new_status": "active"}


class RescheduleBody(BaseModel):
    """Edit-execution-time payload. Only the fields the user changed are sent;
    absent (None) fields leave the existing trigger_config untouched."""
    run_at: Optional[str] = None          # naive ISO, e.g. "2026-08-01T09:00:00"
    cron: Optional[str] = None
    interval_seconds: Optional[int] = None
    timezone: Optional[str] = None


@router.put("/jobs/{job_id}/schedule")
async def reschedule_job(job_id: str, body: RescheduleBody, request: Request):
    """Edit a non-running, non-terminal job's execution time (trigger rule).

    Delegates to the portable core (job_recovery.reschedule_job): merge the new
    time fields into trigger_config, revalidate, recompute next_run. The job's
    status is left unchanged. Auth/ownership stays here, mirroring pause/resume.
    """
    viewer_id = await resolve_viewer(request)
    from narranexus.platform.utils.db.db_factory import get_db_client
    db = await get_db_client()
    rows = await db.execute(
        "SELECT agent_id, status FROM instance_jobs WHERE job_id=%s LIMIT 1",
        (job_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="job not found")
    agent = await agent_visible(viewer_id, rows[0]["agent_id"])
    if agent["created_by"] != viewer_id:
        raise HTTPException(status_code=403, detail="not owned")
    # exclude_none: only overlay the fields the user actually changed, so e.g.
    # editing just the cron keeps the existing timezone.
    new_fields = body.model_dump(exclude_none=True)
    from narranexus_plugins.job_module.job_recovery import reschedule_job as _reschedule
    ok, detail = await _reschedule(job_id, new_fields, db)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    updated = await db.execute(
        "SELECT next_run_at_local, next_run_tz FROM instance_jobs WHERE job_id=%s LIMIT 1",
        (job_id,),
    )
    return {
        "success": True,
        "job_id": job_id,
        "next_run_at": updated[0]["next_run_at_local"] if updated else None,
        "next_run_timezone": updated[0]["next_run_tz"] if updated else None,
    }


ROUTES = (Contribution("dashboard_jobs", lambda: RouterSpec(router, "/api/dashboard", tags=("Dashboard",))),)
