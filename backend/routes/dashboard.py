"""
@file_name: dashboard.py
@author: NarraNexus
@date: 2026-04-13
@description: Agent Dashboard v2 route.

GET /api/dashboard/agents-status — returns per-viewer aggregated view of all
visible agents (owned + public). Enforces permission boundary via Pydantic
discriminated union (owner-only fields cannot appear on public variant) and
2 req/s per-viewer sliding-window rate limit.

Cloud mode: viewer_id from request.state.user_id (JWT middleware populated).
Local mode: viewer_id from backend.auth.get_local_user_id() — NEVER from
`?user_id=` query param (that would be an impersonation vector; see TDR-12).

See design doc rev-2 for the full flow + threat model.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from backend.auth import _is_cloud_mode, get_local_user_id
from backend.routes._dashboard_helpers import (
    bucket_count,
    build_action_line,
    build_recent_events_resp,
    build_run_state_for_agent,
    classify_kind,
    derive_attention_banners,
    derive_health,
    fetch_enhanced_signals,
    fetch_instances,
    fetch_jobs,
    fetch_last_activity,
    fetch_metrics_today,
    fetch_recent_events,
    fetch_sparkline_24h,
    humanize_verb,
    sort_agents,
    to_response,
)
from backend.routes._dashboard_schema import DashboardResponse
from backend.routes._rate_limiter import SlidingWindowRateLimiter
from backend.state.active_sessions import get_session_registry

router = APIRouter()

# Per-viewer rate limit: dashboard legitimate rate is <1 req/s (3s polling);
# 2 req/s gives headroom for manual refresh while blocking 100-tab DoS.
_rate_limiter = SlidingWindowRateLimiter(limit=2, window_sec=1.0)


@router.get("/agents-status", response_model=DashboardResponse)
async def agents_status(request: Request, response: Response):
    # 1. Reject ?user_id= query param (TDR-12: impersonation vector)
    if "user_id" in request.query_params:
        raise HTTPException(
            status_code=400,
            detail="user_id query param not accepted; viewer identified by session",
        )

    # 2. Identify viewer
    if _is_cloud_mode():
        viewer_id = getattr(request.state, "user_id", None)
        if not viewer_id:
            raise HTTPException(status_code=401, detail="Authentication required")
    else:
        viewer_id = await get_local_user_id()

    # 3. Rate limit
    if not _rate_limiter.allow(viewer_id):
        raise HTTPException(
            status_code=429,
            detail="Too Many Requests",
            headers={"Retry-After": "1"},
        )

    # 4. Fetch visible agents (owned OR public)
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    agent_rows = await db.execute(
        "SELECT agent_id, agent_name, agent_description, created_by, is_public "
        "FROM agents WHERE created_by=%s OR is_public=1",
        (viewer_id,),
    )
    agent_ids = [r["agent_id"] for r in agent_rows]

    # 5. Parallel aggregation (fail-fast — no partial degradation)
    if agent_ids:
        last_act, jobs_map, inst_map, enh_map, recent_map, metrics_map = await asyncio.gather(
            fetch_last_activity(agent_ids),
            fetch_jobs(agent_ids),
            fetch_instances(agent_ids),
            fetch_enhanced_signals(agent_ids),
            fetch_recent_events(agent_ids, limit_per_agent=3),
            fetch_metrics_today(agent_ids),
            return_exceptions=False,
        )
        sessions_map = await get_session_registry().snapshot(agent_ids)
    else:
        last_act, jobs_map, inst_map, enh_map, recent_map, metrics_map, sessions_map = (
            {}, {}, {}, {}, {}, {}, {},
        )

    # 6. Build response per agent
    statuses: list[Any] = []
    for r in agent_rows:
        aid = r["agent_id"]
        sessions = sessions_map.get(aid, [])
        per_state = jobs_map.get(aid, {})
        running_jobs_raw = list(per_state.get("running", []))
        instances = inst_map.get(aid, [])

        running_count = len(sessions) + len(running_jobs_raw) + len(instances)
        kind = _derive_kind(sessions, running_jobs_raw, instances)
        started_at = (
            _earliest_started_at(sessions, running_jobs_raw)
            if kind != "idle"
            else None
        )

        run_state = await build_run_state_for_agent(
            agent_id=aid, kind=kind,
            sessions=sessions, running_jobs=running_jobs_raw, instances=instances,
        )
        action_line = build_action_line(run_state)

        # v2.1.1: queue counts (all 6 live states, no overlap — see fetch_jobs)
        queue_counts = {
            s: len(per_state.get(s, []))
            for s in ("running", "active", "pending", "blocked", "paused", "failed")
        }
        queue_counts["total"] = sum(queue_counts.values())

        # v2.1.1: pending_jobs surfaces all 5 non-running live states with
        # a queue_status tag. Each job appears EXACTLY ONCE (was a v2.1 bug).
        pending_jobs_items: list[dict] = []
        seen_job_ids: set[str] = set()
        for qstate in ("pending", "active", "blocked", "paused", "failed"):
            for j in per_state.get(qstate, []):
                if j["job_id"] in seen_job_ids:
                    continue  # belt-and-suspenders dedup
                seen_job_ids.add(j["job_id"])
                pending_jobs_items.append({
                    "job_id": j["job_id"],
                    "title": j["title"],
                    "job_type": j["job_type"],
                    "next_run_time": j.get("next_run_time"),
                    "description": j.get("description"),
                    "queue_status": qstate,
                })

        # v2.1: metrics_today + recent events
        metrics_today = metrics_map.get(aid) or {
            "runs_ok": 0, "errors": 0, "avg_duration_ms": None,
            "avg_duration_trend": "unknown", "token_cost_cents": None,
        }
        recent_events = build_recent_events_resp(recent_map.get(aid, []))

        # v2.1: attention banners + health
        banners = derive_attention_banners(queue_counts, has_slow_response=False)
        health = derive_health(
            kind=kind, queue=queue_counts, last_activity_at=last_act.get(aid),
            errors_today=metrics_today["errors"],
        )

        # v2.1: verb line (human-readable)
        verb_line = humanize_verb(
            kind=kind, sessions=sessions, running_jobs=running_jobs_raw,
            last_activity_at=last_act.get(aid),
        )

        raw = {
            "agent_id": aid,
            "name": r["agent_name"],
            "description": r.get("agent_description"),
            "created_by": r["created_by"],
            "is_public": bool(r["is_public"]),
            "status": {
                "kind": kind,
                "last_activity_at": _iso(last_act.get(aid)),
                "started_at": _iso(started_at),
            },
            "running_count": running_count,
            "action_line": action_line,
            "verb_line": verb_line,
            "sessions": [
                {
                    "session_id": s.session_id,
                    "user_display": s.user_display,
                    "channel": s.channel,
                    "started_at": s.started_at,
                }
                for s in sessions
            ],
            "running_jobs": [
                {
                    "job_id": j["job_id"],
                    "title": j["title"],
                    "job_type": j["job_type"],
                    "started_at": _iso(j.get("started_at")),
                    "description": j.get("description"),
                    "progress": None,  # no step data in current schema
                }
                for j in running_jobs_raw
            ],
            "pending_jobs": pending_jobs_items,
            "enhanced": enh_map.get(aid)
            or {
                "recent_errors_1h": 0,
                "token_rate_1h": None,
                "active_narratives": 0,
                "unread_bus_messages": 0,
            },
            "queue": queue_counts,
            "recent_events": recent_events,
            "metrics_today": metrics_today,
            "attention_banners": banners,
            "health": health,
        }
        resp = to_response(raw, viewer_id=viewer_id)
        if resp is not None:
            statuses.append(resp)

    statuses = sort_agents(statuses)
    return {"success": True, "agents": statuses}


def _derive_kind(sessions, running_jobs, instances) -> str:
    """Heuristic: prefer running_jobs > sessions > instances > idle.

    For sessions we distinguish MESSAGE_BUS (non-web channels) from CHAT (web).
    """
    if running_jobs:
        return "JOB"
    if sessions:
        ch = (sessions[0].channel or "").lower()
        if ch.startswith(("lark", "slack", "matrix", "message_bus", "bus")):
            return "MESSAGE_BUS"
        return "CHAT"
    if instances:
        return "CALLBACK"
    return "idle"


def _earliest_started_at(sessions, running_jobs) -> str | None:
    """TDR-11: started_at = min(active session/job start) across concurrent work."""
    candidates = [s.started_at for s in sessions if getattr(s, "started_at", None)]
    candidates.extend(j["started_at"] for j in running_jobs if j.get("started_at"))
    if not candidates:
        return None
    # Normalize possibly-datetime objects (from MySQL) to comparable ISO strings
    norm = [_iso(c) for c in candidates]
    norm = [x for x in norm if x is not None]
    if not norm:
        return None
    return min(norm)


def _iso(value) -> str | None:
    """Normalize datetime → ISO str; pass strings through; None stays None."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# v2.1: Lazy-loaded detail endpoints (called when user expands a card item).
