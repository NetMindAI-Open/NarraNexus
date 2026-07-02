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

import logging
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter

from xyz_agent_context.agent_framework.broker_client import broker_url
from xyz_agent_context.agent_runtime.admission import get_admission_controller
from xyz_agent_context.repository.executor_audit_repository import (
    ExecutorAuditRepository,
)
from xyz_agent_context.utils.db_factory import get_db_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/runtime", tags=["admin", "runtime"])


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
