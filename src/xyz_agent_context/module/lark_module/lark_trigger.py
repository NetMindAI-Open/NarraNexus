"""
@file_name: lark_trigger.py
@date: 2026-04-10
@description: Lark event trigger — listens for incoming messages via
lark-oapi SDK WebSocket long connection.

Architecture: 1 WebSocket thread per bound bot + N shared async workers.
When a colleague sends a message to the bot, the trigger:
1. SDK callback puts event into async task_queue
2. Worker picks up event, builds context via LarkContextBuilder
3. Runs AgentRuntime
4. Writes result to Inbox
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import uuid

from loguru import logger

from xyz_agent_context.schema.channel_tag import ChannelTag
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.module.lark_module._lark_credential_manager import (
    LarkCredential,
    LarkCredentialManager,
)
from xyz_agent_context.module.lark_module.lark_cli_client import LarkCLIClient
from xyz_agent_context.module.lark_module.lark_context_builder import LarkContextBuilder
from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime
from xyz_agent_context.agent_runtime.run_collector import (
    RunError,
    collect_run,
)
from xyz_agent_context.channel.channel_context_builder_base import ChannelHistoryConfig
from xyz_agent_context.repository.lark_seen_message_repository import (
    LarkSeenMessageRepository,
)
from xyz_agent_context.repository.lark_trigger_audit_repository import (
    LarkTriggerAuditRepository,
    EVENT_HEARTBEAT,
    EVENT_SUBSCRIBER_STARTED,
    EVENT_SUBSCRIBER_STOPPED,
    EVENT_WS_CONNECTED,
    EVENT_WS_DISCONNECTED,
    EVENT_WS_BACKOFF,
    EVENT_WORKER_ERROR,
    EVENT_WORKER_TIMEOUT,
    EVENT_INGRESS_PROCESSED,
    EVENT_INGRESS_DROPPED_DEDUP,
    EVENT_INGRESS_DROPPED_HISTORIC,
    EVENT_INGRESS_DROPPED_ECHO,
    EVENT_INGRESS_DROPPED_UNBOUND,
    EVENT_DEDUP_FAIL_OPEN,
    EVENT_INBOX_WRITE_FAILED,
)
from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils.timezone import utc_now


# H-6 (2026-04-27): replace lark_oapi.ws.client.loop module global with a
# thread-local proxy.
#
# Background — what the SDK does:
# `lark_oapi.ws.client` defines a module-level `loop = asyncio.get_event_loop()`
# captured once at import time on the main thread. Every Client method then
# reads this global at every use:
#     loop.run_until_complete(self._connect())
#     loop.create_task(self._receive_message_loop())
#     loop.create_task(self._handle_message(msg))
#     ...
# The SDK is implicitly designed for one Client per process.
#
# Why the previous M-9 patch was insufficient:
# NarraNexus runs N Client instances concurrently — one daemon thread per bot
# — and the previous workaround patched `ws_mod.loop = fresh_loop` per thread
# under `_WS_LOOP_PATCH_LOCK`. The lock only covered the assignment, not the
# subsequent `ws_client.start()` call. After thread A released the lock,
# thread B could overwrite the global with `fresh_loop_B`. Thread A's
# `start()` then reads `loop` on every line, intermittently picking up
# thread B's loop, and the `_receive_message_loop` task ends up bound to a
# different loop than the websocket future it awaits. Result:
# `RuntimeError: Task got Future <Future pending> attached to a different
# loop`. Reproduced in /tmp/lark_loop_race_reproducer.py: 28/40 observations
# saw a foreign thread's loop with 5 racing threads.
#
# Why the proxy is the correct fix:
# `asyncio.get_event_loop()` is already thread-local — `asyncio.set_event_loop`
# stores the loop in the calling thread's slot, and `get_event_loop` reads
# back the calling thread's slot. By replacing the SDK's module global with a
# proxy that delegates every attribute access to `asyncio.get_event_loop()`,
# every SDK call from thread T resolves to thread T's own loop, with no
# shared mutable state across threads. _subscribe_loop only needs to call
# `asyncio.set_event_loop(fresh_loop)` once per thread — no module-level
# patching, no lock, no race window.
#
# This patch is applied once at module import time below; threads do nothing.
class _ThreadLocalLoopProxy:
    """Drop-in replacement for the lark_oapi SDK's module-level `loop`.

    Resolves every attribute access (run_until_complete, create_task, time,
    etc.) to the calling thread's current asyncio event loop, eliminating
    the cross-thread race that caused
    `RuntimeError: Future attached to a different loop`.
    """

    def __getattr__(self, name: str):
        return getattr(asyncio.get_event_loop(), name)

    def __bool__(self) -> bool:
        # SDK uses `if self._conn is not None` — never tests truthiness on
        # `loop` directly, but be safe anyway.
        return True

    def __repr__(self) -> str:
        try:
            return f"<_ThreadLocalLoopProxy bound to {asyncio.get_event_loop()!r}>"
        except RuntimeError:
            return "<_ThreadLocalLoopProxy (no loop on this thread)>"


def _install_lark_oapi_loop_proxy() -> None:
    """Install the proxy as `lark_oapi.ws.client.loop`.

    Idempotent: if the proxy is already installed (e.g. on test reload), this
    is a no-op. Imported lazily inside the function so unit tests can monkey-
    patch the SDK before the proxy is installed.
    """
    import lark_oapi.ws.client as _ws_client_mod
    if not isinstance(_ws_client_mod.loop, _ThreadLocalLoopProxy):
        _ws_client_mod.loop = _ThreadLocalLoopProxy()


_install_lark_oapi_loop_proxy()


# L-12: characters that must not survive into a sanitised display name.
# Newlines and control bytes can smuggle fake prompt instructions into
# the rendered channel tag; tabs collide with log parsers; nulls and
# escape sequences mess with terminals and downstream serializers.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _compute_next_backoff(
    current: int,
    ran_seconds: float,
    *,
    base: int = 5,
    max_backoff: int = 120,
    healthy_threshold_seconds: int = 60,
) -> int:
    """Pick the next WS-reconnect backoff (H-1 fix).

    The previous loop had a dead `backoff = 5` line (no "ran > 60s"
    gate as its comment claimed) followed by an unconditional double,
    so backoff compounded toward the 120s cap after every disconnect
    — even after hours of healthy session.

    Now: if the session that just ended lasted at least
    ``healthy_threshold_seconds``, we treat it as a real connection
    and reset to ``base``. Otherwise we double, clamped to
    ``max_backoff``.
    """
    if ran_seconds >= healthy_threshold_seconds:
        return base
    return min(max(current, base) * 2, max_backoff)


def format_lark_error_reply(error: RunError) -> str:
    """Render an AgentRuntime failure as a Lark-friendly message.

    The sender in a Lark chat is often not the agent's owner (e.g. a
    colleague messaging a team bot). Showing them the raw developer
    error ("'agent' slot is not configured, go to Settings → Providers")
    is useless — they can't fix it. Instead we tell them what happened
    in plain language and point them at the owner.
    """
    etype = error.error_type
    if etype == "SystemDefaultUnavailable":
        return (
            "⚠️ I can't reply right now: the owner's free-quota tier is "
            "unavailable (disabled or exhausted). Please contact the "
            "bot's owner."
        )
    if etype == "LLMConfigNotConfigured":
        return (
            "⚠️ I can't reply right now: the owner hasn't finished "
            "configuring me. Please contact the bot's owner to set up "
            "an LLM provider or enable the free-quota tier in Settings."
        )
    return (
        "⚠️ I hit an internal error and can't reply to this message. "
        "Please try again in a bit, or contact the bot's owner."
    )


class LarkTrigger:
    """
    Event trigger using lark-oapi SDK WebSocket per bot.

    Each active + logged_in credential gets its own WebSocket thread.
    Events are dispatched to a shared async task queue processed by N workers.
    """

    # At least this many workers always run
    MIN_WORKERS = 3
    # Each active subscriber adds this many workers
    WORKERS_PER_SUBSCRIBER = 2
    # Never exceed this cap
    MAX_WORKERS = 50

    # Memory dedup window. The durable dedup lives in `lark_seen_messages`
    # DB table (see `LarkSeenMessageRepository`) and survives restarts;
    # this in-memory layer is a hot cache that keeps the common case off
    # the DB. 10 min is comfortably longer than any observed burst of
    # Lark re-deliveries during a single WebSocket session.
    DEDUP_TTL_SECONDS = 600

    # Startup-time filter: events whose Lark-side `create_time` is older
    # than (startup_time - HISTORY_BUFFER_MS) are replays of messages
    # sent before this process started and are dropped outright. 5 min
    # of buffer keeps "user sent a message right before restart" traffic
    # flowing, while still cutting off the hour-old-event replays an operator
    # reported.
    HISTORY_BUFFER_MS = 5 * 60 * 1000

    # Durable-dedup retention: the `lark_seen_messages` table is cleaned
    # of rows older than this many days once per trigger startup.
    DEDUP_RETENTION_DAYS = 7

    # Audit-table retention (M-7 / observability): 30 d is comfortably
    # longer than the incident-review windows we've needed in practice.
    AUDIT_RETENTION_DAYS = 30

    # M-7 per-message total timeout. `collect_run` internally idle-times
    # out in 600 s (see Bug 20), but that's per-idle-stream not total
    # wall-clock. 30 min is a generous cap for any realistic agent turn
    # and prevents a single stuck message from permanently occupying a
    # worker slot.
    PROCESS_MESSAGE_TIMEOUT_SECONDS = 1800

    # L-13: cleanup used to run once at startup only. A long-running
    # container (weeks) would let the tables grow forever. The watcher
    # re-runs cleanup when this many seconds elapse since the last run.
    CLEANUP_INTERVAL_SECONDS = 24 * 3600

    # Heartbeat cadence for the audit table — a "trigger was alive at
    # T+N min" record lets post-incident reviewers tell "silent but
    # healthy" apart from "crashed and nobody noticed". 10 min is short
    # enough to catch multi-minute outages, long enough to not flood
    # the table (6 rows/hour).
    HEARTBEAT_INTERVAL_SECONDS = 600

    def __init__(self, max_workers: int = 3):
        self._base_workers = max(max_workers, self.MIN_WORKERS)
        self._subscriber_tasks: dict[str, asyncio.Task] = {}  # app_id -> subscribe_loop task
        self._subscriber_creds: dict[str, LarkCredential] = {}  # app_id -> credential
        self._task_queue: asyncio.Queue = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._monitor_tasks: list[asyncio.Task] = []
        self.running = False
        self._cli = LarkCLIClient()  # Still used for get_user, bot info lookups
        self._loop: asyncio.AbstractEventLoop | None = None  # Set in start()
        # (agent_id, app_id) -> bot open_id, ensures each bot's echo is
        # filtered correctly. M-6 fix: keying by profile_name meant that
        # a rebind to a different app under the same profile_name would
        # reuse the old bot's open_id; the tuple key rules that out and
        # `_stop_subscriber` clears stale entries explicitly.
        self._bot_open_ids: dict[tuple[str, str], str] = {}
        # Thread-safe dedup: message_id -> timestamp
        self._seen_messages: dict[str, float] = {}
        self._seen_lock = threading.Lock()  # Protects _seen_messages
        # Set in `start()`. Kept per-instance so a test can inject a
        # controlled time without monkey-patching module state.
        self._startup_time_ms: int = 0
        # Durable dedup store, also initialised in `start()` when the db
        # handle becomes available.
        self._seen_repo: "LarkSeenMessageRepository | None" = None
        # H-5: baseline for the historic-replay filter. After a long WS
        # disconnect the WS reconnect can release backlogged events that
        # are newer than our process startup but older than our current
        # reconnect — those are still "historic" from the bot's POV and
        # must be filtered, not processed. Updated every time
        # `_subscribe_loop` starts a fresh `ws_client.start()`.
        self._last_ws_connected_monotonic: float = 0.0
        self._last_ws_connected_wallclock_ms: int = 0
        # L-13: monotonic time of the most recent dedup/audit cleanup.
        # The watcher re-triggers cleanup every CLEANUP_INTERVAL_SECONDS.
        self._last_cleanup_monotonic: float = 0.0
        # Heartbeat cadence: watcher emits one audit row every
        # HEARTBEAT_INTERVAL_SECONDS so post-incident reviewers can tell
        # the difference between "silent + healthy" and "crashed".
        self._last_heartbeat_monotonic: float = 0.0
        # Audit repo (set in start()) records every lifecycle event so
        # post-incident triage doesn't depend on container log access.
        self._audit_repo: "LarkTriggerAuditRepository | None" = None

    async def start(self, db) -> None:
        """Start workers and credential watcher."""
        self.running = True
        self._db = db
        self._loop = asyncio.get_running_loop()
        self._startup_time_ms = int(time.time() * 1000)
        self._seen_repo = LarkSeenMessageRepository(db)
        self._audit_repo = LarkTriggerAuditRepository(db)

        # Run cleanup once at startup + let the watcher re-trigger on
        # L-13's CLEANUP_INTERVAL_SECONDS cadence afterwards.
        await self._run_cleanup()

        # Start baseline workers
        self._adjust_workers(self._base_workers)

        # Start credential watcher (checks for new/changed credentials periodically)
        watcher = asyncio.create_task(self._credential_watcher())
        self._monitor_tasks.append(watcher)

        # Bring up the /healthz endpoint so operators can curl from inside
        # the container during incidents. Best-effort — trigger still runs
        # if the health server can't bind.
        from ._health_server import start_health_server
        health_task = await start_health_server(self)
        if health_task is not None:
            self._monitor_tasks.append(health_task)

        logger.info(f"LarkTrigger started: {len(self._workers)} workers, watching for credentials")

    def _desired_worker_count(self) -> int:
        """Calculate how many workers we need based on active subscribers."""
        sub_count = len(self._subscriber_tasks)
        desired = self._base_workers + sub_count * self.WORKERS_PER_SUBSCRIBER
        return min(desired, self.MAX_WORKERS)

    async def _audit(self, event_type: str, **kwargs) -> None:
        """Best-effort audit write. Silent no-op before repo is wired.

        `LarkTriggerAuditRepository.append` already swallows backend
        errors so the trigger hot path never pays for audit failures.
        """
        if self._audit_repo is None:
            return
        await self._audit_repo.append(event_type, **kwargs)

    async def _maybe_heartbeat(self) -> None:
        """Emit a heartbeat audit row every ``HEARTBEAT_INTERVAL_SECONDS``.

        The row records queue depth, worker count, subscriber count and
        the monotonic uptime of the process. Absence of heartbeats in
        the audit table for N intervals = the trigger was down / stuck.
        """
        if self._audit_repo is None:
            return
        now = time.monotonic()
        if now - self._last_heartbeat_monotonic < self.HEARTBEAT_INTERVAL_SECONDS:
            return
        self._last_heartbeat_monotonic = now
        details = {
            "queue_depth": self._task_queue.qsize(),
            "worker_count": len(self._workers),
            "subscriber_count": len(self._subscriber_tasks),
            "uptime_seconds": (
                (int(time.time() * 1000) - self._startup_time_ms) / 1000.0
                if self._startup_time_ms > 0 else 0.0
            ),
            "last_ws_connected_ms": self._last_ws_connected_wallclock_ms,
        }
        await self._audit_repo.append(EVENT_HEARTBEAT, details=details)

    async def _run_cleanup(self) -> None:
        """Purge aged rows from `lark_seen_messages` and `lark_trigger_audit`.

        Called once at startup and again from the watcher every
        ``CLEANUP_INTERVAL_SECONDS``. Failures are best-effort — the
        trigger must keep serving traffic even if hygiene fails.
        """
        self._last_cleanup_monotonic = time.monotonic()
        try:
            if self._seen_repo is not None:
                deleted = await self._seen_repo.cleanup_older_than_days(
                    self.DEDUP_RETENTION_DAYS
                )
                if deleted:
                    logger.info(
                        f"LarkTrigger: cleaned {deleted} dedup rows older than "
                        f"{self.DEDUP_RETENTION_DAYS} days"
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"LarkTrigger: dedup cleanup failed: {e}")
        try:
            if self._audit_repo is not None:
                deleted = await self._audit_repo.cleanup_older_than_days(
                    self.AUDIT_RETENTION_DAYS
                )
                if deleted:
                    logger.info(
                        f"LarkTrigger: cleaned {deleted} audit rows older than "
                        f"{self.AUDIT_RETENTION_DAYS} days"
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"LarkTrigger: audit cleanup failed: {e}")

    def _prune_dead_workers(self) -> int:
        """Drop workers whose task has finished (success or error).

        H-4: `_worker` catches per-message exceptions, but a bug in the
        outer poll (e.g. `asyncio.wait_for(queue.get())` oddity, a
        cancellation leaking out) could end the task. If we never
        prune, `_adjust_workers` stops scheduling new workers because
        it thinks the pool is already at target size — queue then
        grows unbounded with no consumer.

        Called from the watcher loop. Returns the number pruned.
        """
        alive = [w for w in self._workers if not w.done()]
        pruned = len(self._workers) - len(alive)
        if pruned:
            logger.warning(
                f"LarkTrigger: pruned {pruned} dead worker task(s); "
                f"_adjust_workers will re-create them on the next tick"
            )
        self._workers = alive
        return pruned

    def _adjust_workers(self, target: int) -> None:
        """Scale workers up or down to match target count."""
        current = len(self._workers)
        if target > current:
            for i in range(current, target):
                worker = asyncio.ensure_future(self._worker(i))
                self._workers.append(worker)
            logger.info(f"LarkTrigger: scaled workers {current} -> {target}")
        elif target < current:
            # Cancel excess workers (they will finish current task first)
            excess = self._workers[target:]
            for task in excess:
                task.cancel()
            self._workers = self._workers[:target]
            logger.info(f"LarkTrigger: scaled workers {current} -> {target}")

    async def _credential_watcher(self, poll_interval: int = 10) -> None:
        """
        Periodically check for new credentials and start/stop subscribers.
        This allows users to bind a bot without restarting the service.
        Also stops subscribers whose credentials are no longer active.
        """
        idle_logged = False
        while self.running:
            try:
                mgr = LarkCredentialManager(self._db)
                creds = await mgr.get_active_credentials()

                # When no bots are bound, reduce log noise and poll less often
                if not creds and not self._subscriber_tasks:
                    if not idle_logged:
                        logger.info("LarkTrigger: no Lark bots bound, watching for new bindings...")
                        idle_logged = True
                    await asyncio.sleep(30)
                    continue
                idle_logged = False

                # Deduplicate by app_id
                seen_apps: dict[str, LarkCredential] = {}
                for cred in creds:
                    if cred.app_id not in seen_apps:
                        seen_apps[cred.app_id] = cred

                current_app_ids = set(seen_apps.keys())
                running_app_ids = set(self._subscriber_tasks.keys())

                # Stop subscribers for deactivated credentials
                for app_id in running_app_ids - current_app_ids:
                    await self._stop_subscriber(app_id)

                # Clean up dead subscriber tasks (crashed and not restarting)
                dead_apps = [
                    app_id for app_id, task in self._subscriber_tasks.items()
                    if task.done()
                ]
                for app_id in dead_apps:
                    logger.warning(f"LarkTrigger: subscriber for {app_id} died, removing")
                    self._subscriber_tasks.pop(app_id, None)
                    self._subscriber_creds.pop(app_id, None)

                # Start subscribers for new app_ids (including ones that just died)
                for app_id, cred in seen_apps.items():
                    if app_id not in self._subscriber_tasks:
                        # Validate: must have decryptable secret for SDK
                        app_secret = cred.get_app_secret()
                        if app_secret:
                            task = asyncio.create_task(self._subscribe_loop(cred))
                            self._subscriber_tasks[app_id] = task
                            self._subscriber_creds[app_id] = cred
                            logger.info(f"LarkTrigger: started SDK subscriber for {cred.profile_name}")
                            await self._audit(
                                EVENT_SUBSCRIBER_STARTED,
                                agent_id=cred.agent_id,
                                app_id=cred.app_id,
                                details={
                                    "profile_name": cred.profile_name,
                                    "brand": cred.brand,
                                },
                            )
                        else:
                            # Two possible causes — point the user at the right fix
                            if cred.workspace_path:
                                logger.info(
                                    f"LarkTrigger: {cred.profile_name} pending "
                                    f"lark_enable_receive (agent-assisted setup has "
                                    f"no plain App Secret yet; bot can send but "
                                    f"real-time receive stays off until user pastes "
                                    f"the secret)."
                                )
                            else:
                                logger.warning(
                                    f"LarkTrigger: {cred.profile_name} has no "
                                    f"plain App Secret in DB — re-bind via frontend "
                                    f"LarkConfig panel to fix."
                                )

                # Replace any worker tasks that died (H-4) before computing
                # the desired size; otherwise a dead-but-still-counted worker
                # keeps the pool at target and no fresh worker is scheduled.
                self._prune_dead_workers()
                self._adjust_workers(self._desired_worker_count())

                # L-13: periodic retention cleanup. Runs once a day to
                # bound table growth on long-lived containers.
                if (
                    time.monotonic() - self._last_cleanup_monotonic
                    >= self.CLEANUP_INTERVAL_SECONDS
                ):
                    await self._run_cleanup()

                # Heartbeat + audit snapshot every ~10 min so a silent
                # process is distinguishable from a healthy one when a
                # user looks at the audit table after the fact.
                await self._maybe_heartbeat()

            except Exception as e:
                logger.warning(f"LarkTrigger credential watcher error: {e}")

            await asyncio.sleep(poll_interval)

    async def _stop_subscriber(self, app_id: str) -> None:
        """Stop a running subscriber by app_id."""
        cred = self._subscriber_creds.pop(app_id, None)
        profile = cred.profile_name if cred else app_id

        # Cancel the subscribe_loop task (interrupts asyncio.to_thread)
        task = self._subscriber_tasks.pop(app_id, None)
        if task and not task.done():
            task.cancel()

        # M-6: clear the bot_open_id cache for this cred so a later
        # rebind of the same agent to a different app doesn't reuse
        # stale identity.
        if cred is not None:
            self._bot_open_ids.pop((cred.agent_id, cred.app_id), None)

        logger.info(f"LarkTrigger: stopped subscriber for {profile} (app_id={app_id})")
        await self._audit(
            EVENT_SUBSCRIBER_STOPPED,
            agent_id=cred.agent_id if cred else "",
            app_id=app_id,
            details={"profile_name": profile},
        )

    async def _subscribe_loop(self, cred: LarkCredential) -> None:
        """
        Run SDK WebSocket subscription for one bot. Restart on failure with backoff.

        The SDK's ws.Client.start() internally runs its own asyncio event loop,
        so it must run in a separate thread with NO existing event loop.
        We use threading.Thread (not asyncio.to_thread) to ensure a clean thread
        without an inherited event loop.

        Re-reads the credential from DB at each iteration so that if the user
        corrects a wrong App Secret via `lark_enable_receive` (or updates via
        re-bind), the next retry picks up the fresh value instead of looping
        forever against stale state.
        """
        import lark_oapi as lark

        agent_id = cred.agent_id
        app_id_initial = cred.app_id
        backoff = 5
        max_backoff = 120
        ws_start_monotonic: float = 0.0

        while self.running:
            # Refresh the credential from DB each iteration
            fresh_cred = await LarkCredentialManager(self._db).get_credential(agent_id)
            if not fresh_cred or not fresh_cred.is_active:
                logger.info(
                    f"LarkTrigger: credential gone or inactive for {agent_id}, "
                    f"exiting subscriber"
                )
                return
            if fresh_cred.app_id != app_id_initial:
                logger.info(
                    f"LarkTrigger: app_id changed for {agent_id} "
                    f"({app_id_initial} -> {fresh_cred.app_id}); exiting so the "
                    f"watcher can start a fresh subscriber"
                )
                return
            app_secret = fresh_cred.get_app_secret()
            if not app_secret:
                logger.warning(
                    f"LarkTrigger: App Secret cleared for {fresh_cred.profile_name}; "
                    f"exiting subscriber"
                )
                return
            cred = fresh_cred  # use fresh cred throughout this iteration

            try:
                # SDK callback: runs in SDK's thread. Instead of doing the
                # dedup inline (which needs to await the DB layer for
                # durable checks), we hand the event off to an async
                # coroutine on the main loop; that coroutine runs
                # `_should_process_event` (memory hot cache + startup
                # filter + DB persistence) and only enqueues the event
                # for workers when the checks clear it.
                def on_message(data):
                    try:
                        event_dict = self._sdk_event_to_dict(data)
                        if not event_dict:
                            return
                        asyncio.run_coroutine_threadsafe(
                            self._dedup_and_enqueue(cred, event_dict),
                            self._loop,
                        )
                    except Exception as e:
                        logger.warning(f"LarkTrigger SDK callback error: {e}")

                handler = lark.EventDispatcherHandler.builder("", "") \
                    .register_p2_im_message_receive_v1(on_message) \
                    .build()

                domain = lark.LARK_DOMAIN if cred.brand == "lark" else lark.FEISHU_DOMAIN
                # `auto_reconnect=False` (H-6 fix, 2026-04-27): the SDK's
                # internal `_reconnect()` does not re-patch
                # `lark_oapi.ws.client.loop` after a keepalive timeout, so the
                # second connection's futures get bound to a different loop
                # than the `_receive_message_loop` task. The result is an
                # endless `RuntimeError: ... attached to a different loop`
                # caught silently inside the SDK — `ws_client.start()` never
                # returns, the daemon thread stays alive, and the bot stops
                # delivering messages without any audit signal. Letting the
                # SDK raise on first disconnect lets the outer `while
                # self.running` loop here own the reconnect (with backoff +
                # fresh credentials + audit rows) — exactly what M-9 already
                # built infrastructure for.
                ws_client = lark.ws.Client(
                    app_id=cred.app_id,
                    app_secret=app_secret,
                    event_handler=handler,
                    domain=domain,
                    auto_reconnect=False,
                )

                logger.info(f"LarkTrigger: connecting SDK WebSocket for {cred.profile_name}")

                # Run start() in a daemon thread with its own event loop
                thread_error = []

                def run_ws():
                    try:
                        # H-6 (2026-04-27): module-level proxy installed at
                        # `lark_oapi.ws.client.loop` (see top of file) makes
                        # every SDK access of `loop` resolve to the calling
                        # thread's current asyncio loop. So all this thread
                        # has to do is set its own current loop — no module-
                        # level patch, no shared lock, no race.
                        fresh_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(fresh_loop)
                        # Recreate ws_client._lock under the fresh loop. The
                        # Client.__init__ call site ran on the main asyncio
                        # loop, and even though Python 3.10+ Locks bind
                        # lazily, rebuilding here keeps the invariant simple:
                        # every asyncio primitive owned by this Client is
                        # bound to fresh_loop, the loop that will drive it.
                        ws_client._lock = asyncio.Lock()
                        ws_client.start()
                    except Exception as e:
                        thread_error.append(e)

                t = threading.Thread(target=run_ws, daemon=True)
                ws_start_monotonic = time.monotonic()
                t.start()

                # Note the moment the WS is considered "up" from our POV —
                # H-5 uses this as the baseline for the historic-replay
                # filter, so a long disconnect followed by reconnect won't
                # silently let Lark's backlog of old events through.
                self._last_ws_connected_monotonic = ws_start_monotonic
                self._last_ws_connected_wallclock_ms = int(time.time() * 1000)
                await self._audit(
                    EVENT_WS_CONNECTED,
                    agent_id=cred.agent_id,
                    app_id=cred.app_id,
                    details={"profile_name": cred.profile_name, "brand": cred.brand},
                )

                # Wait for thread to finish (poll so we can check self.running)
                while t.is_alive() and self.running:
                    await asyncio.sleep(1)

                if thread_error:
                    raise thread_error[0]

                ran_seconds = time.monotonic() - ws_start_monotonic
                if not t.is_alive():
                    backoff = _compute_next_backoff(
                        current=backoff, ran_seconds=ran_seconds,
                        max_backoff=max_backoff,
                    )
                    logger.warning(
                        f"LarkTrigger SDK WebSocket disconnected for {cred.profile_name} "
                        f"after {ran_seconds:.1f}s; restarting in {backoff}s"
                    )
                    await self._audit(
                        EVENT_WS_DISCONNECTED,
                        agent_id=cred.agent_id,
                        app_id=cred.app_id,
                        details={
                            "ran_seconds": ran_seconds,
                            "next_backoff_seconds": backoff,
                        },
                    )
            except asyncio.CancelledError:
                logger.info(f"LarkTrigger: subscriber cancelled for {cred.profile_name}")
                return
            except Exception as e:
                ran_seconds = (
                    time.monotonic() - ws_start_monotonic
                    if ws_start_monotonic > 0 else 0.0
                )
                backoff = _compute_next_backoff(
                    current=backoff, ran_seconds=ran_seconds,
                    max_backoff=max_backoff,
                )
                logger.exception(
                    f"LarkTrigger SDK error for {cred.profile_name} "
                    f"after {ran_seconds:.1f}s (next backoff {backoff}s): {e}"
                )
                await self._audit(
                    EVENT_WS_DISCONNECTED,
                    agent_id=cred.agent_id,
                    app_id=cred.app_id,
                    details={
                        "ran_seconds": ran_seconds,
                        "next_backoff_seconds": backoff,
                        "error": f"{type(e).__name__}: {e}",
                    },
                )

            if not self.running:
                break

            await self._audit(
                EVENT_WS_BACKOFF,
                agent_id=cred.agent_id,
                app_id=cred.app_id,
                details={"sleep_seconds": backoff},
            )
            await asyncio.sleep(backoff)

    async def _dedup_and_enqueue(self, cred, event_dict: dict) -> None:
        """Check dedup; enqueue only if this is a genuinely new event.

        Emits a structured "who sent what to whom" INFO log at entry, BEFORE
        the dedup decision, so operators can correlate a user's "I sent X but
        agent never replied" report against what actually hit the bot (even
        when the event is subsequently dropped as a replay / duplicate).
        Audit rows carry the same enriched payload for post-incident review.
        """
        msg_id = event_dict.get("message_id", "")
        chat_id = event_dict.get("chat_id", "")
        chat_type = event_dict.get("chat_type", "")
        sender_id = event_dict.get("sender_id", "")
        message_type = event_dict.get("message_type", "")
        content_preview = self._preview_message_content(
            event_dict.get("content", ""), message_type
        )

        logger.info(
            "LarkTrigger ingress | agent={agent} app={app} <- from={sender} "
            "chat={chat}({chat_type}) msg_id={msg_id} type={msg_type} preview={preview!r}",
            agent=cred.agent_id,
            app=cred.app_id,
            sender=sender_id or "<unknown>",
            chat=chat_id or "<unknown>",
            chat_type=chat_type or "<unknown>",
            msg_id=msg_id or "<unknown>",
            msg_type=message_type or "<unknown>",
            preview=content_preview,
        )

        ingress_details = {
            "message_type": message_type,
            "chat_type": chat_type,
            "content_preview": content_preview,
        }

        decision = await self._check_and_classify_event(event_dict)
        if decision["accept"]:
            await self._audit(
                EVENT_INGRESS_PROCESSED,
                message_id=msg_id,
                agent_id=cred.agent_id,
                app_id=cred.app_id,
                chat_id=chat_id,
                sender_id=sender_id,
                details={"dedup_layer": decision["layer"], **ingress_details},
            )
            if decision["layer"] == "db_fail_open":
                # Fail-open traversed the DB layer but the DB rejected
                # us; record a separate audit row so reviewers can spot
                # DB-driven double-processing after the fact.
                await self._audit(
                    EVENT_DEDUP_FAIL_OPEN,
                    message_id=msg_id,
                    agent_id=cred.agent_id,
                    app_id=cred.app_id,
                    details={"error": decision.get("error", "")},
                )
            await self._task_queue.put((cred, event_dict))
        else:
            event_name = {
                "historic": EVENT_INGRESS_DROPPED_HISTORIC,
                "memory_dedup": EVENT_INGRESS_DROPPED_DEDUP,
                "db_dedup": EVENT_INGRESS_DROPPED_DEDUP,
            }.get(decision["layer"], EVENT_INGRESS_DROPPED_DEDUP)
            await self._audit(
                event_name,
                message_id=msg_id,
                agent_id=cred.agent_id,
                app_id=cred.app_id,
                chat_id=chat_id,
                sender_id=sender_id,
                details={"layer": decision["layer"], **ingress_details},
            )
            logger.info(
                f"LarkTrigger: dedup skipping message_id={msg_id!r} "
                f"(layer={decision['layer']})"
            )

    @staticmethod
    def _preview_message_content(raw_content: str, message_type: str) -> str:
        """
        Render a short, log-safe preview of a Lark message payload.

        Lark stores message content as a JSON-encoded string whose shape
        depends on `message_type` (text → {"text": "..."}, post → rich
        segments, file → {"file_key": "...", "file_name": "..."}, etc.).
        For observability we want the human-readable gist, not the raw
        envelope. We pull the most-useful textual field per type, strip
        newlines, and cap at 160 chars so audit rows and logs stay scannable.
        """
        if not raw_content:
            return ""
        try:
            payload = json.loads(raw_content)
        except Exception:
            payload = None

        text = ""
        if isinstance(payload, dict):
            if message_type == "text":
                text = payload.get("text", "") or ""
            elif message_type == "post":
                # Post payloads nest {"zh_cn": {"title": "...", "content": [[...]]}}
                for lang_block in payload.values():
                    if isinstance(lang_block, dict):
                        title = lang_block.get("title", "") or ""
                        body_bits = []
                        for line in lang_block.get("content", []) or []:
                            if isinstance(line, list):
                                for seg in line:
                                    if isinstance(seg, dict):
                                        body_bits.append(seg.get("text", "") or "")
                        text = (title + " " + " ".join(body_bits)).strip()
                        if text:
                            break
            elif message_type in ("file", "image", "audio", "media"):
                text = (
                    payload.get("file_name")
                    or payload.get("image_key")
                    or payload.get("file_key")
                    or ""
                )
            else:
                # Fallback: take the first string-valued field we find so
                # unknown types still leave a human-useful breadcrumb.
                for v in payload.values():
                    if isinstance(v, str) and v:
                        text = v
                        break
        if not text:
            text = raw_content

        flattened = " ".join(text.split())
        return flattened[:160]

    async def _should_process_event(self, event_dict: dict) -> bool:
        """Compat shim over ``_check_and_classify_event``.

        Keeps the simple True/False contract that callers / tests use;
        the richer dict is consumed by ``_dedup_and_enqueue`` for audit.
        """
        decision = await self._check_and_classify_event(event_dict)
        return decision["accept"]

    async def _check_and_classify_event(self, event_dict: dict) -> dict:
        """
        Classify an incoming event as ``process`` / ``drop`` and record
        WHICH layer decided, so the audit log can tell a reviewer
        exactly why any given message survived or was rejected.

        Three layers, cheapest-first:

          1. Historic-replay filter (O(1), no I/O) — events whose
             ``create_time`` is older than ``baseline - HISTORY_BUFFER_MS``
             are replays from before the current WS session. Baseline =
             ``max(startup_time, last_ws_connected)`` (H-5 fix).

          2. In-memory hot cache (O(1) with lock) — TTL-bounded.

          3. Durable DB gate (one round-trip, atomic). Survives process
             restarts via ``LarkSeenMessageRepository.mark_seen``.

        Fail-open on backend I/O error: layer=``db_fail_open`` is
        recorded so post-incident reviewers can spot DB-driven
        double-processing.
        """
        msg_id = event_dict.get("message_id", "")

        # Layer 1: historic-replay filter. Applies only when the Lark event
        # carries a create_time we can compare; if we can't tell the age
        # of the event, fall through to the other layers.
        #
        # Baseline is the MAX of process-startup and last WS-reconnect
        # (H-5 fix). A long WS disconnect followed by reconnect can
        # release Lark's server-side backlog — events that were created
        # AFTER process startup but BEFORE the current WS session should
        # still be treated as historic replays, not fresh traffic. Using
        # only startup_time here meant those backlog bursts slipped
        # through all layers (Layer 2 memory TTL is only 10 min), and
        # the user saw "agent replies to 5 old messages an hour later".
        baseline_ms = max(
            self._startup_time_ms,
            self._last_ws_connected_wallclock_ms,
        )
        create_time_raw = event_dict.get("create_time", "")
        if create_time_raw and baseline_ms > 0:
            try:
                create_time_ms = int(create_time_raw)
                cutoff = baseline_ms - self.HISTORY_BUFFER_MS
                if create_time_ms < cutoff:
                    age_min = (baseline_ms - create_time_ms) / 60000.0
                    logger.info(
                        f"LarkTrigger: dropping historic event {msg_id!r} "
                        f"(created {age_min:.1f} min before baseline, past "
                        f"{self.HISTORY_BUFFER_MS / 60000:.0f} min buffer)"
                    )
                    return {"accept": False, "layer": "historic",
                            "age_min": age_min}
            except (ValueError, TypeError):
                # Non-numeric create_time — fall through to other layers.
                pass

        if not msg_id:
            # No id → can't dedup; process defensively. Lark's SDK should
            # always populate this, so this is belt-and-braces.
            return {"accept": True, "layer": "no_msg_id"}

        # Layer 2: in-memory hot cache.
        now = time.time()
        with self._seen_lock:
            if msg_id in self._seen_messages:
                return {"accept": False, "layer": "memory_dedup"}
            self._seen_messages[msg_id] = now
            cutoff = now - self.DEDUP_TTL_SECONDS
            self._seen_messages = {
                k: v for k, v in self._seen_messages.items() if v > cutoff
            }

        # Layer 3: durable DB gate. Skipped only when no repo is wired —
        # tests may run without one.
        if self._seen_repo is not None:
            try:
                newly_inserted = await self._seen_repo.mark_seen(msg_id)
                return {
                    "accept": bool(newly_inserted),
                    "layer": "db_new" if newly_inserted else "db_dedup",
                }
            except Exception as e:  # noqa: BLE001 — fail-open on I/O
                logger.warning(
                    f"LarkTrigger: DB dedup check failed for {msg_id}: "
                    f"{type(e).__name__}: {e}; processing anyway"
                )
                return {
                    "accept": True,
                    "layer": "db_fail_open",
                    "error": f"{type(e).__name__}: {e}",
                }
        return {"accept": True, "layer": "no_repo"}

    @staticmethod
    def _sdk_event_to_dict(data) -> dict:
        """
        Convert lark-oapi P2ImMessageReceiveV1 event to the flat dict format
        that _process_message expects.
        """
        try:
            event = data.event
            sender = event.sender
            message = event.message

            return {
                "type": "im.message.receive_v1",
                "chat_id": message.chat_id or "",
                "chat_type": message.chat_type or "p2p",
                "message_id": message.message_id or "",
                "sender_id": sender.sender_id.open_id if sender and sender.sender_id else "",
                "sender_type": sender.sender_type or "" if sender else "",
                "content": message.content or "",
                "message_type": message.message_type or "text",
                "create_time": message.create_time or "",
            }
        except Exception as e:
            logger.warning(f"LarkTrigger: failed to convert SDK event: {e}")
            return {}

    async def _worker(self, worker_id: int) -> None:
        """Process events from the shared queue."""
        while self.running:
            try:
                cred, event = await asyncio.wait_for(
                    self._task_queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                continue

            # M-7: cap the wall-clock any one message can consume so a
            # stuck LLM / tool call cannot permanently occupy this worker.
            # `collect_run` has its own idle timeout (~10 min) but that
            # gates only stream silence, not total run time.
            try:
                await asyncio.wait_for(
                    self._process_message(cred, event, worker_id),
                    timeout=self.PROCESS_MESSAGE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                message_id = (
                    event.get("message_id", "")
                    if isinstance(event, dict) else ""
                )
                logger.exception(
                    f"LarkTrigger worker {worker_id} message {message_id!r} "
                    f"exceeded {self.PROCESS_MESSAGE_TIMEOUT_SECONDS}s "
                    f"— cancelling"
                )
                await self._audit(
                    EVENT_WORKER_TIMEOUT,
                    message_id=message_id,
                    agent_id=getattr(cred, "agent_id", ""),
                    app_id=getattr(cred, "app_id", ""),
                    chat_id=event.get("chat_id", "") if isinstance(event, dict) else "",
                    details={
                        "worker_id": worker_id,
                        "timeout_seconds": self.PROCESS_MESSAGE_TIMEOUT_SECONDS,
                    },
                )
            except Exception as e:
                logger.exception(
                    f"LarkTrigger worker {worker_id} error: {e}",
                    exc_info=True,
                )
                message_id = (
                    event.get("message_id", "")
                    if isinstance(event, dict) else ""
                )
                await self._audit(
                    EVENT_WORKER_ERROR,
                    message_id=message_id,
                    agent_id=getattr(cred, "agent_id", ""),
                    app_id=getattr(cred, "app_id", ""),
                    chat_id=event.get("chat_id", "") if isinstance(event, dict) else "",
                    details={
                        "worker_id": worker_id,
                        "error": f"{type(e).__name__}: {e}",
                    },
                )

    # ------------------------------------------------------------------
    # Message processing — split into focused helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_event_fields(event: dict) -> dict:
        """Extract normalized fields from either compact or raw event format."""
        if "message" in event and isinstance(event["message"], dict):
            message = event.get("event", event).get("message", {})
            sender = event.get("event", event).get("sender", {})
            return {
                "chat_id": message.get("chat_id", ""),
                "sender_id": sender.get("sender_id", {}).get("open_id", sender.get("open_id", "")),
                "sender_name": sender.get("sender_id", {}).get("name", sender.get("name", "Unknown")),
                "content_str": message.get("content", "{}"),
                "message_id": message.get("message_id", ""),
            }
        return {
            "chat_id": event.get("chat_id", ""),
            "sender_id": event.get("sender_id", ""),
            "sender_name": event.get("sender_name", "Unknown"),
            "content_str": event.get("content", ""),
            "message_id": event.get("message_id", event.get("id", "")),
        }

    async def _is_echo(self, cred: LarkCredential, event: dict, sender_id: str) -> bool:
        """Check if message was sent by the bot itself (prevents echo loops).

        Two-layer defence:
          1. Raw SDK event sender_type == bot|app — cheap, always tried.
          2. open_id equality against this bot's cached open_id — requires
             an API lookup which is cached per (agent_id, app_id).
        """
        sender_type = event.get("sender_type", "")
        if sender_type in ("bot", "app"):
            return True
        # Lazy-load bot open_id. Key is (agent_id, app_id) — same agent
        # rebound to a different app must NOT reuse old identity (M-6).
        cache_key = (cred.agent_id, cred.app_id)
        if cache_key not in self._bot_open_ids:
            try:
                bot_info = await self._cli._run_with_agent_id(
                    ["api", "GET", "/open-apis/bot/v3/info"],
                    cred.agent_id,
                )
                if bot_info.get("success"):
                    bot_oid = bot_info.get("data", {}).get("bot", {}).get("open_id", "")
                    if bot_oid:
                        self._bot_open_ids[cache_key] = bot_oid
            except Exception:
                logger.debug(f"Failed to fetch bot open_id for {cred.profile_name}")
        bot_oid = self._bot_open_ids.get(cache_key, "")
        return bool(bot_oid and sender_id == bot_oid)

    async def _resolve_sender_name(self, agent_id: str, sender_id: str) -> str:
        """Resolve a Lark user's display name from their open_id."""
        try:
            user_info = await self._cli.get_user(agent_id, user_id=sender_id)
            if user_info.get("success"):
                outer = user_info.get("data", {})
                inner = outer.get("data", outer)
                user_obj = inner.get("user", inner)
                return (
                    user_obj.get("name")
                    or user_obj.get("en_name")
                    or user_obj.get("email", "").split("@")[0].replace(".", " ").title()
                    or "Unknown"
                )
        except Exception:
            logger.debug(f"Failed to resolve sender name for {sender_id}")
        return "Unknown"

    @staticmethod
    def _parse_content(content_str: str) -> str:
        """Parse message content (may be JSON-encoded or plain text)."""
        text = content_str
        if text.startswith("{"):
            try:
                text = json.loads(text).get("text", text)
            except (json.JSONDecodeError, TypeError):
                pass
        return text.strip()

    @staticmethod
    def _sanitize_display_name(name: str) -> str:
        """Truncate + strip control characters from a display name.

        Lark nicknames are user-controlled strings. Embedding raw ones
        into the prompt via ChannelTag opens a prompt-injection seam
        (newlines + fake 'SYSTEM:' prefixes). We:
          1. Replace all C0/C1 control characters (incl. \\r \\n \\t
             \\x00 ESC) with a single space.
          2. Collapse whitespace runs.
          3. Truncate to 128 chars for safe DB storage.
        """
        if not name:
            return "Unknown"
        cleaned = _CONTROL_CHARS_RE.sub(" ", name)
        cleaned = " ".join(cleaned.split())
        return cleaned[:128] or "Unknown"

    async def _process_message(
        self, cred: LarkCredential, event: dict, worker_id: int
    ) -> None:
        """Process a single incoming message event."""
        # H-2: cred gatekeeper. The SDK daemon thread keeps running even
        # after we cancel its subscribe_loop task (no portable way to
        # stop `ws_client.start()` from outside), so events from a bot
        # that has been unbound can still reach the queue. Reject them
        # here before running the agent.
        if cred.app_id not in self._subscriber_creds:
            msg_id_unbound = (
                event.get('message_id', '') if isinstance(event, dict) else ''
            )
            logger.info(
                f"LarkTrigger worker {worker_id}: dropping event from "
                f"unbound credential (agent_id={cred.agent_id}, "
                f"app_id={cred.app_id}); msg_id={msg_id_unbound!r}"
            )
            await self._audit(
                EVENT_INGRESS_DROPPED_UNBOUND,
                message_id=msg_id_unbound,
                agent_id=cred.agent_id,
                app_id=cred.app_id,
                details={"worker_id": worker_id},
            )
            return

        fields = self._parse_event_fields(event)
        chat_id = fields["chat_id"]
        sender_id = fields["sender_id"]
        sender_name = fields["sender_name"]
        message_id = fields["message_id"]

        # Filter bot echoes
        if await self._is_echo(cred, event, sender_id):
            await self._audit(
                EVENT_INGRESS_DROPPED_ECHO,
                message_id=message_id,
                agent_id=cred.agent_id,
                app_id=cred.app_id,
                chat_id=chat_id,
                sender_id=sender_id,
            )
            return

        # Parse content
        text = self._parse_content(fields["content_str"])
        if not text:
            return

        # Resolve sender name if unknown
        if sender_name == "Unknown" and sender_id:
            sender_name = await self._resolve_sender_name(cred.agent_id, sender_id)

        # Sanitize for safe storage
        sender_name = self._sanitize_display_name(sender_name)

        logger.info(
            f"LarkTrigger [{cred.profile_name}] message from {sender_name} ({sender_id}): "
            f"{text[:100]}"
        )

        # Build context and run agent
        output_text = await self._build_and_run_agent(
            cred, event, chat_id, sender_id, sender_name, text, message_id
        )

        # Write to Inbox
        await self._write_to_inbox(
            cred=cred,
            sender_name=sender_name,
            sender_id=sender_id,
            original_message=text,
            agent_response=output_text,
            chat_id=chat_id,
        )

    async def _build_and_run_agent(
        self,
        cred: LarkCredential,
        event: dict,
        chat_id: str,
        sender_id: str,
        sender_name: str,
        text: str,
        message_id: str,
    ) -> str:
        """Build context, run AgentRuntime, and return the output text."""
        normalized_event = {
            "chat_id": chat_id,
            "chat_type": event.get("chat_type", "p2p"),
            "chat_name": event.get("chat_name", ""),
            "sender_id": sender_id,
            "sender_name": sender_name,
            "content": text,
            "message_id": message_id,
            "create_time": event.get("create_time", ""),
        }

        builder = LarkContextBuilder(
            event=normalized_event, credential=cred,
            cli=self._cli, agent_id=cred.agent_id,
        )
        history_config = ChannelHistoryConfig(
            load_conversation_history=True, history_limit=20, history_max_chars=3000,
        )
        prompt = await builder.build_prompt(history_config)

        channel_tag = ChannelTag.lark(
            sender_name=sender_name, sender_id=sender_id,
            chat_id=chat_id, chat_name=normalized_event.get("chat_name", ""),
        )
        tagged_prompt = f"{channel_tag.format()}\n{prompt}"

        # Resolve the AGENT'S OWNER (NarraNexus user_id) — NOT the Lark
        # sender's open_id. sender_id is a Lark-internal identifier that
        # ProviderResolver can't map to an API key; using it meant every
        # Lark-triggered run silently fell back to the system default
        # provider instead of the owner's configured one. JobTrigger and
        # MessageBusTrigger already use NarraNexus user_id correctly; we
        # bring Lark in line.
        agent_row = await self._db.get_one("agents", {"agent_id": cred.agent_id})
        owner_user_id = (agent_row or {}).get("created_by", "") or cred.agent_id

        runtime = AgentRuntime()
        result = await collect_run(
            runtime,
            agent_id=cred.agent_id,
            user_id=owner_user_id,
            input_content=tagged_prompt,
            working_source=WorkingSource.LARK,
            trigger_extra_data={
                "channel_tag": channel_tag.to_dict(),
                "trigger_id": f"lark_{message_id}" if message_id else "lark_unknown",
            },
        )

        # Error path (Bug 2): the old loop ignored MessageType.ERROR so
        # the sender saw radio silence. Surface a friendly IM message so
        # they know the bot got their text but can't act on it, and
        # return the same text so the inbox row reflects reality.
        if result.is_error:
            friendly = format_lark_error_reply(result.error)
            logger.warning(
                f"LarkTrigger [{cred.profile_name}] runtime error "
                f"({result.error.error_type}): {result.error.error_message}"
            )
            try:
                await self._cli.send_message(
                    cred.agent_id, chat_id=chat_id, text=friendly
                )
            except Exception as send_err:
                logger.warning(
                    f"LarkTrigger [{cred.profile_name}] failed to deliver "
                    f"error reply to Lark: {send_err}"
                )
            return friendly

        # Happy path: extract the text the agent itself sent via
        # `lark_cli im +messages-send` from the tool_call raw payloads.
        lark_replies: list[str] = []
        for raw in result.raw_items:
            if isinstance(raw, dict):
                item = raw.get("item", {})
                if item.get("type") == "tool_call_item":
                    sent_text = self._extract_lark_reply(item)
                    if sent_text:
                        lark_replies.append(sent_text)

        if lark_replies:
            output_text = "\n".join(lark_replies)
        elif result.output_text.strip():
            output_text = "(Replied on Lark)"
        else:
            output_text = ""

        logger.info(
            f"LarkTrigger [{cred.profile_name}] agent responded: {output_text[:200]}"
        )
        return output_text

    @staticmethod
    def _extract_lark_reply(item: dict) -> str:
        """Extract sent text from a lark_cli tool call item.

        Expects tool_name="lark_cli" with command containing +messages-send
        or +messages-reply. Returns the value of --text or --markdown.
        """
        tool_name = item.get("tool_name", "")
        args = item.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if not isinstance(args, dict):
            return ""

        if "lark_cli" not in tool_name:
            return ""

        command = args.get("command", "")
        if "+messages-send" not in command and "+messages-reply" not in command:
            return ""

        import shlex
        try:
            parts = shlex.split(command)
        except ValueError:
            parts = command.split()
        for i, part in enumerate(parts):
            if part == "--text" and i + 1 < len(parts):
                return parts[i + 1]
            if part == "--markdown" and i + 1 < len(parts):
                return parts[i + 1]
        # Couldn't parse text but it IS a send command
        return "(sent via lark_cli)"

    # ------------------------------------------------------------------
    # Inbox writing
    # ------------------------------------------------------------------

    async def _write_to_inbox(
        self,
        cred: LarkCredential,
        sender_name: str,
        sender_id: str,
        original_message: str,
        agent_response: str,
        chat_id: str,
    ) -> None:
        """Write Lark messages to MessageBus tables for Inbox display."""
        try:
            db = await get_db_client()
            now = utc_now()
            brand_display = "Lark" if cred.brand == "lark" else "Feishu"

            # sender_name already resolved by caller — no duplicate lookup needed
            channel_id = f"lark_{chat_id}"
            display_name = sender_name if sender_name != "Unknown" else sender_id
            channel_name = f"{brand_display}: {display_name}"

            await self._ensure_inbox_entities(
                db, cred, sender_id, sender_name, display_name,
                brand_display, channel_id, channel_name, now,
            )

            # Write incoming message
            await db.insert("bus_messages", {
                "message_id": f"lark_in_{uuid.uuid4().hex[:12]}",
                "channel_id": channel_id,
                "from_agent": f"lark_user_{sender_id}",
                "content": original_message,
                "msg_type": "text",
                "created_at": now,
            })

            # Write agent response summary — persist the actual reply so the
            # Inbox UI shows what was sent, not a placeholder stub.
            if agent_response and agent_response.strip():
                await db.insert("bus_messages", {
                    "message_id": f"lark_out_{uuid.uuid4().hex[:12]}",
                    "channel_id": channel_id,
                    "from_agent": cred.agent_id,
                    "content": agent_response,
                    "msg_type": "text",
                    "created_at": now,
                })

            logger.info(f"Wrote Lark messages to inbox channel {channel_id}")
        except Exception as e:
            logger.warning(f"Failed to write to inbox: {e}")
            # M-10: preserve a record of the lost inbox row in the audit
            # table so the content isn't silently gone forever. Operators
            # can replay / inspect it after the fact.
            await self._audit(
                EVENT_INBOX_WRITE_FAILED,
                agent_id=cred.agent_id,
                app_id=cred.app_id,
                chat_id=chat_id,
                sender_id=sender_id,
                details={
                    "error": f"{type(e).__name__}: {e}",
                    "sender_name": sender_name,
                    "original_message": original_message[:500],
                    "agent_response": (agent_response or "")[:500],
                },
            )

    @staticmethod
    async def _ensure_inbox_entities(
        db, cred: LarkCredential, sender_id: str, sender_name: str,
        display_name: str, brand_display: str, channel_id: str,
        channel_name: str, now: str,
    ) -> None:
        """Ensure pseudo-agent, channel, and membership exist in inbox tables."""
        lark_agent_id = f"lark_user_{sender_id}"
        existing_agent = await db.get_one("bus_agent_registry", {"agent_id": lark_agent_id})
        if not existing_agent:
            await db.insert("bus_agent_registry", {
                "agent_id": lark_agent_id,
                "owner_user_id": "",
                "capabilities": f"{brand_display} user",
                "description": display_name,
                "visibility": "public",
                "registered_at": now,
            })
        elif sender_name != "Unknown" and existing_agent.get("description") != sender_name:
            await db.update("bus_agent_registry",
                {"agent_id": lark_agent_id},
                {"description": sender_name})

        existing_channel = await db.get_one("bus_channels", {"channel_id": channel_id})
        if not existing_channel:
            await db.insert("bus_channels", {
                "channel_id": channel_id,
                "name": channel_name,
                "channel_type": "direct",
                "created_by": cred.agent_id,
                "created_at": now,
            })

        existing_member = await db.get_one("bus_channel_members", {
            "channel_id": channel_id, "agent_id": cred.agent_id,
        })
        if not existing_member:
            await db.insert("bus_channel_members", {
                "channel_id": channel_id,
                "agent_id": cred.agent_id,
                "joined_at": now,
            })

    async def stop(self) -> None:
        """Gracefully stop all subscribers and workers."""
        self.running = False

        self._subscriber_creds.clear()

        # Cancel all tasks (subscriber loops, workers, monitors)
        all_tasks = (
            list(self._subscriber_tasks.values())
            + self._workers
            + self._monitor_tasks
        )
        for task in all_tasks:
            task.cancel()

        self._subscriber_tasks.clear()
        self._workers.clear()
        self._monitor_tasks.clear()
        logger.info("LarkTrigger stopped")