# These are intentionally separate from the main polled endpoint so the
# 3s/30s fan-out doesn't bloat with rarely-needed deep data.
# ---------------------------------------------------------------------------

async def _resolve_viewer(request: Request) -> str:
    """Shared identity resolution mirroring the main endpoint."""
    if "user_id" in request.query_params:
        raise HTTPException(
            status_code=400,
            detail="user_id query param not accepted; viewer identified by session",
        )
    if _is_cloud_mode():
        viewer_id = getattr(request.state, "user_id", None)
        if not viewer_id:
            raise HTTPException(status_code=401, detail="Authentication required")
        return viewer_id
    return await get_local_user_id()


async def _assert_agent_visible(viewer_id: str, agent_id: str) -> dict:
    """Ensure viewer can see this agent (owned OR public). Returns agent row."""
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    rows = await db.execute(
        "SELECT agent_id, agent_name, created_by, is_public "
        "FROM agents WHERE agent_id=%s LIMIT 1",
        (agent_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="agent not found")
    row = rows[0]
    if row["created_by"] != viewer_id and not row.get("is_public"):
        raise HTTPException(status_code=404, detail="agent not found")  # don't leak existence
    return row


@router.get("/jobs/{job_id}")
async def job_detail(job_id: str, request: Request):
    """v2.1: expand-on-click detail for one job.

    Authorization: viewer must own (or public-see) the agent this job belongs to.
    Response shape varies by job.status — running jobs include progress &
    recent_history; blocked jobs include blocking_dependencies; failed jobs
    include the full error.
    """
    viewer_id = await _resolve_viewer(request)
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    rows = await db.execute(
        "SELECT job_id, agent_id, title, description, job_type, status, "
        "trigger_config, next_run_time, started_at, last_run_time, "
        "last_error, iteration_count, process, monitored_job_ids, "
        "created_at, updated_at "
        "FROM instance_jobs WHERE job_id=%s LIMIT 1",
        (job_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="job not found")
    job = rows[0]

    # Check caller can see the owning agent
    agent = await _assert_agent_visible(viewer_id, job["agent_id"])
    owns_agent = agent["created_by"] == viewer_id
    if not owns_agent:
        raise HTTPException(status_code=403, detail="not owned")  # public can't peek internals

    # Recent history: last 5 events that reference this job (heuristic via embedding_text match)
    history_rows = await db.execute(
        "SELECT event_id, created_at, final_output "
        "FROM events WHERE agent_id=%s "
        "ORDER BY created_at DESC LIMIT 5",
        (job["agent_id"],),
    )
    recent_history = [
        {
            "event_id": h["event_id"],
            "at": _iso(h["created_at"]),
            "status": "failed" if "ERROR" in (h.get("final_output") or "") else "success",
            "duration_ms": None,
        }
        for h in history_rows
    ]

    # Blocking dependencies (if status == blocked)
    blocking: list[dict] = []
    if job["status"] == "blocked":
        # Heuristic: look at monitored_job_ids in reverse (which jobs we depend on)
        # This repo's schema stores `monitored_job_ids` (downstream), not upstream,
        # so we provide a placeholder until a formal dep field lands.
        blocking = [{
            "job_id": "unknown",
            "title": "(upstream dependency)",
            "status": "active",
            "next_run_time": None,
            "reason": "Waiting for upstream dependencies",
        }]

    return {
        "success": True,
        "job": {
            "job_id": job["job_id"],
            "agent_id": job["agent_id"],
            "title": job["title"],
            "description": job.get("description"),
            "job_type": job["job_type"],
            "status": job["status"],
            "trigger_config": job.get("trigger_config"),
            "next_run_time": _iso(job.get("next_run_time")),
            "started_at": _iso(job.get("started_at")),
            "last_run_time": _iso(job.get("last_run_time")),
            "iteration_count": job.get("iteration_count") or 0,
            "last_error": job.get("last_error"),
            "recent_history": recent_history,
            "blocking_dependencies": blocking,
            "created_at": _iso(job.get("created_at")),
            "updated_at": _iso(job.get("updated_at")),
        },
    }


@router.get("/sessions/{session_id}")
async def session_detail(session_id: str, request: Request):
    """v2.1: expand-on-click detail for one active WS session.

    Returns enriched info: most recent bus message in the session's channel
    plus session metadata. Only owner of the agent can read details.
    """
    viewer_id = await _resolve_viewer(request)

    # Scan registry to find this session_id
    registry = get_session_registry()
    # We don't have a direct lookup by session_id, so snapshot then filter.
    # The caller must also pass ?agent_id= to keep this O(agent) not O(all).
    agent_id = request.query_params.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id query param required")
    snap = await registry.snapshot([agent_id])
    sessions = snap.get(agent_id, [])
    match = next((s for s in sessions if s.session_id == session_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="session not found")

    agent = await _assert_agent_visible(viewer_id, agent_id)
    if agent["created_by"] != viewer_id:
        raise HTTPException(status_code=403, detail="not owned")

    # Latest bus message for this channel (best-effort preview)
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    preview = None
    try:
        rows = await db.execute(
            "SELECT content, created_at FROM bus_messages WHERE channel_id=%s "
            "ORDER BY created_at DESC LIMIT 1",
            (match.channel,),
        )
        if rows:
            preview = {"content": rows[0]["content"], "at": _iso(rows[0]["created_at"])}
    except Exception:
        preview = None

    return {
        "success": True,
        "session": {
            "session_id": match.session_id,
            "agent_id": agent_id,
            "user_id": match.user_id,
            "user_display": match.user_display,
            "channel": match.channel,
            "started_at": match.started_at,
            "latest_message": preview,
        },
    }


@router.get("/agents/{agent_id}/sparkline")
async def agent_sparkline(agent_id: str, request: Request, hours: int = 24):
    """v2.1: 24h events-per-hour buckets for the sparkline micro-viz."""
    viewer_id = await _resolve_viewer(request)
    await _assert_agent_visible(viewer_id, agent_id)
    hours = max(1, min(168, int(hours)))  # clamp 1..168 (7 days)
    buckets = await fetch_sparkline_24h(agent_id, hours=hours)
    return {"success": True, "buckets": buckets, "hours": hours}


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str, request: Request):
    """v2.1: reset a failed job back to 'pending' so the trigger can pick it up."""
    viewer_id = await _resolve_viewer(request)
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    rows = await db.execute(
        "SELECT agent_id, status FROM instance_jobs WHERE job_id=%s LIMIT 1",
        (job_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="job not found")
    agent = await _assert_agent_visible(viewer_id, rows[0]["agent_id"])
    if agent["created_by"] != viewer_id:
        raise HTTPException(status_code=403, detail="not owned")
    if rows[0]["status"] not in ("failed", "blocked", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail=f"cannot retry from status={rows[0]['status']}",
        )
    await db.execute(
        "UPDATE instance_jobs SET status='pending', last_error=NULL, updated_at=datetime('now') "
        "WHERE job_id=%s",
        (job_id,),
        fetch=False,
    )
    return {"success": True, "job_id": job_id, "new_status": "pending"}


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str, request: Request):
    """v2.1: pause an active/pending job."""
    viewer_id = await _resolve_viewer(request)
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    rows = await db.execute(
        "SELECT agent_id, status FROM instance_jobs WHERE job_id=%s LIMIT 1",
        (job_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="job not found")
    agent = await _assert_agent_visible(viewer_id, rows[0]["agent_id"])
    if agent["created_by"] != viewer_id:
        raise HTTPException(status_code=403, detail="not owned")
    if rows[0]["status"] not in ("active", "pending"):
        raise HTTPException(
            status_code=400,
            detail=f"cannot pause from status={rows[0]['status']}",
        )
    await db.execute(
        "UPDATE instance_jobs SET status='paused', updated_at=datetime('now') "
        "WHERE job_id=%s",
        (job_id,),
        fetch=False,
    )
    return {"success": True, "job_id": job_id, "new_status": "paused"}


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str, request: Request):
    """v2.1: resume a paused job (back to pending so trigger can take it)."""
    viewer_id = await _resolve_viewer(request)
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    rows = await db.execute(
        "SELECT agent_id, status FROM instance_jobs WHERE job_id=%s LIMIT 1",
        (job_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="job not found")
    agent = await _assert_agent_visible(viewer_id, rows[0]["agent_id"])
    if agent["created_by"] != viewer_id:
        raise HTTPException(status_code=403, detail="not owned")
    if rows[0]["status"] != "paused":
        raise HTTPException(
            status_code=400,
            detail=f"cannot resume from status={rows[0]['status']}",
        )
    await db.execute(
        "UPDATE instance_jobs SET status='pending', updated_at=datetime('now') "
        "WHERE job_id=%s",
        (job_id,),
        fetch=False,
    )
    return {"success": True, "job_id": job_id, "new_status": "pending"}
