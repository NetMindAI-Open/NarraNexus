"""
@file_name: _health_server.py
@author: Bin Liang
@date: 2026-04-21
@description: Tiny FastAPI health endpoint mounted by LarkTrigger.

Why private: the trigger is the only caller. No module should import
this outside the trigger.

Port: 47831 (quiet range, no collision with the NarraNexus fleet of
74xx ports). Container-internal — compose.yml does not publish to the
host. Operators curl from inside the container:
    docker exec narranexus-lark curl -s localhost:47831/healthz

Routes:
    GET /healthz
        A single-shot snapshot of the trigger: status, subscribers,
        workers, queue depth, uptime, last WS connect wallclock,
        recent event-type counts.

The server is best-effort: if FastAPI/uvicorn aren't installed (tests,
stripped image) `start_health_server` silently returns None. The
trigger never fails to boot because of health server trouble.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Optional

from loguru import logger

if TYPE_CHECKING:
    from .lark_trigger import LarkTrigger


HEALTHZ_PORT = 47831


async def build_health_payload(trigger: "LarkTrigger") -> dict:
    """Pure function: snapshot the trigger state as a JSON-serialisable dict."""
    now_ms = int(time.time() * 1000)
    startup_ms = trigger._startup_time_ms or 0
    recent_counts: dict[str, int] = {}

    status = "ok" if trigger._audit_repo is not None and trigger.running else "starting"

    if trigger._audit_repo is not None:
        try:
            recent_counts = await trigger._audit_repo.count_by_type(since_hours=1)
        except Exception as e:  # noqa: BLE001 — health must degrade, not crash
            logger.warning(f"build_health_payload: count_by_type failed: {e}")

    return {
        "status": status,
        "running": trigger.running,
        "uptime_seconds": (now_ms - startup_ms) / 1000.0 if startup_ms else 0.0,
        "startup_time_ms": startup_ms,
        "last_ws_connected_ms": trigger._last_ws_connected_wallclock_ms,
        "subscriber_count": len(trigger._subscriber_tasks),
        "worker_count": len(trigger._workers),
        "queue_depth": trigger._task_queue.qsize(),
        "subscriber_app_ids": sorted(trigger._subscriber_creds.keys()),
        "recent_event_counts": recent_counts,
    }


async def start_health_server(
    trigger: "LarkTrigger",
    port: int = HEALTHZ_PORT,
) -> Optional[asyncio.Task]:
    """Spawn the /healthz HTTP server as an asyncio task.

    Returns the task so callers can cancel it on shutdown. Returns None
    if FastAPI/uvicorn aren't available (tests, minimal image) — the
    trigger continues to run without health.
    """
    try:
        from fastapi import FastAPI
        import uvicorn
    except ImportError as e:
        logger.warning(
            f"LarkTrigger: /healthz disabled (fastapi/uvicorn not installed: {e})"
        )
        return None

    app = FastAPI(title="lark-trigger-health", openapi_url=None, docs_url=None)

    @app.get("/healthz")
    async def _healthz():
        return await build_health_payload(trigger)

    # 0.0.0.0 so `docker exec ... curl` on the container's internal IP
    # works without the user hunting for the container IP.
    config = uvicorn.Config(
        app, host="0.0.0.0", port=port, log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    async def _run():
        try:
            await server.serve()
        except asyncio.CancelledError:
            await server.shutdown()
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"LarkTrigger health server crashed: {e}")

    task = asyncio.create_task(_run())
    logger.info(f"LarkTrigger health endpoint listening on :{port}/healthz")
    return task
