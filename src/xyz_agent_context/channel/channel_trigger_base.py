"""
@file_name: channel_trigger_base.py
@date: 2026-05-08
@description: Abstract base for all IM channel triggers.

Direct extraction of the channel-agnostic machinery that lives in
``LarkTrigger`` today. Subclasses implement six channel-specific methods
(connect / parse_event / is_echo / resolve_sender_name /
create_context_builder / load_active_credentials); the base owns
everything else:

  - Credential watcher loop (start/stop subscribers as DB changes,
    daily cleanup, periodic heartbeat).
  - Per-credential subscribe loop with reconnect backoff and audit
    recording.
  - Three-layer dedup via ``ChannelDedupStore``.
  - Optional Debounce via ``ChannelDebounceMerger`` (subclasses can
    enable in __init__).
  - Worker pool with dynamic sizing, dead-task pruning, per-message
    timeout cap.
  - AgentRuntime invocation via ``collect_run`` (so subclasses never
    re-implement the silent-error-drop bug Lark patched).
  - Inbox writes via ``ChannelInboxWriter``.
  - Audit log via ``ChannelTriggerAuditRepository``.

PUSH mode (Phase 6) intentionally stubbed: ``handle_webhook`` and
``verify_webhook`` raise NotImplementedError. This file does NOT route
HTTP requests.

Lark today is NOT a subclass — Phase 2 owns that refactor. Phase 1 ships
the base + a mock-channel integration test that proves the abstraction
works end-to-end.
"""
from __future__ import annotations

import asyncio
import re
import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from xyz_agent_context.agent_runtime.run_collector import RunError

from loguru import logger

# NOTE: AgentRuntime / collect_run / RunError are imported lazily inside
# the methods that need them. Eager top-level import causes a circular
# load when ``channel/__init__.py`` (which re-exports ChannelTriggerBase
# for ergonomic imports) is reached during ``module/__init__.py``'s
# initial pass — module imports ``LarkModule`` → ``ChannelSenderRegistry``
# → ``channel/__init__.py`` → ``channel_trigger_base`` → ``AgentRuntime``
# → back into ``module``, which is still partially initialised.
from xyz_agent_context.channel.channel_audit_events import (
    EVENT_INGRESS_PROCESSED,
    EVENT_INGRESS_DROPPED_DEDUP,
    EVENT_INGRESS_DROPPED_HISTORIC,
    EVENT_INGRESS_DROPPED_ECHO,
    EVENT_INGRESS_DROPPED_UNBOUND,
    EVENT_DEDUP_FAIL_OPEN,
    EVENT_DEBOUNCE_MERGED,
    EVENT_SUBSCRIBER_STARTED,
    EVENT_SUBSCRIBER_STOPPED,
    EVENT_TRANSPORT_CONNECTED,
    EVENT_TRANSPORT_DISCONNECTED,
    EVENT_TRANSPORT_BACKOFF,
    EVENT_WORKER_ERROR,
    EVENT_WORKER_TIMEOUT,
    EVENT_INBOX_WRITE_FAILED,
    EVENT_HEARTBEAT,
    EVENT_ATTACHMENT_FETCH_FAILED,
)
from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
    ChannelHistoryConfig,
)
from xyz_agent_context.channel.channel_debounce_merger import ChannelDebounceMerger
from xyz_agent_context.channel.channel_dedup_store import ChannelDedupStore
from xyz_agent_context.channel.channel_inbox_writer import ChannelInboxWriter
from xyz_agent_context.repository.channel_seen_message_repository import (
    ChannelSeenMessageRepository,
)
from xyz_agent_context.repository.channel_trigger_audit_repository import (
    ChannelTriggerAuditRepository,
)
from xyz_agent_context.schema.attachment_schema import (
    Attachment,
    derive_category_from_mime,
)
from xyz_agent_context.schema.channel_tag import ChannelTag
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.parsed_message import ParsedMessage
from xyz_agent_context.utils.attachment_storage import store_uploaded_attachment


# Used by sanitize_display_name to strip C0/C1 controls (newlines, tabs,
# nulls, escape sequences). Public so subclasses with stricter rules can
# compose their own regex on top.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _compute_next_backoff(
    current: int,
    ran_seconds: float,
    *,
    base: int = 5,
    max_backoff: int = 120,
    healthy_threshold_seconds: int = 60,
) -> int:
    """
    Pick the next reconnect backoff.

    If the session that just ended lasted at least
    ``healthy_threshold_seconds``, treat it as a real connection and
    reset to ``base``. Otherwise double, clamped to ``max_backoff``.

    This matches the H-1 fix in LarkTrigger so subclasses get correct
    reconnect behaviour for free.
    """
    if ran_seconds >= healthy_threshold_seconds:
        return base
    return min(max(current, base) * 2, max_backoff)


