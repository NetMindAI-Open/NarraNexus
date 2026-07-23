"""
@file_name: admin_runtime.py
@author: Bin Liang
@date: 2026-06-18
@description: GET /api/admin/runtime/status — read-only L2 observability for
the executor scheduling / resource system.

Combines three resilient sections so a single failing source never 500s the
endpoint:
  - admission: the live two-level concurrency gate snapshot (active users /
    loops, per-cap limits, queue depth, free-mem vs guard).
  - executors: the broker's live per-user executor list (empty when no broker
    is configured or it is unreachable).
  - audit_counts: recent instance_executor_audit event counts (last hour),
    for spotting OOM / cull / orphan-reap spikes.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter

from xyz_agent_context.agent_framework.broker_client import broker_url
from xyz_agent_context.agent_runtime.admission import get_admission_controller
from xyz_agent_context.repository.executor_audit_repository import (
    ExecutorAuditRepository,
)
from xyz_agent_context.repository.service_audit_repository import (
    EVENT_STARTED,
    ServiceAuditRepository,
)
from xyz_agent_context.utils.db_factory import get_db_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/runtime", tags=["admin", "runtime"])

# The consolidated worker supervisor's ServiceAuditor service name (see
# xyz_agent_context/module/run_worker_supervisor.py). Its heartbeat `detail`
# carries the per-worker liveness snapshot the System page's Workers card wants.
_WORKER_SUPERVISOR_SERVICE = "worker_supervisor"


async def _get_executor_list() -> list:
    """Live executor list from the broker. [] when no broker configured.

    May raise on broker errors — the route handler guards the call so a
    broker outage never 500s the status endpoint.
    """
    url = broker_url()
    if not url:
        return []
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{url}/executors")
        resp.raise_for_status()
        return resp.json().get("executors", [])


async def _get_audit_counts() -> dict:
    """Recent (last hour) executor audit event counts. May raise — guarded."""
    db = await get_db_client()
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    return await ExecutorAuditRepository(db).counts_since(since)


@router.get("/status")
async def runtime_status() -> dict:
    """Live scheduling/resource state. Read-only; never 500s on a sub-section."""
    try:
        executors = await _get_executor_list()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[runtime-status] broker executor list unavailable: {e}")
        executors = []

    try:
        audit_counts = await _get_audit_counts()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[runtime-status] audit counts unavailable: {e}")
        audit_counts = {}

    return {
        "admission": get_admission_controller().snapshot(),
        "executors": executors,
        "audit_counts": audit_counts,
    }


# ---------------------------------------------------------------------------
# Worker-supervisor liveness (System page Workers card)
# ---------------------------------------------------------------------------


def _parse_detail(detail: Any) -> dict:
    """Best-effort parse of a service_audit `detail` blob into a dict."""
    if isinstance(detail, dict):
        return detail
    if isinstance(detail, str) and detail:
        try:
            parsed = json.loads(detail)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _row_age_seconds(created_at: Any) -> Optional[float]:
    """Seconds since a service_audit row's `created_at` (UTC), or None.

    `created_at` is written as ``datetime('now')`` — space-separated UTC, no
    tz suffix (SQLite) or DATETIME(6) (MySQL). Parse leniently; a parse miss
    just means "age unknown", never an error.
    """
    if not created_at:
        return None
    text = str(created_at).strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _snapshot_to_workers(event_type: str, detail: dict) -> list[dict]:
    """Normalise a heartbeat OR started `detail` into a per-worker list.

    Heartbeat detail is ``{name: {state, restart_count, last_error}}``; the
    `started` fallback is ``{"workers": [names]}`` (no per-worker state yet →
    reported as ``starting``).
    """
    if event_type == EVENT_STARTED:
        names = detail.get("workers") or []
        return [
            {"name": n, "state": "starting", "restart_count": 0, "last_error": None}
            for n in names
        ]
    workers = []
    for name, entry in detail.items():
        entry = entry if isinstance(entry, dict) else {}
        workers.append(
            {
                "name": name,
                "state": entry.get("state", "unknown"),
                "restart_count": entry.get("restart_count", 0),
                "last_error": entry.get("last_error"),
            }
        )
    return workers


async def _get_worker_snapshot() -> dict:
    """Latest worker-supervisor liveness snapshot. May raise — guarded by the
    route handler so a DB blip never 500s the endpoint."""
    db = await get_db_client()
    repo = ServiceAuditRepository(db)
    row = await repo.last_heartbeat(_WORKER_SUPERVISOR_SERVICE)
    if row is None:
        # No heartbeat yet (just booted): fall back to the `started` row so the
        # card at least lists which workers were launched.
        started = await repo.recent(
            service=_WORKER_SUPERVISOR_SERVICE, event_type=EVENT_STARTED, limit=1
        )
        row = started[0] if started else None
    if row is None:
        return {"available": False, "heartbeat_age_seconds": None, "workers": []}

    event_type = row.get("event_type", "")
    detail = _parse_detail(row.get("detail"))
    return {
        "available": True,
        "event": event_type,
        "heartbeat_age_seconds": _row_age_seconds(row.get("created_at")),
        "workers": _snapshot_to_workers(event_type, detail),
    }


@router.get("/workers")
async def worker_status() -> dict:
    """Per-worker liveness of the consolidated worker supervisor.

    Read-only L2 view for the desktop System page's single "Workers" card:
    the four merged workers (poller / jobs / bus / channels) share one process,
    so the process-level card cannot tell which sub-worker is flapping — this
    surfaces each one's state + cumulative restart_count. Never 500s: an
    unreachable DB or an absent supervisor yields ``available: false``.
    """
    try:
        return await _get_worker_snapshot()
    except Exception as e:  # noqa: BLE001 — advisory view, never 500s
        logger.warning(f"[runtime-workers] snapshot unavailable: {e}")
        return {"available": False, "heartbeat_age_seconds": None, "workers": []}
