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
from xyz_agent_context.agent_runtime.logging_service import LoggingService
from xyz_agent_context.agent_runtime.run_collector import (
    RunError,
    collect_run,
)
from xyz_agent_context.channel.channel_context_builder_base import ChannelHistoryConfig
from xyz_agent_context.repository.lark_seen_message_repository import (
    LarkSeenMessageRepository,
)
from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils.timezone import utc_now


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
    # flowing, while still cutting off the hour-old-event replays Xiong
    # reported.
    HISTORY_BUFFER_MS = 5 * 60 * 1000

    # Durable-dedup retention: the `lark_seen_messages` table is cleaned
    # of rows older than this many days once per trigger startup.
    DEDUP_RETENTION_DAYS = 7

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
        # profile_name -> bot open_id, ensures each bot's echo is filtered
        self._bot_open_ids: dict[str, str] = {}
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

    async def start(self, db) -> None:
        """Start workers and credential watcher."""
        self.running = True
        self._db = db
        self._loop = asyncio.get_running_loop()
        self._startup_time_ms = int(time.time() * 1000)
        self._seen_repo = LarkSeenMessageRepository(db)

        # Purge old dedup rows so the table doesn't grow without bound.
        try:
            deleted = await self._seen_repo.cleanup_older_than_days(
                self.DEDUP_RETENTION_DAYS
            )
            if deleted:
                logger.info(
                    f"LarkTrigger: cleaned {deleted} dedup rows older than "
                    f"{self.DEDUP_RETENTION_DAYS} days"
                )
        except Exception as e:  # noqa: BLE001 — best-effort startup hygiene
            logger.warning(f"LarkTrigger: dedup cleanup failed: {e}")

        # Start baseline workers
        self._adjust_workers(self._base_workers)

        # Start credential watcher (checks for new/changed credentials periodically)
        watcher = asyncio.create_task(self._credential_watcher())
        self._monitor_tasks.append(watcher)

        logger.info(f"LarkTrigger started: {len(self._workers)} workers, watching for credentials")

    def _desired_worker_count(self) -> int:
        """Calculate how many workers we need based on active subscribers."""
        sub_count = len(self._subscriber_tasks)
        desired = self._base_workers + sub_count * self.WORKERS_PER_SUBSCRIBER
        return min(desired, self.MAX_WORKERS)

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

        logger.info(f"LarkTrigger: stopped subscriber for {profile} (app_id={app_id})")

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
                ws_client = lark.ws.Client(
                    app_id=cred.app_id,
                    app_secret=app_secret,
                    event_handler=handler,
                    domain=domain,
                )

                logger.info(f"LarkTrigger: connecting SDK WebSocket for {cred.profile_name}")

                # Run start() in a daemon thread with its own event loop
                thread_error = []

                def run_ws():
                    try:
                        # SDK's ws/client.py uses a module-level `loop` variable
                        # captured at import time. Replace it with a fresh loop
                        # so start() can call loop.run_until_complete() without conflict.
                        import lark_oapi.ws.client as ws_mod
                        fresh_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(fresh_loop)
                        ws_mod.loop = fresh_loop
                        ws_client._lock = asyncio.Lock()  # Lock must belong to the new loop
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
                logger.error(
                    f"LarkTrigger SDK error for {cred.profile_name} "
                    f"after {ran_seconds:.1f}s (next backoff {backoff}s): {e}"
                )

            if not self.running:
                break

            await asyncio.sleep(backoff)

    async def _dedup_and_enqueue(self, cred, event_dict: dict) -> None:
        """Check dedup; enqueue only if this is a genuinely new event."""
        if await self._should_process_event(event_dict):
            await self._task_queue.put((cred, event_dict))
        else:
            msg_id = event_dict.get("message_id", "")
            logger.info(
                f"LarkTrigger: dedup skipping message_id={msg_id!r} "
                f"(already processed or pre-startup replay)"
            )

    async def _should_process_event(self, event_dict: dict) -> bool:
        """
        Return True if the event should be handed to a worker, False if it
        should be dropped as a duplicate / historic replay.

        Three layers, cheapest-first:

          1. Startup-time filter (O(1), no I/O) — Lark events whose
             `create_time` is older than ``startup_time - HISTORY_BUFFER_MS``
             are replays from before this process started. Drop without
             any further check. This alone kills most of the restart-
             induced duplication because Lark re-delivers many minutes-
             to-hours-old events on reconnect.

          2. In-memory hot cache (O(1) with lock) — dedup within the
             current process lifetime. TTL-bounded so the dict doesn't
             grow without bound.

          3. Durable DB gate (one round-trip, atomic) — via
             ``LarkSeenMessageRepository.mark_seen``. Survives process
             restarts: a message recorded in a prior process lifetime
             will still be flagged as "seen". The unique constraint on
             ``message_id`` makes this atomic under concurrent workers.

        Fail-open on I/O error (return True) so that a transient DB
        issue doesn't silence the bot — double-processing is annoying
        but silent loss is worse.
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
                    return False
            except (ValueError, TypeError):
                # Non-numeric create_time — fall through to other layers.
                pass

        if not msg_id:
            # No id → can't dedup; process defensively. Lark's SDK should
            # always populate this, so this is belt-and-braces.
            return True

        # Layer 2: in-memory hot cache.
        now = time.time()
        with self._seen_lock:
            if msg_id in self._seen_messages:
                return False
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
                return newly_inserted
            except Exception as e:  # noqa: BLE001 — fail-open on I/O
                logger.warning(
                    f"LarkTrigger: DB dedup check failed for {msg_id}: "
                    f"{type(e).__name__}: {e}; processing anyway"
                )
        return True

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

            try:
                await self._process_message(cred, event, worker_id)
            except Exception as e:
                logger.error(
                    f"LarkTrigger worker {worker_id} error: {e}",
                    exc_info=True,
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
        """Check if message was sent by the bot itself (prevents echo loops)."""
        sender_type = event.get("sender_type", "")
        if sender_type in ("bot", "app"):
            return True
        # Lazy-load bot open_id per credential
        if cred.profile_name not in self._bot_open_ids:
            try:
                bot_info = await self._cli._run_with_agent_id(
                    ["api", "GET", "/open-apis/bot/v3/info"],
                    cred.agent_id,
                )
                if bot_info.get("success"):
                    bot_oid = bot_info.get("data", {}).get("bot", {}).get("open_id", "")
                    if bot_oid:
                        self._bot_open_ids[cred.profile_name] = bot_oid
            except Exception:
                logger.debug(f"Failed to fetch bot open_id for {cred.profile_name}")
        bot_oid = self._bot_open_ids.get(cred.profile_name, "")
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
        """Truncate and sanitize a display name for safe DB storage."""
        return (name or "Unknown")[:128]

    async def _process_message(
        self, cred: LarkCredential, event: dict, worker_id: int
    ) -> None:
        """Process a single incoming message event."""
        fields = self._parse_event_fields(event)
        chat_id = fields["chat_id"]
        sender_id = fields["sender_id"]
        sender_name = fields["sender_name"]
        message_id = fields["message_id"]

        # Filter bot echoes
        if await self._is_echo(cred, event, sender_id):
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

        runtime = AgentRuntime(logging_service=LoggingService(enabled=False))
        result = await collect_run(
            runtime,
            agent_id=cred.agent_id,
            user_id=owner_user_id,
            input_content=tagged_prompt,
            working_source=WorkingSource.LARK,
            trigger_extra_data={"channel_tag": channel_tag.to_dict()},
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
