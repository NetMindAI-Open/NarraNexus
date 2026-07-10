"""
@file_name: run_channel_triggers.py
@author: NetMind.AI
@date: 2026-07-08
@description: Consolidated supervisor for ALL IM channel triggers.

Replaces the old "one OS process per channel" layout (six near-identical
``run_<channel>_trigger.py`` entrypoints). Every ``ChannelTriggerBase`` subclass
now runs inside THIS single process, sharing one Python interpreter, one import
of the package, and one per-loop database pool.

Why consolidate
===============
- Memory: the heavy package import graph (agent_runtime -> agent_framework ->
  all modules -> narrative -> ...) was resident SIX times. Now once.
- SQLite: six processes each opened the same SQLite file, multiplying lock
  contention. One process = one opener.
- Maintenance: the six-process fact was hard-coded across run.sh, dev-local.sh,
  deploy-cloud.sh, and the Tauri desktop factories. One entrypoint collapses it.

``ChannelTriggerBase.start()`` is non-blocking (it spawns the credential watcher
+ workers as asyncio tasks and returns), and every trigger keeps its state on
the instance with no shared mutable globals — so N triggers coexist in one event
loop, each driving its own subscribers/workers independently. A crash in one
channel's task is contained by the base's per-task try/except; the supervisor
additionally isolates STARTUP failures (one channel failing to start never
aborts the others).

Usage
=====
    # all channels (default)
    python -m xyz_agent_context.module.run_channel_triggers

    # a subset — lets cloud split a high-volume channel into its own
    # container without any code change
    python -m xyz_agent_context.module.run_channel_triggers --only lark,slack

Out of scope: JobTrigger and MessageBusTrigger are NOT channel triggers (they do
not subclass ChannelTriggerBase) and keep their own processes.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
from typing import Optional

from loguru import logger

from xyz_agent_context.utils.logging import setup_logging


async def start_channel_triggers(
    db,
    only: Optional[set[str]] = None,
    trigger_map: Optional[dict] = None,
) -> list[tuple[str, object]]:
    """Instantiate + ``pre_start`` + ``start`` each selected channel trigger.

    Testable core of the supervisor: no DB acquisition, no infinite loop. Each
    channel is isolated — a failure to instantiate/pre_start/start one channel
    is logged and skipped so the remaining channels still come up.

    Args:
        db: the shared AsyncDatabaseClient (already acquired by ``main``).
        only: subset of channel names to start; None means all.
        trigger_map: name -> trigger class; defaults to CHANNEL_TRIGGER_MAP
            (injectable for tests).

    Returns:
        List of (channel_name, live trigger instance) that started successfully.
    """
    if trigger_map is None:
        from xyz_agent_context.module.channel_trigger_map import CHANNEL_TRIGGER_MAP

        trigger_map = CHANNEL_TRIGGER_MAP

    if only:
        unknown = only - set(trigger_map)
        if unknown:
            logger.warning(
                f"[supervisor] --only names unknown channel(s) {sorted(unknown)}; "
                f"known: {sorted(trigger_map)}"
            )

    started: list[tuple[str, object]] = []
    for name, trigger_cls in trigger_map.items():
        if only and name not in only:
            continue
        try:
            trigger = trigger_cls(max_workers=3)
            # Channel-specific one-off migration (default no-op), then start the
            # (non-blocking) worker pool + credential watcher.
            await trigger.pre_start(db)
            await trigger.start(db)
            started.append((name, trigger))
        except Exception as e:  # noqa: BLE001 — one bad channel must not abort the rest
            logger.exception(f"[supervisor] channel '{name}' failed to start: {e}")

    if not started:
        logger.warning(
            "[supervisor] no channel triggers started "
            f"(only={sorted(only) if only else 'ALL'}) — idling"
        )
    else:
        logger.info(
            f"[supervisor] started {len(started)} channel(s): "
            f"{[n for n, _ in started]}"
        )
    return started


async def main(only: Optional[set[str]] = None) -> None:
    from xyz_agent_context.utils.db_factory import get_db_client, close_db_client
    from xyz_agent_context.utils.schema_registry import auto_migrate
    from xyz_agent_context.channel.channel_health_server import (
        start_channel_health_server,
    )

    db = await get_db_client()
    await auto_migrate(db._backend)

    started = await start_channel_triggers(db, only)

    # One aggregated /healthz for every channel (best-effort; None in tests).
    health_task = await start_channel_health_server(started)

    # Graceful shutdown. We install our OWN handlers for BOTH signals:
    #   - SIGINT  (Ctrl+C): asyncio.run() would handle this, but the health
    #     server's uvicorn is told not to install handlers, so we own it here.
    #   - SIGTERM (systemd / docker stop): asyncio.run() does NOT handle this by
    #     default, so without this the cloud/container stop path would hard-kill
    #     the process instead of letting each channel stop() cleanly.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # pragma: no cover — non-Unix platforms
            pass

    try:
        await stop_event.wait()
    finally:
        logger.info("[supervisor] shutting down channel triggers...")
        for _, trigger in started:
            try:
                await trigger.stop()
            except Exception as e:  # noqa: BLE001 — best-effort shutdown
                logger.warning(f"[supervisor] stop failed: {e}")
        if health_task is not None:
            # Cancel AND await so uvicorn's ``server.shutdown()`` (in the
            # task's CancelledError handler) actually runs — a bare cancel()
            # leaves it pending and the socket only closes on process exit.
            health_task.cancel()
            await asyncio.gather(health_task, return_exceptions=True)
        # Close the shared DB client. We opened it, so we close it — the
        # aiosqlite backend runs its single connection on a background thread
        # that otherwise keeps the process alive after main() returns, turning
        # a clean SIGINT/SIGTERM into a hang.
        try:
            await close_db_client()
        except Exception as e:  # noqa: BLE001 — best-effort shutdown
            logger.warning(f"[supervisor] close_db_client failed: {e}")
        # Drain loguru's async sinks inside this loop scope — the same reason
        # the old per-channel entrypoints did (a fresh asyncio.run() would bind
        # complete() to a closed loop). See run_lark_trigger history.
        flush = logger.complete()
        if hasattr(flush, "__await__"):
            await flush


def _parse_only(raw: Optional[str]) -> Optional[set[str]]:
    """'lark,slack' -> {'lark','slack'}; None/'' -> None (means ALL)."""
    if not raw:
        return None
    names = {part.strip() for part in raw.split(",") if part.strip()}
    return names or None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run all (or a subset of) IM channel triggers in one process."
    )
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated channel names to run (default: all). "
        "e.g. --only lark,slack",
    )
    args = parser.parse_args()

    setup_logging("channel_triggers")
    logger.info("Starting consolidated channel-trigger supervisor...")
    asyncio.run(main(_parse_only(args.only)))
