"""
@file_name: channel_health_server.py
@author: NetMind.AI
@date: 2026-07-08
@description: One /healthz endpoint reporting ALL consolidated channel triggers.

Generalised from the old ``lark_module/_health_server.py`` (which snapshotted a
single ``LarkTrigger``). Since every IM channel now runs inside ONE supervisor
process (``module/run_channel_triggers.py``), a single health server reports a
per-channel snapshot for every trigger the supervisor brought up — closing the
old observability gap where only Lark had a health endpoint.

Port: 47831 (unchanged from the Lark server — quiet range, no collision with the
NarraNexus 74xx fleet). Container-internal; operators curl from inside:
    docker exec <container> curl -s localhost:47831/healthz

The payload reads only ``ChannelTriggerBase`` attributes, so it works for any
trigger. Lark-specific fields (last WS connect wallclock) are read via
``getattr`` with a default, so channels that don't track them simply report 0.

Best-effort: if FastAPI/uvicorn aren't installed (tests, stripped image)
``start_channel_health_server`` returns None and the supervisor runs without
health. It never blocks trigger startup.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Optional

from loguru import logger

if TYPE_CHECKING:
    from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase


HEALTHZ_PORT = 47831


async def _snapshot_one(name: str, trigger: "ChannelTriggerBase") -> dict:
    """Snapshot a single trigger's state as a JSON-serialisable dict.

    Reads only base-class attributes plus optional Lark-specific ones via
    getattr, so it is channel-agnostic.
    """
    now_ms = int(time.time() * 1000)
    startup_ms = trigger._startup_time_ms or 0

    recent_counts: dict[str, int] = {}
    if trigger._audit_repo is not None:
        try:
            recent_counts = await trigger._audit_repo.count_by_type(since_hours=1)
        except Exception as e:  # noqa: BLE001 — health must degrade, not crash
            logger.warning(f"[health:{name}] count_by_type failed: {e}")

    status = "ok" if trigger._audit_repo is not None and trigger.running else "starting"

    return {
        "status": status,
        "running": trigger.running,
        "uptime_seconds": (now_ms - startup_ms) / 1000.0 if startup_ms else 0.0,
        "startup_time_ms": startup_ms,
        # Lark tracks this; other channels don't — default 0.
        "last_ws_connected_ms": getattr(trigger, "_last_ws_connected_wallclock_ms", 0),
        "subscriber_count": len(trigger._subscriber_tasks),
        "worker_count": len(trigger._workers),
        "queue_depth": trigger._task_queue.qsize(),
        "subscriber_keys": sorted(trigger._subscriber_creds.keys()),
        "recent_event_counts": recent_counts,
    }


async def build_health_payload(
    triggers: list[tuple[str, "ChannelTriggerBase"]],
) -> dict:
    """Aggregate per-channel snapshots for the supervisor.

    Overall status is ``ok`` only when every trigger reports ``ok``; otherwise
    ``degraded`` (a channel still starting or with no audit repo yet).
    """
    channels = {name: await _snapshot_one(name, t) for name, t in triggers}
    overall = (
        "ok"
        if channels and all(c["status"] == "ok" for c in channels.values())
        else "degraded"
    )
    return {
        "status": overall,
        "channel_count": len(channels),
        "channels": channels,
    }


async def start_channel_health_server(
    triggers: list[tuple[str, "ChannelTriggerBase"]],
    port: int = HEALTHZ_PORT,
) -> Optional[asyncio.Task]:
    """Spawn the aggregated /healthz server as an asyncio task.

    Returns the task so the supervisor can cancel it on shutdown, or None if
    FastAPI/uvicorn aren't available (tests, minimal image).
    """
    try:
        from fastapi import FastAPI
        import uvicorn
    except ImportError as e:
        logger.warning(
            f"channel health: /healthz disabled (fastapi/uvicorn not installed: {e})"
        )
        return None

    app = FastAPI(title="channel-triggers-health", openapi_url=None, docs_url=None)

    @app.get("/healthz")
    async def _healthz():
        return await build_health_payload(triggers)

    # 0.0.0.0 so `docker exec ... curl` works without hunting the container IP.
    config = uvicorn.Config(
        app, host="0.0.0.0", port=port, log_level="warning", access_log=False,
    )
    server = uvicorn.Server(config)
    # The supervisor owns SIGINT/SIGTERM. Without this, uvicorn installs its own
    # handlers that swallow SIGINT, so the supervisor's shutdown wait never
    # wakes and the process hangs on Ctrl+C / systemd stop.
    server.install_signal_handlers = lambda: None

    async def _run():
        try:
            await server.serve()
        except asyncio.CancelledError:
            await server.shutdown()
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"channel health server crashed: {e}")

    task = asyncio.create_task(_run())
    logger.info(
        f"channel health endpoint listening on :{port}/healthz "
        f"({len(triggers)} channel(s))"
    )
    return task