class ChannelTriggerBase(ABC):
    """Abstract base class for IM channel triggers.

    Subclass contract — six methods MUST be implemented:
      - ``connect(credential)`` — async iterator yielding raw events
      - ``parse_event(raw)`` — raw event → ParsedMessage | None
      - ``is_echo(message, credential)`` — bot's own message?
      - ``resolve_sender_name(sender_id, credential)`` — display name lookup
      - ``create_context_builder(message, credential, agent_id)`` — prompt builder
      - ``load_active_credentials()`` — pull active credentials from DB

    Each credential MUST expose ``.agent_id`` and ``.app_id`` attributes.
    Subclasses may use any concrete credential type.

    Subclasses MUST also set the class attributes:
      - ``channel_name`` (lowercase, e.g. "slack")
      - ``brand_display`` (human label, e.g. "Slack")
      - ``working_source`` (the WorkingSource enum value to pass to AgentRuntime)
    """

    # ── Subclass MUST override ────────────────────────────────────────────
    channel_name: str = ""
    brand_display: str = ""
    working_source: WorkingSource = WorkingSource.CHAT  # subclass overrides

    # ── Tunable defaults — subclass may override ──────────────────────────
    MIN_WORKERS: int = 3
    WORKERS_PER_SUBSCRIBER: int = 2
    MAX_WORKERS: int = 50
    PROCESS_MESSAGE_TIMEOUT_SECONDS: int = 1800  # 30 min cap per message
    CLEANUP_INTERVAL_SECONDS: int = 24 * 3600
    HEARTBEAT_INTERVAL_SECONDS: int = 600
    DEDUP_RETENTION_DAYS: int = 7
    AUDIT_RETENTION_DAYS: int = 30
    CREDENTIAL_POLL_INTERVAL_SECONDS: int = 10
    IDLE_POLL_INTERVAL_SECONDS: int = 30  # when no credentials are active

    # If non-zero, submit messages through ChannelDebounceMerger with this
    # window before processing. 0 disables debounce (Lark today, Phase 2).
    DEBOUNCE_WINDOW_MS: int = 0

    # ── Construction ──────────────────────────────────────────────────────
    def __init__(self, *, base_workers: int = 3, history_config: Optional[ChannelHistoryConfig] = None):
        if not self.channel_name:
            raise ValueError(
                f"{type(self).__name__}.channel_name must be set on the subclass"
            )
        if not self.brand_display:
            raise ValueError(
                f"{type(self).__name__}.brand_display must be set on the subclass"
            )

        self._base_workers = max(base_workers, self.MIN_WORKERS)

        # Subscriber bookkeeping. Keyed on subscriber identity (default
        # ``credential.app_id`` — overridable via _subscriber_key).
        self._subscriber_tasks: dict[str, asyncio.Task] = {}
        self._subscriber_creds: dict[str, Any] = {}

        # Worker pool
        self._task_queue: asyncio.Queue = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._monitor_tasks: list[asyncio.Task] = []
        self.running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._db: Any = None  # set in start()

        # Owned helpers — instantiated in start() once we know channel + db.
        self._dedup_store: Optional[ChannelDedupStore] = None
        self._audit_repo: Optional[ChannelTriggerAuditRepository] = None
        self._inbox_writer = ChannelInboxWriter(self.channel_name, self.brand_display)

        # Optional debounce merger
        self._debounce: Optional[ChannelDebounceMerger] = (
            ChannelDebounceMerger(self.DEBOUNCE_WINDOW_MS)
            if self.DEBOUNCE_WINDOW_MS > 0
            else None
        )

        self._history_config = history_config or ChannelHistoryConfig(
            load_conversation_history=True,
            history_limit=20,
            history_max_chars=3000,
        )

        # Lifecycle bookkeeping
        self._startup_time_ms: int = 0
        self._last_cleanup_monotonic: float = 0.0
        self._last_heartbeat_monotonic: float = 0.0

    # ────────────────────────────────────────────────────────────────────
    # Subclass-implemented hooks (PULL mode)
    # ────────────────────────────────────────────────────────────────────

    @abstractmethod
    async def connect(self, credential: Any) -> AsyncIterator[dict]:
        """
        Establish the channel connection and yield raw events.

        Returns an async iterator. The base ``_subscribe_loop`` consumes it
        until the connection drops, then reconnects with exponential
        backoff via ``_compute_next_backoff``.

        Examples:
            - Lark:     yield SDK WebSocket message_dict per inbound event
            - Slack:    yield Socket Mode events_api payload per event
            - Telegram: yield each Update returned by getUpdates long polling
        """
        ...

    @abstractmethod
    def parse_event(self, raw: dict) -> Optional[ParsedMessage]:
        """Convert a raw platform event to a ParsedMessage. None drops it."""
        ...

    @abstractmethod
    async def is_echo(self, message: ParsedMessage, credential: Any) -> bool:
        """True iff the message was sent by the bot itself."""
        ...

    @abstractmethod
    async def resolve_sender_name(self, sender_id: str, credential: Any) -> str:
        """Look up a display name when ParsedMessage.sender_name is 'Unknown'."""
        ...

    @abstractmethod
    def create_context_builder(
        self, message: ParsedMessage, credential: Any, agent_id: str
    ) -> ChannelContextBuilderBase:
        """Build the channel-specific prompt builder."""
        ...

    @abstractmethod
    async def load_active_credentials(self) -> list[Any]:
        """Return all active credentials from this channel's credential table."""
        ...

    # Override hook — defaults to credential.app_id but subclasses may
    # combine team_id + app_id (Slack workspace) or similar.
    def _subscriber_key(self, credential: Any) -> str:
        return getattr(credential, "app_id", "")

    # Override hook — runs whenever a transport connection is established.
    # Default updates the dedup baseline so post-reconnect backlog drops.
    async def _on_transport_connected(self, credential: Any) -> None:
        if self._dedup_store is not None:
            self._dedup_store.update_baseline(int(time.time() * 1000))

    # Override hook — return True when the exception means "this token is
    # permanently broken; stop retrying." The default returns False, which
    # keeps the old behaviour (back off forever). Subclasses that can
    # identify revoked / invalid tokens override to True for those codes
    # so the loop can disable the credential and exit cleanly.
    def is_permanent_auth_failure(self, exc: BaseException) -> bool:
        return False

    # Override hook — flip the credential row's ``enabled`` flag to False
    # so the watcher stops respawning subscribers against a dead token.
    # Subclass implementations typically call ``mgr.set_enabled(agent_id,
    # False)``. Default is a no-op for safety.
    async def disable_credential(self, credential: Any) -> None:
        return None

    # Override hook — async context manager wrapping the actual
    # ``_build_and_run_agent`` call. Subclasses MAY use this to drive a
    # platform-side "processing" indicator that's visible to the human
    # waiting on the reply:
    #
    #   - Telegram: ``sendChatAction(chat_id, action="typing")`` shows a
    #     "typing..." hint. Decays after 5s, so the override re-fires
    #     every ~4s in a background task and cancels on exit.
    #   - Slack: ``assistant.threads.setStatus`` (assistant apps) or
    #     ``reactions.add`` (regular channels) — TBD per channel design.
    #   - Lark: ``bot/typing`` API — TBD.
    #
    # The default returns a no-op context manager so existing channels
    # keep working unchanged. Failures inside the override MUST NOT
    # propagate (the indicator is cosmetic; agent execution wins).
    @asynccontextmanager
    async def processing_indicator(
        self, credential: Any, message: ParsedMessage
    ) -> AsyncIterator[None]:
        yield

    # ────────────────────────────────────────────────────────────────────
    # Attachment ingestion (Phase 1a)
    # ────────────────────────────────────────────────────────────────────

    async def fetch_attachments(
        self, message: ParsedMessage, credential: Any
    ) -> list[Attachment]:
        """Download platform attachment refs and persist them locally.

        Subclasses for channels that support media (Telegram / Slack /
        Lark) override this; channels with no media support inherit the
        no-op default and the trigger flow stays text-only.

        Contract:
          - Read platform-specific refs from ``message.raw["attachment_refs"]``
            (populated by the subclass's ``parse_event``).
          - For each ref: call the platform SDK to fetch bytes, then
            ``_persist_attachment`` to land them on disk and assemble an
            ``Attachment``.
          - Return the list (empty when no refs / all failed).
          - **NEVER raise.** Failures must be swallowed, audited via
            ``EVENT_ATTACHMENT_FETCH_FAILED``, and reduced to the
            partial-result list. The agent run continues against
            ``message.content`` text — attachment loss is graceful
            degradation, not a worker crash. Mirrors the never-raise
            contract on ``ChannelModuleBase.hook_data_gathering``.

        Cross-platform ``kind`` field vocabulary is INTENTIONALLY
        non-normalized: each subclass uses its platform's native taxonomy:
          - Telegram: ``"document"`` / ``"photo"`` / ``"voice"`` /
            ``"audio"`` / ``"video"``
          - Slack:    ``"file"`` (single value — Slack treats every
            upload as a generic file)
          - Lark:     ``"image"`` / ``"file"`` / ``"audio"`` / ``"media"``
        The base class does NOT read ``kind`` anywhere. If you add
        cross-channel logic that switches on it, you MUST normalize
        first; otherwise the same ``"file"`` upload behaves differently
        across platforms. Cross-platform normalization is deferred to a
        post-Phase-2 cleanup (would touch all three subclass parsers).
        """
        return []

    async def _persist_attachment(
        self,
        *,
        agent_id: str,
        raw_bytes: bytes,
        original_name: str,
        mime_hint: str,
    ) -> Attachment:
        """Sniff MIME, store on disk, run Whisper STT for audio/*,
        return a fully-populated Attachment Pydantic model.

        MIME tier:  libmagic > platform hint > mimetypes.guess > octet-stream.
        STT:        same TranscriptionService used by the WS upload route
                    (see ``backend/routes/agents_attachments.py``).
                    Never-raise contract — transcript stays ``None`` on
                    failure / provider unavailable / non-audio MIME.

        Owner resolution: ``user_id`` is the agent OWNER (per
        ``agents.created_by``), not the IM sender. This matches the
        existing ``_build_and_run_agent`` fallback so attachment writes
        and Read-tool lookups target the same workspace path.
        """
        user_id = await self._resolve_agent_owner(agent_id) or agent_id
        mime_type = self._sniff_mime(raw_bytes, mime_hint, original_name)
        file_id, on_disk = store_uploaded_attachment(
            agent_id,
            user_id,
            raw_bytes=raw_bytes,
            original_name=original_name,
            mime_type=mime_type,
        )

        transcript: Optional[str] = None
        if mime_type.startswith("audio/"):
            try:
                # Lazy import to keep the channel layer free of agent_framework
                # dependencies at import time.
                from xyz_agent_context.agent_framework.transcription import (
                    TranscriptionService,
                )

                svc = TranscriptionService.instance()
                if await svc.is_available(user_id):
                    transcript = await svc.transcribe(
                        file_path=str(on_disk),
                        file_id=file_id,
                        agent_id=agent_id,
                        user_id=user_id,
                    )
                    if transcript:
                        logger.info(
                            f"[{self.channel_name}:{agent_id}] transcribed "
                            f"file_id={file_id} chars={len(transcript)}"
                        )
            except Exception as e:  # noqa: BLE001
                # Never-raise: STT failure must not block attachment flow.
                logger.warning(
                    f"[{self.channel_name}:{agent_id}] STT failed for "
                    f"file_id={file_id}: {type(e).__name__}: {e}"
                )

        return Attachment(
            file_id=file_id,
            mime_type=mime_type,
            original_name=original_name,
            size_bytes=len(raw_bytes),
            category=derive_category_from_mime(mime_type),
            transcript=transcript,
        )

    @staticmethod
    def _sniff_mime(raw_bytes: bytes, hint: str, filename: str) -> str:
        """Tiered MIME sniff: libmagic > platform hint > extension > octet-stream.

        Mirrors ``backend/routes/agents_attachments.py:_sniff_mime_type``
        tiering so IM uploads and WS uploads classify the same way.
        libmagic is optional — when unavailable we fall through cleanly
        to the platform hint.
        """
        try:
            import magic  # type: ignore[import-not-found]

            sniffed = magic.from_buffer(raw_bytes, mime=True)
            if sniffed and sniffed != "application/octet-stream":
                return sniffed
        except ImportError:
            # python-magic not installed; fall through.
            pass
        except Exception as e:  # noqa: BLE001
            logger.debug(f"libmagic sniff failed: {e}; falling back to hint")
        if hint:
            return hint
        import mimetypes

        guessed, _ = mimetypes.guess_type(filename or "")
        return guessed or "application/octet-stream"

    # ────────────────────────────────────────────────────────────────────
    # PUSH mode stubs (Phase 6)
    # ────────────────────────────────────────────────────────────────────

    async def handle_webhook(
        self, request_body: bytes, headers: dict
    ) -> Optional[ParsedMessage]:
        """Phase 6 — webhook ingress. Stubbed."""
        raise NotImplementedError(
            f"{self.channel_name} webhook mode is not implemented (Phase 6)"
        )

    def verify_webhook(
        self, request_body: bytes, headers: dict, signing_secret: str
    ) -> bool:
        """Phase 6 — webhook signature verification. Stubbed."""
        raise NotImplementedError(
            f"{self.channel_name} webhook mode is not implemented (Phase 6)"
        )

    # ────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ────────────────────────────────────────────────────────────────────

    async def start(self, db) -> None:
        """Start workers + credential watcher. Idempotent ish — call once per process."""
        self.running = True
        self._db = db
        self._loop = asyncio.get_running_loop()
        self._startup_time_ms = int(time.time() * 1000)

        seen_repo = ChannelSeenMessageRepository(self.channel_name, db)
        self._dedup_store = ChannelDedupStore(
            channel=self.channel_name,
            repo=seen_repo,
        )
        # First baseline = process startup. Subclasses' transport hook
        # advances it on reconnect.
        self._dedup_store.update_baseline(self._startup_time_ms)

        self._audit_repo = ChannelTriggerAuditRepository(self.channel_name, db)

        # Initial retention sweep.
        await self._run_cleanup()

        # Bring up baseline workers.
        self._adjust_workers(self._base_workers)

        watcher = asyncio.create_task(self._credential_watcher())
        self._monitor_tasks.append(watcher)

        logger.info(
            f"{type(self).__name__} started: {len(self._workers)} workers, "
            f"watching for {self.channel_name} credentials"
        )

    async def stop(self) -> None:
        """Cancel everything. Idempotent."""
        self.running = False

        # Drain any buffered debounced messages before shutting down.
        if self._debounce is not None:
            try:
                await self._debounce.flush_all(self._enqueue_debounced)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"{type(self).__name__}.stop: flush_all failed: {e}")

        self._subscriber_creds.clear()

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
        logger.info(f"{type(self).__name__} stopped")

    # ────────────────────────────────────────────────────────────────────
    # Worker pool sizing
    # ────────────────────────────────────────────────────────────────────

    def _desired_worker_count(self) -> int:
        return min(
            self._base_workers + len(self._subscriber_tasks) * self.WORKERS_PER_SUBSCRIBER,
            self.MAX_WORKERS,
        )

    def _adjust_workers(self, target: int) -> None:
        current = len(self._workers)
        if target > current:
            for i in range(current, target):
                worker = asyncio.ensure_future(self._worker(i))
                self._workers.append(worker)
            logger.info(
                f"{type(self).__name__}: scaled workers {current} -> {target}"
            )
        elif target < current:
            excess = self._workers[target:]
            for task in excess:
                task.cancel()
            self._workers = self._workers[:target]
            logger.info(
                f"{type(self).__name__}: scaled workers {current} -> {target}"
            )

    def _prune_dead_workers(self) -> int:
        """Drop done() tasks before sizing. H-4 fix from Lark — without
        this, ``_adjust_workers`` thinks the pool is at target while a
        crashed worker silently accumulates queue depth."""
        alive = [w for w in self._workers if not w.done()]
        pruned = len(self._workers) - len(alive)
        if pruned:
            logger.warning(
                f"{type(self).__name__}: pruned {pruned} dead worker task(s); "
                f"re-creating on next tick"
            )
        self._workers = alive
        return pruned

    # ────────────────────────────────────────────────────────────────────
    # Credential watcher
    # ────────────────────────────────────────────────────────────────────

    async def _credential_watcher(self) -> None:
        """Periodically reconcile DB credentials with running subscribers."""
        idle_logged = False
        while self.running:
            try:
                creds = await self.load_active_credentials()

                # Idle path — fewer log lines, longer poll.
                if not creds and not self._subscriber_tasks:
                    if not idle_logged:
                        logger.info(
                            f"{type(self).__name__}: no {self.channel_name} "
                            f"credentials bound, watching for new bindings..."
                        )
                        idle_logged = True
                    await asyncio.sleep(self.IDLE_POLL_INTERVAL_SECONDS)
                    continue
                idle_logged = False

                # Deduplicate by subscriber key (subclass may use compound
                # key). Last-write-wins on duplicates — matches Lark today.
                seen_keys: dict[str, Any] = {}
                for cred in creds:
                    key = self._subscriber_key(cred)
                    if key and key not in seen_keys:
                        seen_keys[key] = cred

                current_keys = set(seen_keys.keys())
                running_keys = set(self._subscriber_tasks.keys())

                # Stop subscribers whose credential is gone.
                for key in running_keys - current_keys:
                    await self._stop_subscriber(key)

                # Reap crashed subscriber tasks.
                dead_keys = [
                    k for k, t in self._subscriber_tasks.items() if t.done()
                ]
                for key in dead_keys:
                    logger.warning(
                        f"{type(self).__name__}: subscriber for {key} died, removing"
                    )
                    self._subscriber_tasks.pop(key, None)
                    self._subscriber_creds.pop(key, None)

                # Start subscribers for new keys; refresh the cached
                # credential for ALL keys every poll. The credential is a
                # DB snapshot — fields like permission_state / auth_status
                # change mid-session (e.g. the owner completing the
                # three-click user authorization). A subscriber captured its
                # credential once at connect time and the long-lived
                # transport never re-reads it, so without refreshing the
                # cache here the per-message path (resolve_sender_name,
                # context build) would keep seeing the stale pre-change
                # credential until the subscriber restarts. Refreshing the
                # cache is cheap (we already loaded creds this poll) and does
                # not disturb the live transport connection.
                for key, cred in seen_keys.items():
                    if key not in self._subscriber_tasks:
                        task = asyncio.create_task(self._subscribe_loop(cred))
                        self._subscriber_tasks[key] = task
                        agent_id = getattr(cred, "agent_id", "")
                        app_id = getattr(cred, "app_id", "")
                        logger.info(
                            f"{type(self).__name__}: started subscriber for "
                            f"agent={agent_id} key={key}"
                        )
                        await self._audit(
                            EVENT_SUBSCRIBER_STARTED,
                            agent_id=agent_id,
                            app_id=app_id,
                        )
                    self._subscriber_creds[key] = cred

                # Pool sizing.
                self._prune_dead_workers()
                self._adjust_workers(self._desired_worker_count())

                # Periodic retention sweep.
                if (
                    time.monotonic() - self._last_cleanup_monotonic
                    >= self.CLEANUP_INTERVAL_SECONDS
                ):
                    await self._run_cleanup()

                await self._maybe_heartbeat()

            except Exception as e:  # noqa: BLE001
                logger.warning(f"{type(self).__name__} credential watcher error: {e}")

            await asyncio.sleep(self.CREDENTIAL_POLL_INTERVAL_SECONDS)

    async def _stop_subscriber(self, key: str) -> None:
        cred = self._subscriber_creds.pop(key, None)
        task = self._subscriber_tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
        agent_id = getattr(cred, "agent_id", "") if cred else ""
        app_id = getattr(cred, "app_id", "") if cred else key
        logger.info(f"{type(self).__name__}: stopped subscriber for {key}")
        await self._audit(
            EVENT_SUBSCRIBER_STOPPED,
            agent_id=agent_id,
            app_id=app_id,
            details={"key": key},
        )

    # ────────────────────────────────────────────────────────────────────
    # Subscribe loop (PULL)
    # ────────────────────────────────────────────────────────────────────

    async def _subscribe_loop(self, credential: Any) -> None:
        """
        Drive ``connect()`` until it terminates, then reconnect with backoff.

        Each yielded raw event flows through:
          parse_event → dedup → debounce (optional) → enqueue.
        """
        backoff = 5
        max_backoff = 120
        agent_id = getattr(credential, "agent_id", "")
        app_id = getattr(credential, "app_id", "")

        while self.running:
            session_started = time.monotonic()
            try:
                await self._on_transport_connected(credential)
                await self._audit(
                    EVENT_TRANSPORT_CONNECTED,
                    agent_id=agent_id,
                    app_id=app_id,
                )

                async for raw in self.connect(credential):
                    if not self.running:
                        break
                    parsed = self.parse_event(raw)
                    if parsed is None:
                        continue
                    await self._dedup_and_handle(credential, parsed)

            except asyncio.CancelledError:
                logger.info(
                    f"{type(self).__name__}: subscriber cancelled for {app_id}"
                )
                return
            except Exception as e:  # noqa: BLE001
                ran = time.monotonic() - session_started
                # Permanent auth failure (revoked token, etc) — stop
                # hammering the upstream API. Disable the credential row
                # so the watcher doesn't immediately respawn this loop on
                # the next reconcile cycle. User has to re-bind to wake
                # the subscriber back up.
                if self.is_permanent_auth_failure(e):
                    logger.warning(
                        f"{type(self).__name__} permanent auth failure for "
                        f"agent={agent_id} app={app_id} after {ran:.1f}s: "
                        f"{type(e).__name__}: {e} — disabling credential"
                    )
                    await self._audit(
                        EVENT_TRANSPORT_DISCONNECTED,
                        agent_id=agent_id,
                        app_id=app_id,
                        details={
                            "ran_seconds": ran,
                            "error": f"{type(e).__name__}: {e}",
                            "permanent": True,
                        },
                    )
                    try:
                        await self.disable_credential(credential)
                    except Exception as disable_err:  # noqa: BLE001
                        logger.exception(
                            f"{type(self).__name__}: disable_credential raised "
                            f"for {agent_id}: {disable_err}"
                        )
                    return
                backoff = _compute_next_backoff(
                    current=backoff, ran_seconds=ran, max_backoff=max_backoff,
                )
                logger.exception(
                    f"{type(self).__name__} transport error for {app_id} "
                    f"after {ran:.1f}s (next backoff {backoff}s): {e}"
                )
                await self._audit(
                    EVENT_TRANSPORT_DISCONNECTED,
                    agent_id=agent_id,
                    app_id=app_id,
                    details={
                        "ran_seconds": ran,
                        "next_backoff_seconds": backoff,
                        "error": f"{type(e).__name__}: {e}",
                    },
                )
            else:
                ran = time.monotonic() - session_started
                backoff = _compute_next_backoff(
                    current=backoff, ran_seconds=ran, max_backoff=max_backoff,
                )
                await self._audit(
                    EVENT_TRANSPORT_DISCONNECTED,
                    agent_id=agent_id,
                    app_id=app_id,
                    details={
                        "ran_seconds": ran,
                        "next_backoff_seconds": backoff,
                    },
                )

            if not self.running:
                break

            await self._audit(
                EVENT_TRANSPORT_BACKOFF,
                agent_id=agent_id,
                app_id=app_id,
                details={"sleep_seconds": backoff},
            )
            await asyncio.sleep(backoff)

    # ────────────────────────────────────────────────────────────────────
    # Dedup + Debounce + Enqueue
    # ────────────────────────────────────────────────────────────────────

    async def _dedup_and_handle(
        self, credential: Any, message: ParsedMessage
    ) -> None:
        """Run the dedup cascade, audit the decision, hand off to debounce or queue."""
        agent_id = getattr(credential, "agent_id", "")
        app_id = getattr(credential, "app_id", "")

        if self._dedup_store is None:
            await self._task_queue.put((credential, message))
            return

        decision = await self._dedup_store.classify(
            message.message_id,
            message.timestamp_ms,
            agent_id=agent_id,
        )

        if decision["accept"]:
            await self._audit(
                EVENT_INGRESS_PROCESSED,
                message_id=message.message_id,
                agent_id=agent_id,
                app_id=app_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details={"dedup_layer": decision["layer"]},
            )
            if decision["layer"] == "db_fail_open":
                await self._audit(
                    EVENT_DEDUP_FAIL_OPEN,
                    message_id=message.message_id,
                    agent_id=agent_id,
                    app_id=app_id,
                    details={"error": decision.get("error", "")},
                )
            await self._enqueue_or_debounce(credential, message)
        else:
            event_name = (
                EVENT_INGRESS_DROPPED_HISTORIC
                if decision["layer"] == "historic"
                else EVENT_INGRESS_DROPPED_DEDUP
            )
            await self._audit(
                event_name,
                message_id=message.message_id,
                agent_id=agent_id,
                app_id=app_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details={"layer": decision["layer"]},
            )

    async def _enqueue_or_debounce(self, credential: Any, message: ParsedMessage) -> None:
        if self._debounce is None:
            await self._task_queue.put((credential, message))
            return
        # Stash the credential alongside the message so the debounce
        # callback can find it. We use a closure rather than encoding it
        # in ParsedMessage.raw to keep the schema clean.
        async def flush_callback(merged: ParsedMessage) -> None:
            await self._audit(
                EVENT_DEBOUNCE_MERGED,
                message_id=merged.message_id,
                agent_id=getattr(credential, "agent_id", ""),
                app_id=getattr(credential, "app_id", ""),
                chat_id=merged.chat_id,
                sender_id=merged.sender_id,
                details={"window_ms": self._debounce.window_ms if self._debounce else 0},
            )
            await self._task_queue.put((credential, merged))
        await self._debounce.submit(message, flush_callback)

    async def _enqueue_debounced(self, merged: ParsedMessage) -> None:
        """Used by ``stop()`` to drain pending debounce buffers."""
        # We don't have credential context here — the debounced lambda in
        # _enqueue_or_debounce captured one, but flush_all bypasses that
        # path. For graceful shutdown we simply log; messages in the
        # debounce buffer are already ack'd to the platform and present in
        # the audit log.
        logger.info(
            f"{type(self).__name__}: dropping debounced buffer on shutdown "
            f"(message_id={merged.message_id})"
        )

    # ────────────────────────────────────────────────────────────────────
    # Workers
    # ────────────────────────────────────────────────────────────────────

    async def _worker(self, worker_id: int) -> None:
        """
        Drain the queue and process each message.

        NOTE for future optimisation: AgentRuntime is currently
        instantiated per message. If this trigger sees QPS > 5/sec
        sustained, or P99 ``_process_message`` time exceeds 3s due to
        agent_runtime.run() cold-start, evaluate an LRU cache keyed on
        (agent_id, user_id).
        """
        while self.running:
            try:
                credential, message = await asyncio.wait_for(
                    self._task_queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                continue

            # Re-resolve the credential to the latest cached snapshot. The
            # event was enqueued carrying whatever credential the subscriber
            # held when it arrived; the watcher keeps _subscriber_creds
            # current with the DB, so processing against the cached one means
            # mid-session credential changes (e.g. the owner finishing user
            # authorization) take effect on the very next message instead of
            # only after a subscriber restart. Falls back to the dequeued
            # credential when the key is gone — the _process_message
            # gatekeeper then drops the event as unbound.
            sub_key = self._subscriber_key(credential)
            if sub_key:
                credential = self._subscriber_creds.get(sub_key, credential)

            try:
                await asyncio.wait_for(
                    self._process_message(credential, message),
                    timeout=self.PROCESS_MESSAGE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.exception(
                    f"{type(self).__name__} worker {worker_id} message "
                    f"{message.message_id!r} exceeded "
                    f"{self.PROCESS_MESSAGE_TIMEOUT_SECONDS}s — cancelling"
                )
                await self._audit(
                    EVENT_WORKER_TIMEOUT,
                    message_id=message.message_id,
                    agent_id=getattr(credential, "agent_id", ""),
                    app_id=getattr(credential, "app_id", ""),
                    chat_id=message.chat_id,
                    details={
                        "worker_id": worker_id,
                        "timeout_seconds": self.PROCESS_MESSAGE_TIMEOUT_SECONDS,
                    },
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    f"{type(self).__name__} worker {worker_id} error: {e}"
                )
                await self._audit(
                    EVENT_WORKER_ERROR,
                    message_id=message.message_id,
                    agent_id=getattr(credential, "agent_id", ""),
                    app_id=getattr(credential, "app_id", ""),
                    chat_id=message.chat_id,
                    details={
                        "worker_id": worker_id,
                        "error": f"{type(e).__name__}: {e}",
                    },
                )

    async def _process_message(self, credential: Any, message: ParsedMessage) -> None:
        """Echo filter → name resolution → context build → run agent → inbox."""
        agent_id = getattr(credential, "agent_id", "")
        app_id = getattr(credential, "app_id", "")

        # Cred gatekeeper — events from unbound credentials reach this point
        # if a subscriber crashed mid-stream (or a subclass's connect()
        # keeps yielding from a background thread post-cancel). Reject so
        # the agent never runs against a bot the user has unbound.
        sub_key = self._subscriber_key(credential)
        if sub_key and sub_key not in self._subscriber_creds:
            logger.info(
                f"{type(self).__name__}: dropping event from unbound credential "
                f"agent={agent_id} key={sub_key} msg_id={message.message_id!r}"
            )
            await self._audit(
                EVENT_INGRESS_DROPPED_UNBOUND,
                message_id=message.message_id,
                agent_id=agent_id,
                app_id=app_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
            )
            return

        # Echo filter
        if await self.is_echo(message, credential):
            await self._audit(
                EVENT_INGRESS_DROPPED_ECHO,
                message_id=message.message_id,
                agent_id=agent_id,
                app_id=app_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
            )
            return

        # Empty-content guard. Originally written for Phase 1a when ParsedMessage
        # was text-only — at that point an empty content was a clear no-op.
        # Phase 1b made files a first-class message kind: a user can upload a
        # PDF / image / voice memo with NO caption (very common, especially on
        # Slack drag-drop), in which case message.content is "" but
        # message.raw["attachment_refs"] is non-empty and the upload MUST
        # still trigger the agent. So the guard now keeps the early-return
        # only when there's NEITHER text NOR attachment refs.
        has_refs = bool((message.raw or {}).get("attachment_refs"))
        if (not message.content or not message.content.strip()) and not has_refs:
            return

        # Name resolution + sanitization
        sender_name = message.sender_name
        if (not sender_name or sender_name == "Unknown") and message.sender_id:
            sender_name = await self.resolve_sender_name(message.sender_id, credential)
        sender_name = self.sanitize_display_name(sender_name)

        logger.info(
            f"{type(self).__name__}[{app_id}] from {sender_name} ({message.sender_id}): "
            f"{message.content[:100]}"
        )

        # Attachment ingestion (Phase 1a). Never-raise: any failure
        # degrades to text-only run while preserving the audit trail.
        attachments: list[Attachment] = []
        try:
            attachments = await self.fetch_attachments(message, credential)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"{type(self).__name__}[{app_id}] fetch_attachments raised: "
                f"{type(e).__name__}: {e}"
            )
            await self._audit(
                EVENT_ATTACHMENT_FETCH_FAILED,
                message_id=message.message_id,
                agent_id=agent_id,
                app_id=app_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details={"error": f"{type(e).__name__}: {e}"},
            )

        async with self.processing_indicator(credential, message):
            output_text = await self._build_and_run_agent(
                credential, message, sender_name, attachments=attachments
            )

        try:
            await self._inbox_writer.write(
                db=self._db,
                agent_id=agent_id,
                sender_id=message.sender_id,
                sender_name=sender_name,
                original_message=message.content,
                agent_response=output_text,
                chat_id=message.chat_id,
            )
        except Exception as e:  # noqa: BLE001
            await self._audit(
                EVENT_INBOX_WRITE_FAILED,
                message_id=message.message_id,
                agent_id=agent_id,
                app_id=app_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details={
                    "error": f"{type(e).__name__}: {e}",
                    "sender_name": sender_name,
                    "original_message": message.content[:500],
                    "agent_response": (output_text or "")[:500],
                },
            )

    async def _build_and_run_agent(
        self,
        credential: Any,
        message: ParsedMessage,
        sender_name: str,
        *,
        attachments: Optional[list[Attachment]] = None,
    ) -> str:
        """Build prompt via subclass's context builder, run AgentRuntime, return text.

        ``attachments`` is the list returned by ``fetch_attachments``
        (may be empty). When non-empty it is serialised into
        ``trigger_extra_data["attachments"]`` so ChatModule's
        ``hook_data_gathering`` can synthesise markers into chat_history.
        Mirrors ``backend/routes/websocket.py`` which uses the same key.
        """
        # Lazy import — see top-of-file comment about circular dependency.
        from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime
        from xyz_agent_context.agent_runtime.run_collector import collect_run

        agent_id = getattr(credential, "agent_id", "")

        builder = self.create_context_builder(message, credential, agent_id)
        prompt = await builder.build_prompt(self._history_config)
        # Clean retrieval anchor (sender + this-turn body) for narrative
        # routing — not the full tagged execution prompt. Graceful: the anchor
        # is a retrieval optimization, so its failure must never break the agent
        # run (narrative falls back to input_content). See 2026-06-01 design.
        try:
            anchor = await builder.build_retrieval_anchor()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"build_retrieval_anchor failed; using input fallback: {e}")
            anchor = None

        # Build ChannelTag — generic channel string is enough for any
        # channel we can't represent via a ChannelTag factory method yet.
        channel_tag = ChannelTag(
            channel=self.channel_name,
            sender_name=sender_name,
            sender_id=message.sender_id,
            room_id=message.chat_id,
        )
        tagged_prompt = f"{channel_tag.format()}\n{prompt}"

        # Resolve the AGENT'S OWNER (NarraNexus user_id), NOT the IM
        # sender. ProviderResolver maps the owner's user_id to API keys;
        # using the IM sender_id silently routes through the system
        # default provider. This bug has been fixed in Lark — preserve
        # the fix here for free.
        owner_user_id = await self._resolve_agent_owner(agent_id) or agent_id

        extra_data: dict[str, Any] = {
            "channel_tag": channel_tag.to_dict(),
            "retrieval_anchor": anchor,
            "trigger_id": (
                f"{self.channel_name}_{message.message_id}"
                if message.message_id
                else f"{self.channel_name}_unknown"
            ),
        }
        # Only set "attachments" when non-empty — matches the WS route
        # pattern in backend/routes/websocket.py:644-648 so ChatModule's
        # downstream `.get("attachments")` check behaves identically.
        if attachments:
            extra_data["attachments"] = [
                a.model_dump(mode="json") for a in attachments
            ]

        runtime = AgentRuntime()
        result = await collect_run(
            runtime,
            agent_id=agent_id,
            user_id=owner_user_id,
            input_content=tagged_prompt,
            working_source=self.working_source,
            trigger_extra_data=extra_data,
        )

        if result.is_error:
            logger.warning(
                f"{type(self).__name__}[{agent_id}] runtime error "
                f"({result.error.error_type}): {result.error.error_message}"
            )
            return self.format_error_reply(result.error)

        # Subclasses may want to extract platform-specific tool-call output;
        # default returns the agent's text. Lark's subclass will override
        # to look at result.raw_items in Phase 2.
        return self.extract_output(result, message, credential)

    # ────────────────────────────────────────────────────────────────────
    # Owner resolution + agent output extraction (subclass override hooks)
    # ────────────────────────────────────────────────────────────────────

    async def _resolve_agent_owner(self, agent_id: str) -> str:
        """Look up the agent's owner user_id from the agents table.
        Returns empty string on miss; caller falls back to agent_id."""
        if self._db is None or not agent_id:
            return ""
        try:
            row = await self._db.get_one("agents", {"agent_id": agent_id})
            return (row or {}).get("created_by", "") or ""
        except Exception:  # noqa: BLE001
            return ""

    def format_error_reply(self, error: "RunError") -> str:
        """
        Render an AgentRuntime failure as a user-facing IM reply.
        Default returns a generic apology. Subclasses may override per-channel.
        """
        return (
            "⚠️ I hit an internal error and can't reply to this message. "
            "Please try again in a bit, or contact the bot's owner."
        )

    def extract_output(self, result, message: ParsedMessage, credential: Any) -> str:
        """
        Return the text to record in the inbox.
        Default returns the agent's accumulated text. Subclasses may
        introspect ``result.raw_items`` for platform-specific tool calls
        (e.g. Lark scrapes ``lark_cli`` send-message arguments).
        """
        if result.output_text and result.output_text.strip():
            return result.output_text
        return ""

    # ────────────────────────────────────────────────────────────────────
    # Audit helpers
    # ────────────────────────────────────────────────────────────────────

    async def _audit(self, event_type: str, **kwargs) -> None:
        if self._audit_repo is None:
            return
        await self._audit_repo.append(event_type, **kwargs)

    async def _maybe_heartbeat(self) -> None:
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
        }
        await self._audit_repo.append(EVENT_HEARTBEAT, details=details)

    async def _run_cleanup(self) -> None:
        """Per-channel retention sweep. Called once at startup + daily."""
        self._last_cleanup_monotonic = time.monotonic()
        if self._dedup_store is not None and self._dedup_store._repo is not None:
            try:
                deleted = await self._dedup_store._repo.cleanup_older_than_days(
                    self.DEDUP_RETENTION_DAYS
                )
                if deleted:
                    logger.info(
                        f"{type(self).__name__}: cleaned {deleted} dedup rows "
                        f"older than {self.DEDUP_RETENTION_DAYS} days"
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"{type(self).__name__}: dedup cleanup failed: {e}")

        if self._audit_repo is not None:
            try:
                deleted = await self._audit_repo.cleanup_older_than_days(
                    self.AUDIT_RETENTION_DAYS
                )
                if deleted:
                    logger.info(
                        f"{type(self).__name__}: cleaned {deleted} audit rows "
                        f"older than {self.AUDIT_RETENTION_DAYS} days"
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"{type(self).__name__}: audit cleanup failed: {e}")

    # ────────────────────────────────────────────────────────────────────
    # Sanitisation
    # ────────────────────────────────────────────────────────────────────

    @staticmethod
    def sanitize_display_name(name: str) -> str:
        """Strip C0/C1 controls + collapse whitespace + truncate to 128 chars.

        Display names are user-controlled strings. Embedding raw ones
        into a prompt via ChannelTag opens a prompt-injection seam
        (newlines + fake 'SYSTEM:' prefixes). Mirrors Lark's existing
        ``_sanitize_display_name``.
        """
        if not name:
            return "Unknown"
        cleaned = _CONTROL_CHARS_RE.sub(" ", name)
        cleaned = " ".join(cleaned.split())
        return cleaned[:128] or "Unknown"
