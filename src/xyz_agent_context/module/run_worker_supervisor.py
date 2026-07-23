"""
@file_name: run_worker_supervisor.py
@author: NetMind.AI
@date: 2026-07-22
@description: Consolidated supervisor for ALL long-running background workers.

Replaces the old "one OS process per worker" layout (module_poller,
job_trigger, message_bus_trigger, run_channel_triggers as four separate
``python -m ...`` processes) with ONE process running every worker inside a
single asyncio event loop, sharing one Python interpreter, one import of the
heavy package graph, and one per-loop database pool.

Why consolidate
===============
- Memory: ``import xyz_agent_context`` costs ~128 MB resident per process; four
  worker processes paid that four times over. Now once. (MCP stays a separate
  process on purpose — it is a port-bound SSE server, a different kind of thing,
  and already single-process via ``run_mcp_servers_async``.)
- SQLite: four processes each opened the same file, multiplying lock contention.
  One process = one opener (the same win ``run_channel_triggers`` already banked
  for the six IM-channel processes).
- Maintenance: the four-process fact was hard-coded across run.sh, dev-local.sh,
  .dev-local-safe.sh, deploy-cloud.sh, and the Tauri desktop factories.

This is the same collapse ``run_channel_triggers`` did for channels, one layer
up: that file's ``main()`` is the direct template for the shutdown machinery
here (own-signal handling, close_db_client, loguru drain). The channels group is
itself supervised as ONE of the workers here (via ``start_channel_triggers``),
so there is a single supervisor, not a supervisor-of-supervisors.

Supervision model
=================
Each worker runs as a supervised asyncio task with **per-task exponential
backoff restart** (binding rule / incident lesson #2: no naked fire-and-forget —
every wrapper is awaited via ``asyncio.gather``). A worker task that RAISES is a
crash → audited + backed off + restarted; a task cancelled during shutdown is
NOT restarted. There is deliberately **no** cap on how long a worker may run
without returning: a worker blocked for hours is HEALTHY, not a hang (binding
rule #14). The only timeouts are the restart backoff and each worker's own
internal drain.

The supervisor emits its own L2 heartbeat (``ServiceAuditor("worker_supervisor")``)
carrying a per-worker liveness snapshot, so the ``service_audit`` table gives
one-row-per-minute liveness across all merged workers (incident lesson #4).

Usage
=====
    # all workers (default)
    python -m xyz_agent_context.module.run_worker_supervisor

    # a subset — lets cloud split workers across containers with no code change
    python -m xyz_agent_context.module.run_worker_supervisor --only poller,jobs
    python -m xyz_agent_context.module.run_worker_supervisor --exclude channels

    # split channels themselves (orthogonal to --only), same as the old
    # run_channel_triggers --only:
    python -m xyz_agent_context.module.run_worker_supervisor --only channels --channels lark,slack

The individual workers keep their own ``if __name__ == "__main__"`` blocks as
standalone debugging entrypoints; they are no longer wired into any launcher.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Awaitable, Callable, Optional, Sequence, cast

from loguru import logger

from xyz_agent_context.utils.logging import setup_logging

# Imported at module top (not lazily) so tests can monkeypatch them and so the
# channels worker can be wired without re-importing inside the hot path. Both
# are cheap: start_channel_triggers lazy-imports CHANNEL_TRIGGER_MAP; the health
# server lazy-imports fastapi/uvicorn inside its own body.
from xyz_agent_context.module.run_channel_triggers import start_channel_triggers
from xyz_agent_context.channel.channel_health_server import (
    start_channel_health_server,
)

# All known worker names. --only / --exclude select over this set.
ALL_WORKERS = ("poller", "jobs", "bus", "channels")

# Restart backoff bounds (seconds). Module-level so tests can shrink them.
_BACKOFF_BASE = 1.0
_BACKOFF_CAP = 60.0

# Supervisor heartbeat cadence (seconds). Kept modest so the desktop System
# page's Workers panel (which reads the latest heartbeat row from service_audit)
# is at most this stale; `restart_count` in the snapshot is cumulative, so even
# a between-beats flap surfaces on the next tick.
_HEARTBEAT_INTERVAL = 30.0


# =============================================================================
# Worker handle + spec
# =============================================================================


@dataclass
class WorkerHandle:
    """One buildable run of a worker.

    Attributes:
        run: the BLOCKING coroutine to supervise. Returns only on graceful stop
            or cancel; raising == crash (triggers backoff + restart).
        stop: graceful-drain callable invoked on shutdown BEFORE the wrapper is
            cancelled. May be sync (sets a flag) or async (awaits a drain); the
            supervisor normalises both.
    """

    run: Awaitable[None]
    stop: Callable[[], Any]


@dataclass(frozen=True)
class WorkerSpec:
    """Static description of a supervised worker.

    ``factory`` is called ONCE PER (re)start — a coroutine cannot be awaited
    twice and each restart needs a fresh worker instance (the previous run's
    internal task lists were consumed by its stop/crash).

    ``stable_after_s`` is how long a run must survive before the backoff resets
    to base: a worker that ran for hours then crashed restarts immediately; a
    tight crash-loop escalates the backoff.
    """

    name: str
    factory: Callable[["SupervisorContext"], Awaitable[WorkerHandle]]
    stable_after_s: float = 60.0


# =============================================================================
# Shared context
# =============================================================================


@dataclass
class SupervisorContext:
    """Shared state handed to every worker factory and supervise loop.

    Holds the ONE per-loop DB, the shared ``stop_event``, the supervisor's
    ``ServiceAuditor``, per-worker liveness state (for the heartbeat), restart
    counters, and registered stop callables. Constructed once in ``run()``;
    unit tests build it directly with a fake auditor and ``db=None``.
    """

    db: Any
    audit: Any  # ServiceAuditor (or a fake in tests)
    stop_event: asyncio.Event
    channel_only: Optional[set[str]] = None
    restarts: dict[str, int] = field(default_factory=dict)
    _states: dict[str, dict] = field(default_factory=dict)
    _stops: dict[str, Callable[[], Any]] = field(default_factory=dict)

    def set_state(
        self,
        name: str,
        state: str,
        *,
        restart_count: Optional[int] = None,
        last_error: Optional[str] = None,
    ) -> None:
        entry = self._states.setdefault(name, {})
        entry["state"] = state
        if restart_count is not None:
            entry["restart_count"] = restart_count
        else:
            entry.setdefault("restart_count", self.restarts.get(name, 0))
        if last_error is not None:
            entry["last_error"] = last_error

    def register_stop(self, name: str, stop: Callable[[], Any]) -> None:
        self._stops[name] = stop

    def stops(self) -> list[tuple[str, Callable[[], Any]]]:
        """Registered (name, stop) pairs — read accessor so shutdown code does
        not reach into the private `_stops` field."""
        return list(self._stops.items())

    def liveness_snapshot(self) -> dict:
        """Deep-enough copy of per-worker state for the heartbeat detail blob."""
        return {name: dict(entry) for name, entry in self._states.items()}


# =============================================================================
# Worker factories
# =============================================================================
#
# Heavy worker classes are imported INSIDE each factory (lazy) so that importing
# this module for build_specs / unit tests does not drag in the full worker
# graph. Each factory returns a FRESH WorkerHandle every call (see WorkerSpec).


async def _poller_factory(ctx: SupervisorContext) -> WorkerHandle:
    from xyz_agent_context.services.module_poller import ModulePoller

    inst = ModulePoller(poll_interval=5, max_workers=3)
    return WorkerHandle(run=inst.start(), stop=inst.stop)


async def _jobs_factory(ctx: SupervisorContext) -> WorkerHandle:
    from xyz_agent_context.module.job_module.job_trigger import JobTrigger

    inst = JobTrigger(poll_interval=60, max_workers=5)
    return WorkerHandle(run=inst.start(), stop=inst.stop)


async def _bus_factory(ctx: SupervisorContext) -> WorkerHandle:
    from xyz_agent_context.message_bus.message_bus_trigger import (
        MessageBusTrigger,
        _get_bus,
    )

    # _get_bus() does get_db_client + auto_migrate + bootstrap_quota_subsystem
    # (all idempotent) on THIS loop, so the bus shares the per-loop pool.
    bus = await _get_bus()
    inst = MessageBusTrigger(bus=bus)
    return WorkerHandle(run=inst.start(), stop=inst.stop)  # stop is sync (flag)


async def _channels_factory(ctx: SupervisorContext) -> WorkerHandle:
    """Adapter: channels do not fit the "blocking coroutine" shape.

    ``start_channel_triggers`` is non-blocking (each ChannelTriggerBase.start()
    spawns its own tasks and returns; the base already isolates per-task crashes
    internally). We therefore wrap the channel GROUP as a coroutine that starts
    the channels + the aggregated /healthz server (port 47831) and then blocks
    on ``stop_event`` — giving the uniform supervise loop a "blocking coroutine
    that returns on stop". We do NOT invent a per-channel restart here; that
    would fight the channels' own supervision and double-bind the health port.
    """
    started = await start_channel_triggers(ctx.db, only=ctx.channel_only)
    health_task = await start_channel_health_server(cast(Any, started))

    async def _run() -> None:
        try:
            await ctx.stop_event.wait()
        finally:
            # Ownership of channel + health shutdown lives here (mirrors
            # run_channel_triggers.main()): stop each trigger, then cancel AND
            # await the health task so uvicorn's server.shutdown() actually runs
            # (a bare cancel leaves the 47831 socket open until process exit).
            for _name, trigger in started:
                try:
                    await cast(Any, trigger).stop()
                except Exception as e:  # noqa: BLE001 — best-effort shutdown
                    logger.warning(f"[supervisor] channel stop failed: {e}")
            if health_task is not None:
                health_task.cancel()
                await asyncio.gather(health_task, return_exceptions=True)

    # The channels group drains itself inside _run (on stop_event). Its `stop`
    # just sets the event, which _run is already awaiting.
    return WorkerHandle(run=_run(), stop=ctx.stop_event.set)


# name -> spec. build_specs() selects over this. Default = all four.
WORKER_SPECS: dict[str, WorkerSpec] = {
    "poller": WorkerSpec("poller", _poller_factory),
    "jobs": WorkerSpec("jobs", _jobs_factory),
    "bus": WorkerSpec("bus", _bus_factory),
    "channels": WorkerSpec("channels", _channels_factory),
}


def build_specs(
    only: Optional[set[str]] = None,
    exclude: Optional[set[str]] = None,
) -> list[WorkerSpec]:
    """Resolve --only / --exclude over WORKER_SPECS. Default (neither) = all.

    Unknown names warn but do not abort — the supervisor comes up with the valid
    subset (mirrors ``start_channel_triggers``' unknown-name handling). An empty
    result is allowed (the caller idles on ``stop_event`` rather than exiting, so
    a misconfigured container restarts predictably instead of crash-looping).
    """
    names = set(ALL_WORKERS)
    for label, sel in (("--only", only), ("--exclude", exclude)):
        if sel:
            unknown = sel - names
            if unknown:
                logger.warning(
                    f"[supervisor] {label} names unknown worker(s) {sorted(unknown)}; "
                    f"known: {sorted(names)}"
                )
    if only:
        names &= only
    if exclude:
        names -= exclude
    # Preserve the canonical ALL_WORKERS order for deterministic startup.
    return [WORKER_SPECS[n] for n in ALL_WORKERS if n in names]


# =============================================================================
# Supervision
# =============================================================================


async def _supervise(ctx: SupervisorContext, spec: WorkerSpec) -> None:
    """Run + restart one worker until ``stop_event``.

    Control flow (see module docstring): build a fresh handle, await its
    blocking ``run`` (no timeout — long runs are healthy). A raised exception is
    a crash → audit + exponential backoff + restart; ``CancelledError`` is a
    shutdown signal → propagate, never restart. The backoff resets once a run
    has survived ``stable_after_s``, and the backoff sleep itself is interrupted
    by ``stop_event`` so SIGTERM does not wait out a full 60 s.
    """
    backoff = _BACKOFF_BASE
    while not ctx.stop_event.is_set():
        ctx.set_state(spec.name, "starting", restart_count=ctx.restarts.get(spec.name, 0))
        run_started = monotonic()
        try:
            handle = await spec.factory(ctx)
            ctx.register_stop(spec.name, handle.stop)
            ctx.set_state(spec.name, "running")
            run_started = monotonic()
            await handle.run
            # Normal return: only expected during shutdown.
            if ctx.stop_event.is_set():
                ctx.set_state(spec.name, "stopped")
                return
            # A healthy long-running worker never returns on its own. Treat an
            # unexpected return as a restart, but it is NOT an error → no audit.
            logger.warning(
                f"[supervisor] worker '{spec.name}' returned unexpectedly; restarting"
            )
        except asyncio.CancelledError:
            ctx.set_state(spec.name, "stopped")
            raise
        except Exception as e:  # noqa: BLE001 — crash: audit + backoff + restart
            ctx.restarts[spec.name] = ctx.restarts.get(spec.name, 0) + 1
            ctx.set_state(
                spec.name, "restarting",
                restart_count=ctx.restarts[spec.name], last_error=repr(e),
            )
            await ctx.audit.error(
                {
                    "worker": spec.name,
                    "restart_count": ctx.restarts[spec.name],
                    "error": repr(e),
                }
            )
            logger.exception(f"[supervisor] worker '{spec.name}' crashed: {e}")

        # Restart tail (reached by crash OR unexpected normal return).
        if (monotonic() - run_started) >= spec.stable_after_s:
            backoff = _BACKOFF_BASE
        try:
            # Sleep the backoff, but wake immediately if shutdown starts.
            await asyncio.wait_for(ctx.stop_event.wait(), timeout=backoff)
            ctx.set_state(spec.name, "stopped")
            return
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, _BACKOFF_CAP)


async def _heartbeat_loop(ctx: SupervisorContext) -> None:
    """Emit a per-worker liveness snapshot to ``service_audit`` until shutdown.

    Its own try/except: a heartbeat failure never kills the supervisor (the
    auditor is already best-effort — the observer must not break the observed).
    Emits FIRST, then waits, so the Workers panel has a snapshot within a tick
    of startup instead of after a full interval.
    """
    while not ctx.stop_event.is_set():
        try:
            await ctx.audit.heartbeat(ctx.liveness_snapshot(), force=True)
        except Exception as e:  # noqa: BLE001 — observer never breaks observed
            logger.warning(f"[supervisor] heartbeat failed: {e}")
        try:
            await asyncio.wait_for(ctx.stop_event.wait(), timeout=_HEARTBEAT_INTERVAL)
            return  # stop_event fired → exit
        except asyncio.TimeoutError:
            pass


# =============================================================================
# Shutdown
# =============================================================================


async def _call_stop(name: str, stop: Callable[[], Any]) -> None:
    """Invoke a worker's stop, normalising sync (flag) vs async (drain)."""
    try:
        res = stop()
        if asyncio.iscoroutine(res) or isinstance(res, Awaitable):
            await res
    except Exception as e:  # noqa: BLE001 — best-effort shutdown
        logger.warning(f"[supervisor] stop '{name}' failed: {e}")


async def _drain_and_close(
    ctx: SupervisorContext,
    wrappers: Sequence[asyncio.Future],
    heartbeat: asyncio.Future,
) -> None:
    """Graceful shutdown sequence. Each step is error-isolated.

    Order: graceful stop each worker FIRST (lets poller/jobs run their 30 s
    queue-drain and the bus flag-exit its poll loop), THEN cancel + await the
    wrappers as a backstop (also breaks any wrapper sitting in a backoff sleep),
    then the heartbeat, then audit.stopped, then close the DB we opened, then
    drain loguru — all inside this loop scope.
    """
    ctx.stop_event.set()

    # Drain all workers CONCURRENTLY. ModulePoller.stop / JobTrigger.stop each
    # block up to 30s on their queue join; serialising them would stack to 60s+
    # and, under a fixed docker/systemd stop grace, later workers would be
    # SIGKILL'd before their drain even starts. Concurrent → total ≈ max(30s).
    await asyncio.gather(
        *(_call_stop(name, stop) for name, stop in ctx.stops()),
        return_exceptions=True,
    )

    for w in wrappers:
        w.cancel()
    await asyncio.gather(*wrappers, return_exceptions=True)

    heartbeat.cancel()
    await asyncio.gather(heartbeat, return_exceptions=True)

    try:
        await ctx.audit.stopped(ctx.liveness_snapshot())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[supervisor] audit.stopped failed: {e}")

    # Close the shared DB client — we opened it, so we close it. The aiosqlite
    # backend runs its connection on a background thread that otherwise keeps
    # the process alive after run() returns, turning a clean signal into a hang
    # (same reason as run_channel_triggers.main()).
    try:
        from xyz_agent_context.utils.db_factory import close_db_client

        await close_db_client()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[supervisor] close_db_client failed: {e}")

    flush = logger.complete()
    if hasattr(flush, "__await__"):
        await flush


# =============================================================================
# Entrypoint
# =============================================================================


async def run(
    only: Optional[set[str]] = None,
    exclude: Optional[set[str]] = None,
    channel_only: Optional[set[str]] = None,
) -> None:
    """Supervisor entrypoint. Reuses run_channel_triggers.main()'s template."""
    import xyz_agent_context.settings  # noqa: F401 — ensure .env is loaded
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils.schema_registry import auto_migrate
    from xyz_agent_context.agent_framework.quota_service import (
        bootstrap_quota_subsystem,
    )
    from xyz_agent_context.services.service_audit import ServiceAuditor

    specs = build_specs(only, exclude)
    logger.info(
        f"[supervisor] starting workers: {[s.name for s in specs] or 'NONE (idle)'}"
    )

    # ONE db + migration + quota bootstrap up front, on this loop.
    db = await get_db_client()
    await auto_migrate(db._backend)
    await bootstrap_quota_subsystem(db)

    ctx = SupervisorContext(
        db=db,
        audit=ServiceAuditor("worker_supervisor"),
        stop_event=asyncio.Event(),
        channel_only=channel_only,
    )
    await ctx.audit.started({"workers": [s.name for s in specs]})

    # Install our OWN handlers for BOTH signals (see run_channel_triggers): the
    # health server's uvicorn is told not to install handlers, and asyncio.run
    # does not handle SIGTERM by default. Installed AFTER stop_event exists,
    # BEFORE we await it (a signal arriving now just pre-sets the event).
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, ctx.stop_event.set)
        except NotImplementedError:  # pragma: no cover — non-Unix
            pass

    wrappers = [asyncio.ensure_future(_supervise(ctx, s)) for s in specs]
    heartbeat = asyncio.ensure_future(_heartbeat_loop(ctx))

    try:
        await ctx.stop_event.wait()
    finally:
        logger.info("[supervisor] shutting down workers...")
        await _drain_and_close(ctx, wrappers, heartbeat)


def _parse_csv(raw: Optional[str]) -> Optional[set[str]]:
    """'a,b' -> {'a','b'}; None/'' -> None (means default)."""
    if not raw:
        return None
    names = {part.strip() for part in raw.split(",") if part.strip()}
    return names or None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run all (or a subset of) long-running background workers "
        "in one process."
    )
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated worker names to run (default: all). "
        f"Known: {','.join(ALL_WORKERS)}",
    )
    parser.add_argument(
        "--exclude",
        default="",
        help="Comma-separated worker names to skip (applied after --only).",
    )
    parser.add_argument(
        "--channels",
        default="",
        help="Comma-separated channel subset within the 'channels' worker "
        "(orthogonal to --only), e.g. --channels lark,slack.",
    )
    args = parser.parse_args()

    setup_logging("worker_supervisor")
    logger.info("Starting consolidated worker supervisor...")
    asyncio.run(
        run(
            only=_parse_csv(args.only),
            exclude=_parse_csv(args.exclude),
            channel_only=_parse_csv(args.channels),
        )
    )
