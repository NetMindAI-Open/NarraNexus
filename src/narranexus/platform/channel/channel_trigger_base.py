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
  - Inbox writes via ``InboxRecorder`` (the inbox's own tables).
  - Audit log via ``ChannelTriggerAuditRepository``.

PUSH mode (Phase 6) intentionally stubbed: ``handle_webhook`` and
``verify_webhook`` raise NotImplementedError. This file does NOT route
HTTP requests.

Concrete subclasses today: ``LarkTrigger``, ``SlackTrigger``,
``TelegramTrigger``, ``DiscordTrigger`` — each implements the six
abstract methods (connect / parse_event / is_echo / resolve_sender_name /
create_context_builder / load_active_credentials) and inherits everything
below.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, List, NamedTuple, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from narranexus.platform.agent_runtime.run_collector import RunError
    from narranexus.platform.agent_framework.loop.circuit_breaker import TurnAdmission

from loguru import logger

# NOTE: the AgentRuntimeClient (and RunError) are imported lazily inside
# the methods that need them. Eager top-level import causes a circular
# load when ``channel/__init__.py`` (which re-exports ChannelTriggerBase
# for ergonomic imports) is reached during ``module/__init__.py``'s
# initial pass — module imports ``LarkModule`` → ``ChannelSenderRegistry``
# → ``channel/__init__.py`` → ``channel_trigger_base`` → the runtime
# → back into ``module``, which is still partially initialised. The
# client seam (agent_runtime/client.py) is itself import-safe, but we
# keep the call-site import lazy to preserve this guarantee.
from narranexus.platform.channel.channel_audit_events import (
    EVENT_MANAGED_INGRESS_PROCESSED,
    EVENT_INGRESS_PROCESSED,
    EVENT_INGRESS_DROPPED_DEDUP,
    EVENT_INGRESS_DROPPED_HISTORIC,
    EVENT_INGRESS_DROPPED_ECHO,
    EVENT_INGRESS_DROPPED_UNBOUND,
    EVENT_INGRESS_DROPPED_UNPARSED,
    EVENT_INGRESS_DROPPED_EMPTY,
    EVENT_DEDUP_FAIL_OPEN,
    EVENT_DEBOUNCE_MERGED,
    EVENT_SUBSCRIBER_STARTED,
    EVENT_SUBSCRIBER_STOPPED,
    EVENT_SUBSCRIBER_BREAKER_TRIPPED,
    EVENT_SUBSCRIBER_BREAKER_CLEARED,
    EVENT_SUBSCRIBER_UNSTARTABLE,
    EVENT_TRANSPORT_CONNECTED,
    EVENT_TRANSPORT_DISCONNECTED,
    EVENT_TRANSPORT_BACKOFF,
    EVENT_WORKER_ERROR,
    EVENT_WORKER_TIMEOUT,
    EVENT_INBOX_WRITE_FAILED,
    EVENT_HEARTBEAT,
    EVENT_ATTACHMENT_FETCH_FAILED,
)
from narranexus.platform.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
    ChannelHistoryConfig,
)
from narranexus.platform.channel.channel_debounce_merger import ChannelDebounceMerger
from narranexus.platform.channel.channel_dedup_store import ChannelDedupStore
from narranexus.platform.channel.ingress_guard import (
    IngressGuard,
    IngressVerdict,
    content_fingerprint,
)
from narranexus.platform.channel.inbox_recorder import (
    InboxRecorder,
    im_thread_id,
    resolve_owner_for_agent,
)
from narranexus.platform.channel.channel_reactions import render_early_feedback
from narranexus.platform.repository.channel_ingress_breaker_repository import (
    ChannelIngressBreakerRepository,
)
from narranexus.platform.repository.channel_seen_message_repository import (
    ChannelSeenMessageRepository,
)
from narranexus.platform.repository.channel_trigger_audit_repository import (
    ChannelTriggerAuditRepository,
)
from narranexus.platform.schema.attachment_schema import Attachment
from narranexus.platform.schema.channel_tag import ChannelTag
# Top-level, matching the same hoist done in step_3 this round: no cycle
# here either — `channel/__init__.py` imports message_source_handler well
# before channel_trigger_base. (The NOTE at the top of this file about lazy
# imports is specifically about `agent_runtime.client`, a different
# direction.) Two styles for the same module in one change would read as a
# trap that isn't there.
from narranexus.platform.channel.message_source_handler import (
    PLATFORM_REPLY_TEXT_KEY,
)
from narranexus.platform.schema.hook_schema import WorkingSource
from narranexus.platform.schema.parsed_message import (
    UNKNOWN_SENDER_NAME,
    ChatType,
    ParsedMessage,
)
from narranexus.platform.utils.attachment_storage import persist_attachment_bytes


# Used by sanitize_display_name to strip C0/C1 controls (newlines, tabs,
# nulls, escape sequences). Public so subclasses with stricter rules can
# compose their own regex on top.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


# Inbox sentinel: the agent finished a turn without calling ANY user-reply
# tool. Written to the inbox only, NEVER sent to the channel (respect the
# agent's / silent-mode choice not to speak). Shared across channels so the
# error-fallback path can tell "agent stayed silent" from "agent replied".
CHANNEL_SILENT_SENTINEL = "(stayed silent)"


class ChannelTurnOutput(NamedTuple):
    """``ChannelTriggerBase._run_agent_turn``'s answer.

    ``refused`` is True when the agent circuit-breaker refused the turn:
    ``text`` is then the refusal copy and the gate (``_circuit_admission``)
    already owns its delivery — sent once, or throttled for a group window —
    so a channel that sends the returned text itself must not send it again.
    """

    text: str
    refused: bool = False


class RefusalClaim(NamedTuple):
    """A claim on one chat's circuit-breaker refusal notice
    (``ChannelTriggerBase._claim_circuit_refusal_send``). ``key`` is
    ``(channel_name, agent_id, chat_id)``; ``window`` is the breaker window
    the claim was taken in, or None when the send is not throttled (a 1:1
    chat, or a refusal without a window) and there is nothing to hand back.
    """

    key: tuple[str, str, str]
    window: Optional[str]


# Why a silent memory batch did not run — stable short codes carried in the
# managed silent-ingest receipt. Exception classes and runtime error types
# stay in the log: the receipt is platform-facing and must not change shape
# when an internal exception type does.
SILENT_BATCH_EMPTY = "empty_batch"
SILENT_BATCH_RUNTIME_RAISED = "runtime_raised"
SILENT_BATCH_RUNTIME_ERROR = "runtime_error"


# Upstream error text that reaches a credential row (and from there the
# owner's panel, which has no fold) is trimmed to this many characters.
DISABLE_REASON_MAX_CHARS = 200
# The audit table's ``details.error`` keeps more: it is the post-mortem
# source of truth, and sits next to ``original_message`` /
# ``agent_response`` which are already cut at 500. Panel width and audit
# depth are different trade-offs, hence two names.
AUDIT_ERROR_MAX_CHARS = 500
_REASON_URL = re.compile(r"https?://\S+")
# Bot tokens (Telegram ``<digits>:<base64>``), JWTs and long opaque
# secrets — anything a transport exception string could echo.
_REASON_SECRET = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_.-]{10,}|\b[A-Za-z0-9_-]{32,}\b")


def safe_error_text(exc: BaseException, max_chars: int = DISABLE_REASON_MAX_CHARS) -> str:
    """The one sanitised rendering of a subscriber error.

    Exception type + message, with URLs and token-shaped runs masked and the
    whole thing capped at ``max_chars`` (DISABLE_REASON_MAX_CHARS for the
    credential row, AUDIT_ERROR_MAX_CHARS for audit rows). Used for the persisted
    ``disabled_reason``, the formatted subscriber log line and the audit
    row's ``details.error`` — never the raw ``str(exc)``. It is a safety
    net, not the only guard: the traceback ``logger.exception`` renders
    still ends with the raw exception text, so a channel SDK must strip
    its own secrets at the source (Telegram: ``TelegramSDKClient._redact``
    removes the bot token from every aiohttp message before it becomes a
    ``TelegramSDKError``). Masking here catches what an SDK missed.
    """
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    text = _REASON_URL.sub("<url>", text)
    text = _REASON_SECRET.sub("<redacted>", text)
    return text[:max_chars]


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


# Fingerprint stand-in for a credential with no introspectable attributes.
# A constant (never the default repr, which embeds the object address) so an
# un-fingerprintable credential reads as UNCHANGED rather than as a re-bind
# on every poll.
_UNFINGERPRINTABLE_CREDENTIAL = "<unfingerprintable>"


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

    # The react tool name to show in the per-turn "ack early" directive (see
    # _early_feedback_prefix). Fully-qualified for Lark (to match its
    # ``mcp__lark_module__…`` convention), bare for the others. None = the
    # channel has no reaction API (WeChat) → the ack is a message instead.
    react_tool_ref: Optional[str] = None

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

    # Fast-death circuit breaker for subscriber restarts. A subscriber that
    # exits within BREAKER_FAST_DEATH_SECONDS of starting counts as a fast
    # death; BREAKER_FAST_DEATH_THRESHOLD consecutive fast deaths trip the
    # breaker and the key is isolated, re-probed on the escalating
    # BREAKER_BACKOFF_SCHEDULE_SECONDS delays (last entry repeats forever).
    # Complements ``is_permanent_auth_failure``: that hook needs a
    # *recognised exception*, but a cleared secret makes the subscriber exit
    # silently (return, no raise) so the watcher would otherwise restart the
    # same dead key every poll forever. The breaker never mutates the
    # credential row — an updated credential (owner re-binds) clears it
    # immediately, and a healthy lifetime resets the counters.
    BREAKER_FAST_DEATH_SECONDS: float = 60.0
    BREAKER_FAST_DEATH_THRESHOLD: int = 3
    BREAKER_BACKOFF_SCHEDULE_SECONDS: tuple[float, ...] = (300.0, 1800.0, 7200.0)

    # Credential fields EXCLUDED from the breaker fingerprint. The
    # fingerprint answers exactly one question — "did the owner change
    # something that might fix this credential?" — so bookkeeping the
    # subscriber writes back about itself must not count. Miss one and the
    # breaker clears itself on its own traffic: Matrix's
    # ``matrix_since_token`` is documented WRITE-HEAVY (every sync
    # response), Lark writes ``bot_open_id`` / ``bot_name`` right after
    # connecting. Subclasses extend this set; the escalation memory
    # deliberately survives a fingerprint-triggered clear so that even a
    # field we FAILED to exclude costs a bounded 3 restarts per backoff
    # tier instead of an unbounded storm.
    BREAKER_VOLATILE_CREDENTIAL_FIELDS: frozenset[str] = frozenset(
        {"created_at", "updated_at"}
    )

    # If non-zero, submit messages through ChannelDebounceMerger with this
    # window before processing. 0 disables debounce (Lark today, Phase 2).
    DEBOUNCE_WINDOW_MS: int = 0

    # If non-zero, dedup ALSO fingerprints (chat, sender, content) for this
    # many seconds — for platforms that re-dispatch the same message under a
    # NEW message_id (NarraMessenger's 15-min invocation deadline → X1
    # double-reply). Costs: a user re-sending the exact same text within the
    # window is treated as the platform's re-dispatch and dropped — size the
    # window to the platform's re-dispatch horizon, no larger.
    CONTENT_DEDUP_WINDOW_SECONDS: int = 0

    # Ingress circuit breaker (2026-08-24, the 8/14 ping-pong incident).
    # A DIFFERENT question from every gate above it: dedup asks "have I
    # seen this exact message?", debounce asks "did these arrive together?",
    # the fast-death breaker asks "is my own credential broken?". This one
    # asks "is the traffic in this conversation worth processing at all?".
    #
    # A session must clear BOTH bars — fast AND repetitive. That conjunction
    # is what lets P1 ship a hard breaker without the design's L0
    # observation tier: varied traffic (a user firing off six thoughts, a
    # group at peak, a job batch) can never satisfy the repetition bar, so
    # the population L0 existed to protect is structurally immune.
    # Agent peers get tighter numbers on both — A2A rooms are where loops
    # live, and no human types the same sentence twenty times in ten minutes.
    INGRESS_GUARD_ENABLED: bool = True
    INGRESS_WINDOW_SECONDS: float = 600.0
    INGRESS_RATE_THRESHOLD: int = 60
    INGRESS_DUP_RATIO_THRESHOLD: float = 0.8
    INGRESS_AGENT_RATE_THRESHOLD: int = 20
    INGRESS_AGENT_DUP_RATIO_THRESHOLD: float = 0.5
    INGRESS_BREAKER_SCHEDULE_SECONDS: tuple[float, ...] = (
        300.0, 1800.0, 7200.0, 86400.0,
    )
    INGRESS_RECOVERY_WINDOWS: int = 2
    INGRESS_BREAKER_RETENTION_DAYS: int = 30

    # Circuit-breaker refusal notices already sent, per (channel, agent,
    # chat) → breaker window; shared by every trigger in the process and
    # bounded (oldest dropped). See ``_claim_circuit_refusal_send``.
    _circuit_refusal_windows: "OrderedDict[tuple[str, str, str], str]" = OrderedDict()
    _CIRCUIT_REFUSAL_WINDOWS_MAX: int = 4096

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
        # What each RUNNING subscriber was started with. The fingerprint is
        # captured here (not read off _subscriber_creds at death time) so
        # the breaker compares like with like: "the credential this start
        # used" vs "the credential in the DB now". _subscriber_creds is a
        # poll behind, so sampling it would make any between-poll write look
        # like a re-bind.
        self._subscriber_started_at: dict[str, float] = {}
        self._subscriber_start_fingerprint: dict[str, str] = {}

        # Circuit-breaker state, keyed like _subscriber_tasks.
        self._breaker_fast_deaths: dict[str, int] = {}
        self._breaker_trips: dict[str, int] = {}
        self._breaker_blocked_until: dict[str, float] = {}
        self._breaker_cred_fingerprint: dict[str, str] = {}
        # Keys rejected by the ``should_start_subscriber`` pre-flight, with
        # the fingerprint that was rejected — so the WARNING + audit row
        # fire once per credential state, not once per poll.
        self._unstartable_fingerprint: dict[str, str] = {}

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
        self._ingress_guard: Optional[IngressGuard] = None
        # Records the turn into the inbox's OWN tables. Its predecessor
        # (`ChannelInboxWriter`) wrote the MessageBus tables, which put every
        # IM message on the agent's bus unread cursor — 1,364 messages across
        # 90 agents on prod, none of which any cursor advanced past.
        self._inbox_recorder = InboxRecorder(self.channel_name, self.brand_display)

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

        # Lifecycle bookkeeping.
        #
        # The two "last ran at" marks are -inf, not 0.0, and the difference is
        # not cosmetic. Both gates are `time.monotonic() - mark >= interval`,
        # and `time.monotonic()` counts from BOOT on Linux — so 0.0 does not
        # mean "never ran", it means "ran at boot". On a host whose uptime is
        # below the interval the gate silently stays shut:
        #   * heartbeat (600s): no L2 liveness row for the first 10 minutes of
        #     a freshly booted host — precisely the window where a failing
        #     start is most likely, and precisely what incident lesson #4 asks
        #     these beats to cover.
        #   * cleanup (24h): `_run_cleanup`'s own docstring says "once at
        #     startup + daily", which a new EC2 instance did not get; the first
        #     sweep waited for uptime to reach a day.
        # -inf makes "never ran" a real sentinel, so the first cycle always
        # runs. On a long-uptime machine (`now - 0.0` already huge) behaviour is
        # unchanged — this only fixes the freshly-booted case. Neither mark is
        # ever reported, only differenced, so an infinity cannot leak into a
        # payload; `uptime_seconds` in the heartbeat is built from
        # `_startup_time_ms` (wall clock) and is untouched.
        self._startup_time_ms: int = 0
        self._last_cleanup_monotonic: float = float("-inf")
        self._last_heartbeat_monotonic: float = float("-inf")

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

    # Override hook — pre-flight: can a subscriber possibly start with this
    # credential? Answered from the credential alone, before any connection
    # attempt. The default returns True (every loaded credential is tried).
    # Channels override when a row can be known-bad up front — Lark's
    # ``receive_enabled()`` is False once the App Secret is cleared, which
    # is exactly the 2026-08-01 prod incident: the subscriber used to start,
    # discover the missing secret, and return, on every poll forever.
    # Rejecting here costs zero restarts and zero ERROR lines; the
    # fast-death breaker stays the backstop for everything NOT knowable in
    # advance. Must not do I/O — it runs on every watcher poll.
    def should_start_subscriber(self, credential: Any) -> bool:
        return True

    # Override hook — is the far side of this message another agent rather
    # than a human? Default False: on most channels every sender is a
    # person, and guessing wrong in that direction only costs looser
    # breaker thresholds. NarraMessenger overrides it because its Matrix
    # user ids carry the answer (``@agent-<id>:<homeserver>``), and A2A
    # rooms are precisely where the 8/14 ping-pong loop lived.
    #
    # Answered HERE, once, because only the trigger layer knows each
    # platform's identity convention. The answer travels on ``ChannelTag``
    # so consumers above the trigger read one definition instead of each
    # re-deriving it. Three of them now: the ingress breaker (tighter
    # thresholds for an agent peer), the DM fallback gate (never invent a
    # reply TO another agent), and the DM prompt (tell the model the far
    # side is a machine). Answering the question three ways is how those
    # three drift apart.
    #
    # Contract for overrides: depend on ``message`` ONLY — no ``self``, no
    # I/O, no awaits. It runs per message, and the guard test calls it
    # unbound; an override that reaches for ``self`` would surface there as
    # a confusing AttributeError rather than as "you broke the contract".
    def is_agent_peer(self, message: ParsedMessage) -> bool:
        return False

    # Override hook — flip the credential row's ``enabled`` flag to False
    # so the watcher stops respawning subscribers against a dead token.
    # ``reason`` is the sanitised cause (``safe_error_text``: exception
    # type + message, URLs/token-shaped runs masked, <=200 chars) the loop
    # hands over; every built-in channel persists it via
    # ``mgr.set_enabled(agent_id, False, reason=reason)`` so the owner's
    # panel can say WHY the channel went inactive, and logs an error when
    # the store reports the flip did not happen (a still-enabled dead
    # credential would be reconnected forever). Default is a no-op for
    # safety. This is a contract change for every subclass (2026-09-09):
    # the loop calls it with ``reason=`` as a keyword.
    async def disable_credential(self, credential: Any, reason: str = "") -> None:
        return None

    @staticmethod
    def log_disable_outcome(channel: str, agent_id: str, ok: bool, reason: str) -> None:
        """One line per automatic disable; ERROR when the row flip failed."""
        if ok:
            logger.warning(f"[{channel}:{agent_id}] credential disabled: {reason}")
        else:
            logger.error(
                f"[{channel}:{agent_id}] credential could NOT be disabled "
                f"(row missing or write lost) — it will be reconnected: {reason}"
            )

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
            contract on ``ChannelModuleBase.gather``.

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
                    (see ``backend/routes/agents/attachments.py``).
                    Never-raise contract — transcript stays ``None`` on
                    failure / provider unavailable / non-audio MIME.

        Owner resolution: ``user_id`` is the agent OWNER (per
        ``agents.created_by``), not the IM sender. This matches the
        existing ``_build_and_run_agent`` fallback so attachment writes
        and Read-tool lookups target the same workspace path.
        """
        user_id = await self._resolve_agent_owner(agent_id) or agent_id
        mime_type = self._sniff_mime(raw_bytes, mime_hint, original_name)
        # Shared "bytes → Attachment" tail (store + index + STT) — one home
        # with the managed-ingress converter.
        return await persist_attachment_bytes(
            agent_id,
            user_id,
            raw_bytes=raw_bytes,
            original_name=original_name,
            mime_type=mime_type,
            log_prefix=self.channel_name,
        )

    @staticmethod
    def _sniff_mime(raw_bytes: bytes, hint: str, filename: str) -> str:
        """Tiered MIME sniff — delegates to the shared ``utils.mime_sniff``
        helper so IM downloads, WS uploads and team-chat uploads all classify
        the same way. The platform-supplied ``hint`` plays the client-type
        role: container-ambiguity tiebreaker and last resort, never primary.
        """
        from narranexus.platform.utils.mime_sniff import sniff_mime_type

        return sniff_mime_type(raw_bytes, filename=filename, client_type=hint or None)

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

    async def pre_start(self, db) -> None:
        """One-time, channel-specific bootstrap run BEFORE ``start()``.

        Default is a no-op. The consolidated supervisor
        (``module/run_channel_triggers.py``) calls ``pre_start(db)`` then
        ``start(db)`` for each trigger. Subclasses override this to run their
        own idempotent one-off migrations here — keeping channel-specific
        logic inside the channel (rule #4) instead of in the shared entrypoint.

        Example: ``LarkTrigger`` migrates the legacy ``auth_status`` value that
        used to live in the standalone ``run_lark_trigger`` entrypoint.

        MUST be idempotent (it runs on every process start) and SHOULD NOT
        raise — the supervisor wraps the call, but a channel that fails
        pre_start is skipped entirely, so swallow recoverable errors here.
        """
        return None

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
            content_window_seconds=self.CONTENT_DEDUP_WINDOW_SECONDS,
        )
        # First baseline = process startup. Subclasses' transport hook
        # advances it on reconnect.
        self._dedup_store.update_baseline(self._startup_time_ms)

        self._audit_repo = ChannelTriggerAuditRepository(self.channel_name, db)

        if self.INGRESS_GUARD_ENABLED:
            self._ingress_guard = self.build_ingress_guard(db)
            # Restore still-isolated conversations BEFORE serving traffic,
            # so /healthz and the heartbeat report the real standing state
            # from the first beat instead of an all-clear that only becomes
            # true again once every isolated sender happens to speak.
            await self._ingress_guard.warm_start(self.channel_name)

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
        self._subscriber_started_at.clear()
        self._subscriber_start_fingerprint.clear()
        # Isolation describes live subscriber state; a stopped trigger must
        # not carry it into the next start().
        self._breaker_fast_deaths.clear()
        self._breaker_trips.clear()
        self._breaker_blocked_until.clear()
        self._breaker_cred_fingerprint.clear()
        self._unstartable_fingerprint.clear()
        self._workers.clear()
        self._monitor_tasks.clear()
        logger.info(f"{type(self).__name__} stopped")

    async def health_snapshot(self) -> dict:
        """Return this trigger's JSON-serialisable operational state.

        The health server consumes this public seam instead of reaching into
        subscriber, worker, and breaker internals. Subclasses may extend the
        payload without coupling the server or its test doubles to new private
        bookkeeping fields.
        """
        now_ms = int(time.time() * 1000)
        startup_ms = self._startup_time_ms or 0
        recent_counts: dict[str, int] = {}
        if self._audit_repo is not None:
            try:
                recent_counts = await self._audit_repo.count_by_type(since_hours=1)
            except Exception as e:  # noqa: BLE001 — health must degrade, not crash
                logger.warning(
                    f"[health:{self.channel_name}] count_by_type failed: {e}"
                )

        status = "ok" if self._audit_repo is not None and self.running else "starting"
        return {
            "status": status,
            "running": self.running,
            "uptime_seconds": (
                (now_ms - startup_ms) / 1000.0 if startup_ms else 0.0
            ),
            "startup_time_ms": startup_ms,
            "last_ws_connected_ms": getattr(
                self, "_last_ws_connected_wallclock_ms", 0
            ),
            "subscriber_count": len(self._subscriber_tasks),
            "worker_count": len(self._workers),
            "queue_depth": self._task_queue.qsize(),
            "subscriber_keys": sorted(self._subscriber_creds),
            "breaker_isolated_keys": sorted(self._breaker_blocked_until),
            "unstartable_keys": sorted(self._unstartable_fingerprint),
            # Counts, not keys: a session key contains a chat id and a
            # sender id, and /healthz is not the place to enumerate who is
            # talking to whom. The audit table has the identities.
            "ingress_breaker_cooling_count": (
                self._ingress_guard.cooling_session_count()
                if self._ingress_guard else 0
            ),
            "ingress_breaker_open_count": (
                self._ingress_guard.open_session_count()
                if self._ingress_guard else 0
            ),
            "recent_event_counts": recent_counts,
        }

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
                    self._breaker_purge_stale(set())
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

                # A removed credential also retires its breaker state.
                self._breaker_purge_stale(current_keys)

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
                    dead_cred = self._subscriber_creds.get(key)
                    self._subscriber_tasks.pop(key, None)
                    self._subscriber_creds.pop(key, None)
                    if self._dedup_store is not None and dead_cred is not None:
                        self._dedup_store.clear_agent_baseline(
                            getattr(dead_cred, "agent_id", "")
                        )
                    await self._breaker_record_death(key, dead_cred)

                # A subscriber that is still running after the fast-death
                # window has already proved healthy. Settle that success now;
                # requiring it to die before clearing escalation memory would
                # make every successful backoff recovery permanently raise the
                # next incident's isolation tier.
                now = time.monotonic()
                for key, started in self._subscriber_started_at.items():
                    task = self._subscriber_tasks.get(key)
                    if (
                        task is not None
                        and not task.done()
                        and now - started >= self.BREAKER_FAST_DEATH_SECONDS
                    ):
                        self._breaker_forget(key)

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
                        await self._maybe_start_subscriber(key, cred)
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

    async def _maybe_start_subscriber(self, key: str, cred: Any) -> None:
        """Start one subscriber, unless a gate says otherwise.

        Two independent gates, cheapest first: a credential that provably
        cannot connect never costs a start at all (pre-flight), and anything
        else has to get past the fast-death breaker.
        """
        if not self.should_start_subscriber(cred):
            await self._note_unstartable(key, cred)
            return
        # Startable again — retire any stale pre-flight mark even when the
        # breaker still holds this key; the gates are independent and the
        # health surface reports both.
        self._unstartable_fingerprint.pop(key, None)
        if not await self._breaker_admit(key, cred):
            return

        self._subscriber_tasks[key] = asyncio.create_task(self._subscribe_loop(cred))
        self._subscriber_started_at[key] = time.monotonic()
        self._subscriber_start_fingerprint[key] = self._credential_fingerprint(cred)
        agent_id = getattr(cred, "agent_id", "")
        app_id = getattr(cred, "app_id", "")
        logger.info(
            f"{type(self).__name__}: started subscriber for "
            f"agent={agent_id} key={key}"
        )
        await self._audit(
            EVENT_SUBSCRIBER_STARTED, agent_id=agent_id, app_id=app_id,
        )

    async def _stop_subscriber(self, key: str) -> None:
        cred = self._subscriber_creds.pop(key, None)
        task = self._subscriber_tasks.pop(key, None)
        if self._dedup_store is not None and cred is not None:
            self._dedup_store.clear_agent_baseline(getattr(cred, "agent_id", ""))
        # An intentional stop is not a death — drop the start marks so the
        # breaker never counts it.
        self._subscriber_started_at.pop(key, None)
        self._subscriber_start_fingerprint.pop(key, None)
        if task and not task.done():
            task.cancel()
        agent_id = getattr(cred, "agent_id", "") if cred else ""
        app_id = getattr(cred, "app_id", "") if cred else key
        # Release this agent's ingress sessions. ``prune_idle`` cannot do
        # it: it deliberately keeps sessions carrying escalation memory, so
        # an unbound agent's tripped conversations would otherwise sit in
        # memory for the life of the process. The durable rows stay — a
        # re-bind must not hand a re-offender a fresh budget.
        if self._ingress_guard is not None and agent_id:
            self._ingress_guard.forget_agent(agent_id)
        logger.info(f"{type(self).__name__}: stopped subscriber for {key}")
        await self._audit(
            EVENT_SUBSCRIBER_STOPPED,
            agent_id=agent_id,
            app_id=app_id,
            details={"key": key},
        )

    # ────────────────────────────────────────────────────────────────────
    # Fast-death circuit breaker
    # ────────────────────────────────────────────────────────────────────

    def _credential_fingerprint(self, cred: Any) -> str:
        """Stable identity of a credential's *auth-relevant* contents.

        Answers one question: "did the owner change something that might fix
        this credential?" Fields the subscriber writes back about itself
        (cursors, cached platform identity, timestamps) are excluded via
        ``BREAKER_VOLATILE_CREDENTIAL_FIELDS`` — counting them would make the
        channel's own traffic read as a re-bind and clear the breaker on the
        poll right after it tripped. Credential field values must also have a
        stable ``repr`` across DB loads. If a channel introduces a field whose
        value uses the default address-bearing object repr, that field must be
        excluded as volatile or the channel must override this method with a
        stable auth-relevant encoding.
        """
        try:
            fields = vars(cred)
        except TypeError:
            # ``__slots__`` / C-level object: no introspectable state. Return
            # a CONSTANT, never ``repr(cred)`` — the default repr embeds the
            # object address, which differs on every poll and would read as
            # "the owner re-bound" forever, i.e. no breaker at all. A
            # constant fails safe: isolation holds and the backoff re-probe
            # remains the recovery path.
            return _UNFINGERPRINTABLE_CREDENTIAL
        volatile = self.BREAKER_VOLATILE_CREDENTIAL_FIELDS
        return repr(sorted(
            (k, repr(v)) for k, v in fields.items() if k not in volatile
        ))

    def _breaker_release(self, key: str) -> None:
        """Lift isolation but KEEP the escalation memory (``_breaker_trips``).

        Used for both re-probes and re-binds. Keeping the trip count is what
        bounds the damage of a volatile field we failed to exclude: such a
        key still escalates 5min → 30min → 2h instead of being restarted at
        the shortest tier forever. A genuinely healthy session clears the
        count for real (see ``_breaker_record_death``).
        """
        self._breaker_blocked_until.pop(key, None)
        self._breaker_cred_fingerprint.pop(key, None)
        self._breaker_fast_deaths.pop(key, None)

    def _breaker_forget(self, key: str) -> None:
        """Wipe every trace of a key — healthy subscriber or gone credential."""
        self._breaker_fast_deaths.pop(key, None)
        self._breaker_trips.pop(key, None)
        self._breaker_blocked_until.pop(key, None)
        self._breaker_cred_fingerprint.pop(key, None)
        self._unstartable_fingerprint.pop(key, None)

    def _breaker_purge_stale(self, current_keys: set) -> None:
        """Retire breaker state for keys whose credential no longer exists."""
        tracked = (
            set(self._breaker_fast_deaths)
            | set(self._breaker_trips)
            | set(self._breaker_blocked_until)
            | set(self._breaker_cred_fingerprint)
            | set(self._unstartable_fingerprint)
        )
        for key in tracked - current_keys:
            self._breaker_forget(key)

    async def _breaker_record_death(self, key: str, cred: Any) -> None:
        """Count a reaped subscriber death; trip the breaker on a streak.

        Only *fast* deaths (lifetime < BREAKER_FAST_DEATH_SECONDS) count — a
        subscriber that lived a healthy stretch before dying resets the
        streak, so ordinary network flaps never trip. ``cred`` supplies audit
        labels ONLY; the fingerprint recorded at trip time is the one
        captured when this subscriber was started.
        """
        started = self._subscriber_started_at.pop(key, None)
        start_fingerprint = self._subscriber_start_fingerprint.pop(key, None)
        if started is None:
            return
        lifetime = time.monotonic() - started
        if lifetime >= self.BREAKER_FAST_DEATH_SECONDS:
            self._breaker_forget(key)
            return
        deaths = self._breaker_fast_deaths.get(key, 0) + 1
        self._breaker_fast_deaths[key] = deaths
        if deaths < self.BREAKER_FAST_DEATH_THRESHOLD:
            return

        trips = self._breaker_trips.get(key, 0)
        schedule = self.BREAKER_BACKOFF_SCHEDULE_SECONDS
        delay = schedule[min(trips, len(schedule) - 1)]
        self._breaker_trips[key] = trips + 1
        self._breaker_blocked_until[key] = time.monotonic() + delay
        # The re-probe after backoff gets a fresh streak of its own.
        self._breaker_fast_deaths[key] = 0
        if start_fingerprint is not None:
            self._breaker_cred_fingerprint[key] = start_fingerprint
        agent_id = getattr(cred, "agent_id", "") if cred else ""
        app_id = getattr(cred, "app_id", "") if cred else key
        logger.error(
            f"{type(self).__name__}: circuit breaker TRIPPED for {key} "
            f"(agent={agent_id}): {deaths} consecutive deaths within "
            f"{self.BREAKER_FAST_DEATH_SECONDS:.0f}s of starting; isolating "
            f"for {delay:.0f}s (trip #{trips + 1}). Re-binding the "
            f"credential clears the breaker immediately."
        )
        await self._audit(
            EVENT_SUBSCRIBER_BREAKER_TRIPPED,
            agent_id=agent_id,
            app_id=app_id,
            details={
                "key": key,
                "consecutive_fast_deaths": deaths,
                "isolated_seconds": delay,
                "trip_number": trips + 1,
            },
        )

    async def _breaker_admit(self, key: str, cred: Any) -> bool:
        """Decide whether an isolated key may start again — and record it.

        Not a pure predicate: admitting clears the isolation state and writes
        the audit row that closes the DB trail (a re-probe that left no trace
        would read as "tripped … long silence … tripped again").
        """
        blocked_until = self._breaker_blocked_until.get(key)
        if blocked_until is None:
            return True
        if self._credential_fingerprint(cred) != self._breaker_cred_fingerprint.get(key):
            reason = "credential_changed"
        elif time.monotonic() >= blocked_until:
            reason = "backoff_expired"
        else:
            return False

        agent_id = getattr(cred, "agent_id", "")
        logger.info(
            f"{type(self).__name__}: circuit breaker CLEARED for {key} "
            f"(agent={agent_id}): {reason}"
        )
        await self._audit(
            EVENT_SUBSCRIBER_BREAKER_CLEARED,
            agent_id=agent_id,
            app_id=getattr(cred, "app_id", "") or key,
            details={"key": key, "reason": reason},
        )
        self._breaker_release(key)
        return True

    async def _note_unstartable(self, key: str, cred: Any) -> None:
        """Report a credential the pre-flight rejected — once per state.

        Logging every poll would recreate the spam the breaker exists to
        stop, so the rejected fingerprint is remembered; a re-bind is a new
        situation and reports again.
        """
        fingerprint = self._credential_fingerprint(cred)
        if self._unstartable_fingerprint.get(key) == fingerprint:
            return
        self._unstartable_fingerprint[key] = fingerprint
        agent_id = getattr(cred, "agent_id", "")
        logger.warning(
            f"{type(self).__name__}: credential for {key} (agent={agent_id}) "
            f"cannot start a subscriber — skipping until it changes"
        )
        await self._audit(
            EVENT_SUBSCRIBER_UNSTARTABLE,
            agent_id=agent_id,
            app_id=getattr(cred, "app_id", "") or key,
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
        sub_key = self._subscriber_key(credential)

        while self.running:
            # Every (re)connect starts from the watcher's latest DB snapshot,
            # not the credential captured when this loop was spawned. The
            # watcher refreshes ``_subscriber_creds`` on every poll INCLUDING
            # volatile write-back fields (Matrix's ``matrix_since_token``);
            # reusing the spawn-time object made each reconnect replay
            # everything since the subscriber started.
            credential = self._subscriber_creds.get(sub_key, credential) if sub_key else credential
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
                        await self._on_unparsed(credential, raw)
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
                    error_text = safe_error_text(e, AUDIT_ERROR_MAX_CHARS)
                    logger.warning(
                        f"{type(self).__name__} permanent auth failure for "
                        f"agent={agent_id} app={app_id} after {ran:.1f}s: "
                        f"{error_text} — disabling credential"
                    )
                    await self._audit(
                        EVENT_TRANSPORT_DISCONNECTED,
                        agent_id=agent_id,
                        app_id=app_id,
                        details={
                            "ran_seconds": ran,
                            "error": error_text,
                            "permanent": True,
                        },
                    )
                    try:
                        # The credential row renders in the panel: shorter cap.
                        await self.disable_credential(credential, reason=safe_error_text(e))
                    except Exception as disable_err:  # noqa: BLE001
                        logger.exception(
                            f"{type(self).__name__}: disable_credential raised "
                            f"for {agent_id}: {disable_err}"
                        )
                    return
                backoff = _compute_next_backoff(
                    current=backoff, ran_seconds=ran, max_backoff=max_backoff,
                )
                error_text = safe_error_text(e, AUDIT_ERROR_MAX_CHARS)
                logger.exception(
                    f"{type(self).__name__} transport error for {app_id} "
                    f"after {ran:.1f}s (next backoff {backoff}s): {error_text}"
                )
                await self._audit(
                    EVENT_TRANSPORT_DISCONNECTED,
                    agent_id=agent_id,
                    app_id=app_id,
                    details={
                        "ran_seconds": ran,
                        "next_backoff_seconds": backoff,
                        "error": error_text,
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
            content_fingerprint=self._content_fingerprint(message),
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
            audit_details: dict[str, Any] = {"layer": decision["layer"]}
            if "age_min" in decision:
                # Lets triage tell a replay (age ~ time since last session)
                # from a loss (a never-processed message dropped as old).
                audit_details["age_min"] = round(decision["age_min"], 1)
            await self._audit(
                event_name,
                message_id=message.message_id,
                agent_id=agent_id,
                app_id=app_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details=audit_details,
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
                error_text = safe_error_text(e, AUDIT_ERROR_MAX_CHARS)
                logger.exception(
                    f"{type(self).__name__} worker {worker_id} error: {error_text}"
                )
                await self._audit(
                    EVENT_WORKER_ERROR,
                    message_id=message.message_id,
                    agent_id=getattr(credential, "agent_id", ""),
                    app_id=getattr(credential, "app_id", ""),
                    chat_id=message.chat_id,
                    details={
                        "worker_id": worker_id,
                        "error": error_text,
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
            # Audited, not silent: a payload shape the extractor didn't
            # recognise lands here looking identical to a genuinely empty
            # message, and "why didn't the bot reply?" must stay
            # answerable from the trace (lessons #3/#5).
            await self._audit(
                EVENT_INGRESS_DROPPED_EMPTY,
                message_id=message.message_id,
                agent_id=agent_id,
                app_id=app_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details={
                    "content_type": message.content_type.value,
                    "has_raw": bool(message.raw),
                },
            )
            return

        # Ingress circuit breaker — "is this worth processing at all?".
        # Last gate before anything expensive IN THIS METHOD: name
        # resolution hits the platform API, attachment fetch downloads
        # bytes, and the run itself burns the pipeline.
        #
        # Not the first gate on every route, though. A channel that owns
        # its own ``_process_message`` may spend before delegating here —
        # Matrix pays a Narra ``authorize-event`` round-trip on its dm and
        # group_mention paths first. Stated rather than fixed: hoisting the
        # breaker above that would make ``super()`` judge the same message
        # a second time, so the window would count it twice and the channel
        # would trip at half its threshold. Closing that properly means
        # either a "already judged" channel into the base or Matrix taking
        # over ``_process_message`` outright, which is not a one-line
        # change. The waste is one HTTP round-trip per suppressed message;
        # the expensive half is still blocked. Audits its own drops (see
        # _ingress_admitted).
        if not await self._ingress_admitted(credential, message):
            return

        # Name resolution + sanitization
        sender_name = message.sender_name
        if (not sender_name or sender_name == UNKNOWN_SENDER_NAME) and message.sender_id:
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
            error_text = safe_error_text(e, AUDIT_ERROR_MAX_CHARS)
            logger.warning(
                f"{type(self).__name__}[{app_id}] fetch_attachments raised: {error_text}"
            )
            await self._audit(
                EVENT_ATTACHMENT_FETCH_FAILED,
                message_id=message.message_id,
                agent_id=agent_id,
                app_id=app_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details={"error": error_text},
            )

        async with self.processing_indicator(credential, message):
            output_text = await self._build_and_run_agent(
                credential, message, sender_name, attachments=attachments
            )

        try:
            await self._inbox_recorder.record_turn(
                db=self._db,
                thread_id=im_thread_id(self.channel_name, agent_id, message.chat_id),
                chat_id=message.chat_id,
                chat_type=message.chat_type,
                owner_user_id=await resolve_owner_for_agent(self._db, agent_id),
                agent_id=agent_id,
                counterpart_id=message.sender_id,
                counterpart_name=sender_name,
                inbound_text=message.content,
                outbound_text=output_text,
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
                    "error": safe_error_text(e, AUDIT_ERROR_MAX_CHARS),
                    "sender_name": sender_name,
                    "original_message": message.content[:500],
                    "agent_response": (output_text or "")[:500],
                },
            )

    def _early_feedback_prefix(self, message: ParsedMessage) -> str:
        """The per-turn "ack early" directive prepended to the tagged prompt.

        Channel-agnostic: each trigger sets ``react_tool_ref`` (bare name, or
        fully-qualified for Lark, or None for a channel with no reaction API).
        Reaction-capable channels get the react form when the inbound message id
        is present; otherwise (missing id, or ``react_tool_ref=None``) they
        degrade to the message-only "send a quick 'on it'" form. Always returns a
        non-empty directive for an IM turn — never nothing.
        """
        # A react-capable channel only gets the react form when the inbound
        # message id is present; otherwise (missing id, or no reaction API at
        # all) degrade to the message-only "on it" ack — never emit nothing,
        # which would defeat the point.
        if self.react_tool_ref and message.message_id:
            return render_early_feedback(
                tool_ref=self.react_tool_ref,
                room_id=message.chat_id,
                message_id=message.message_id,
                inline=True,
            )
        return render_early_feedback(
            tool_ref=None, room_id="", message_id="", inline=True,
        )

    def build_trigger_extra_data(
        self,
        *,
        channel_tag: ChannelTag,
        retrieval_anchor: Optional[str],
        trigger_id: str,
        builder: Any = None,
        attachments: Optional[list[Attachment]] = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Assemble ``trigger_extra_data`` for one channel turn.

        **Every** path that starts an AgentRuntime run for an inbound
        channel message must go through here — including subclasses that
        override ``_build_and_run_agent`` wholesale (Lark) or ship their own
        streaming variant (NarraMessenger).

        Why it is a base-class seam rather than a line each site remembers
        to add: the dict was hand-rolled in four places, and the 2026-08-06
        turn envelope (`channel_room_type` / `channel_reply_kwargs`) was
        added to exactly one of them. The other three silently reported an
        empty room type, so `step_3` read every Lark / NarraMessenger /
        silent-batch DM as a group room and the 1:1 no-reply fallback was
        dead code on those channels — the same defect class as the ctx-vs-
        context wiring bug, twice more. A shared builder is what makes the
        NEXT envelope key impossible to miss.

        ``builder`` is optional: paths with no context builder (the silent
        batch merge) get no envelope, which degrades to "not a DM" — no
        fallback, the safe default.

        ``**extra`` carries per-path keys — Lark's ``source_message_id`` and
        the batch path's ``batch_messages`` today. Empty values are dropped
        so callers don't have to branch.

        NarraMessenger's ``rtc_voice`` deliberately does NOT come through
        here: it depends on a ``turn_profile`` computed after this call, so
        that path assigns it onto the returned dict afterwards. Stated
        because this docstring is the contract for the seat — an example
        listed here that the code does differently is worse than no example.
        """
        extra_data: dict[str, Any] = {
            "channel_tag": channel_tag.to_dict(),
            "retrieval_anchor": retrieval_anchor,
            "trigger_id": trigger_id,
        }
        for key, value in extra.items():
            if value is not None:
                extra_data[key] = value
        # Room type + this channel's delivery kwargs. step_3 needs both to
        # decide whether a silent turn was a 1:1 DM (where a person is
        # waiting) and, if so, to deliver a platform-written reply through
        # ChannelSenderRegistry. Generic keys on purpose — the runtime must
        # not learn any channel's specifics (iron rule #3).
        if builder is not None:
            try:
                extra_data.update(builder.turn_envelope())
            except Exception as e:  # noqa: BLE001 — envelope is best-effort
                logger.warning(
                    f"{type(self).__name__}: turn_envelope failed "
                    f"({type(e).__name__}: {e}); turn degrades to non-DM"
                )
        # Only set "attachments" when non-empty — matches the WS route
        # pattern in backend/routes/websocket.py:644-648 so ChatModule's
        # downstream `.get("attachments")` check behaves identically.
        if attachments:
            extra_data["attachments"] = [
                a.model_dump(mode="json") for a in attachments
            ]
        return extra_data

    async def _build_and_run_agent(
        self,
        credential: Any,
        message: ParsedMessage,
        sender_name: str,
        *,
        attachments: Optional[list[Attachment]] = None,
    ) -> str:
        """Build prompt via subclass's context builder, run AgentRuntime, return text.

        The text of ``_run_agent_turn``; a channel that sends the returned
        text itself calls ``_run_agent_turn`` instead, to learn whether the
        circuit-breaker gate already owns delivery (a refusal).
        """
        output = await self._run_agent_turn(
            credential, message, sender_name, attachments=attachments
        )
        return output.text

    async def _run_agent_turn(
        self,
        credential: Any,
        message: ParsedMessage,
        sender_name: str,
        *,
        attachments: Optional[list[Attachment]] = None,
    ) -> ChannelTurnOutput:
        """The turn behind ``_build_and_run_agent``.

        ``attachments`` is the list returned by ``fetch_attachments``
        (may be empty). When non-empty it is serialised into
        ``trigger_extra_data["attachments"]`` so ChatModule's
        ``gather`` can synthesise markers into chat_history.
        Mirrors ``backend/routes/websocket.py`` which uses the same key.
        """
        # Lazy import — see top-of-file comment about circular dependency.
        from narranexus.platform.agent_runtime.client import (
            get_agent_runtime_client,
        )

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
            is_agent_peer=self.is_agent_peer(message),
        )
        tagged_prompt = (
            f"{channel_tag.format()}\n"
            f"{self._early_feedback_prefix(message)}{prompt}"
        )

        # Resolve the AGENT'S OWNER (NarraNexus user_id), NOT the IM
        # sender. ProviderResolver maps the owner's user_id to API keys;
        # using the IM sender_id silently routes through the system
        # default provider. This bug has been fixed in Lark — preserve
        # the fix here for free.
        owner_user_id = await self._resolve_agent_owner(agent_id) or agent_id

        extra_data = self.build_trigger_extra_data(
            channel_tag=channel_tag,
            retrieval_anchor=anchor,
            trigger_id=(
                f"{self.channel_name}_{message.message_id}"
                if message.message_id
                else f"{self.channel_name}_unknown"
            ),
            builder=builder,
            attachments=attachments,
            # The inbound platform message id, surfaced per-turn so a channel
            # module's contribute_instructions can tell the agent which message to
            # react to / reply in-thread (the react_to_user_message tool). Kept
            # here (not in ChannelTag) so it stays ephemeral, not persisted.
            source_message_id=message.message_id or "",
        )

        # Circuit-breaker gate, at the last point before the turn starts —
        # every "no turn" branch (dedup, echo, non-@ group message, ingress
        # guard) has already returned upstream in _process_message.
        admission, refusal = await self._circuit_admission(
            credential, message, agent_id
        )
        if refusal is not None:
            return ChannelTurnOutput(refusal, refused=True)

        try:
            result = await get_agent_runtime_client().run_and_collect(
                agent_id=agent_id,
                user_id=owner_user_id,
                input_content=tagged_prompt,
                working_source=self.working_source,
                trigger_extra_data=extra_data,
                probe_token=admission.probe_token,
            )
        except Exception as e:  # noqa: BLE001
            # The runtime is DESIGNED to yield MessageType.ERROR rather than
            # raise (see run_collector), but a defensive guard here means an
            # unexpected crash still tells the user something failed instead of
            # vanishing into channel silence.
            logger.exception(
                f"{type(self).__name__}[{agent_id}] run_and_collect raised: "
                f"{type(e).__name__}: {e}"
            )
            from narranexus.platform.agent_runtime.run_collector import RunError
            err_text = self.format_error_reply(
                RunError(error_type=type(e).__name__, error_message=str(e))
            )
            await self._send_error_fallback(
                credential, message, err_text, already_replied=False
            )
            return ChannelTurnOutput(err_text)
        finally:
            await self._release_unsettled_probe(agent_id, admission)

        if result.is_error:
            logger.warning(
                f"{type(self).__name__}[{agent_id}] runtime error "
                f"({result.error.error_type}): {result.error.error_message}"
            )
            err_text = self.format_error_reply(result.error)
            # Distinguish "agent failed" from "agent stayed silent": if the
            # agent already sent a real reply via its own tool before the
            # failure (partial_reply_then_error), the user has heard from it —
            # don't double-message. Otherwise the turn vanished silently, so
            # surface the error into the channel. A run that stayed silent by
            # CHOICE never sets is_error, so this never fires on intended
            # silence (group non-@ / nothing to add).
            sent = self.resolve_agent_response(result, message, credential)
            already_replied = bool(sent and sent.strip()) and sent != CHANNEL_SILENT_SENTINEL
            await self._send_error_fallback(
                credential, message, err_text, already_replied=already_replied
            )
            return ChannelTurnOutput(err_text)

        # Subclasses may want to extract platform-specific tool-call output;
        # default returns the agent's text. Lark's subclass will override
        # to look at result.raw_items in Phase 2.
        return ChannelTurnOutput(self.resolve_agent_response(result, message, credential))

    async def _build_and_run_agent_silent_batch(
        self,
        credential: Any,
        messages: List[ParsedMessage],
        sender_name_by_id: Optional[dict[str, str]] = None,
        *,
        attachments_by_index: Optional[List[List[Attachment]]] = None,
    ) -> Optional[str]:
        """Run one silent AgentRuntime pass over a batch of messages.

        Purpose. Group non-@ messages (and reconnect burst backfill) should
        land in memory — chat_history, observations, entity_description —
        WITHOUT calling the agent LLM. This method merges the batch into a
        single input_content, invokes AgentRuntime with silent=True, and
        passes per-message metadata through trigger_extra_data so
        ChatModule can write one user row per original event.

        Runtime shape:
        - Narrative selection runs once on the merged content (topic
          centroid). Modules load; instances sync.
        - step_3 (agent LLM) is skipped.
        - persist_turn writes N user rows (from batch_messages) with
          no assistant row.
        - after_turn runs (observation extraction from
          the merged content; entity update per unique sender).

        Args:
            credential: Loaded credential row (must have agent_id).
            messages: Non-empty list of ParsedMessage in chronological
                order, sharing the same chat_id.
            sender_name_by_id: Pre-resolved display names keyed by
                sender_id. Missing entries fall back to resolve_sender_name.
            attachments_by_index: Optional parallel list — attachments[i]
                is the list returned by fetch_attachments(messages[i]).
                Serialised into batch_messages[i]["attachments"] so
                ChatModule can persist them on the individual user row.

        Not gated by the agent circuit-breaker (#394 fifth review N-1,
        sixth review I-2). The breaker guards turns that spend the
        agent-slot credential, and ``silent=True`` runs ``SilentAct`` —
        step_3 is skipped, so the pass starts no such turn (zero agent LLM
        calls). The pass has no retry or catch-up queue, so skipping it in
        any breaker state would lose the room's messages from memory for
        good. Its memory/narrative LLM calls go through the helper slot,
        which in the default one-key setup is the SAME credential the
        breaker may be holding — during a pause those calls can fail too;
        helper-slot health is the helper side's concern, not this
        breaker's. It never claims or settles the half-open probe: no
        ``probe_token`` is passed.

        Returns:
            None when the pass ran, else a stable short code for why it did
            not (``SILENT_BATCH_EMPTY`` / ``SILENT_BATCH_RUNTIME_RAISED`` /
            ``SILENT_BATCH_RUNTIME_ERROR``) — the managed receipt reports
            it. Exception classes and error types go to the log only. Silent runs produce no user-facing text; the caller
            should NOT send anything to the IM platform.
        """
        if not messages:
            logger.warning(
                f"{type(self).__name__}._build_and_run_agent_silent_batch called with empty batch"
            )
            return SILENT_BATCH_EMPTY

        from narranexus.platform.agent_runtime.client import (
            get_agent_runtime_client,
        )

        agent_id = getattr(credential, "agent_id", "")
        anchor_msg = messages[-1]
        chat_id = anchor_msg.chat_id

        # Resolve display names for every unique sender in the batch,
        # falling back to resolve_sender_name for any not pre-supplied.
        # sender_name_by_id may be partial; sanitize whatever we end up with.
        resolved_names: dict[str, str] = dict(sender_name_by_id or {})
        for m in messages:
            sid = m.sender_id or ""
            if not sid or sid in resolved_names:
                continue
            name = m.sender_name
            if (not name or name == "Unknown") and sid:
                name = await self.resolve_sender_name(sid, credential)
            resolved_names[sid] = self.sanitize_display_name(name or sid)

        # Merge batch into a single input_content. Each line carries
        # timestamp + sender name + body so narrative selection's embedding
        # sees the actual dialogue shape, not just a naked concatenation.
        # The merged string ALSO becomes hook_after's user_input for
        # observation extraction — GeneralMemoryModule will read it as
        # "the users said X, Y, Z". That's the desired behaviour: silent
        # observation sink treats the batch as one conversational chunk.
        merged_lines: List[str] = []
        for m in messages:
            display = resolved_names.get(m.sender_id or "", m.sender_id or "unknown")
            ts_ms = getattr(m, "timestamp_ms", 0) or 0
            body = (m.content or "").strip()
            if not body:
                continue
            merged_lines.append(f"[{ts_ms}] {display}: {body}")
        merged_input = "\n".join(merged_lines) if merged_lines else (anchor_msg.content or "")

        # Build per-message metadata for ChatModule's batch write path.
        # Each entry is written as one user row keyed by its own event_id
        # + timestamp + sender_id, so chat_history shows the group thread
        # with per-speaker attribution.
        batch_messages: List[dict[str, Any]] = []
        for idx, m in enumerate(messages):
            iso_ts = ""
            ts_ms = getattr(m, "timestamp_ms", 0) or 0
            if ts_ms:
                try:
                    from datetime import datetime, timezone
                    iso_ts = datetime.fromtimestamp(
                        ts_ms / 1000.0, tz=timezone.utc
                    ).isoformat()
                except (ValueError, OSError):
                    iso_ts = ""
            sid = m.sender_id or ""
            entry: dict[str, Any] = {
                "event_id": m.message_id,
                "timestamp": iso_ts,
                "sender_id": sid,
                "sender_name": resolved_names.get(sid, sid),
                "content": m.content or "",
            }
            if attachments_by_index and idx < len(attachments_by_index):
                atts = attachments_by_index[idx] or []
                if atts:
                    entry["attachments"] = [a.model_dump(mode="json") for a in atts]
            batch_messages.append(entry)

        # ChannelTag anchors on the LAST message (most recent = strongest
        # topic signal); narrative routing uses this as the retrieval
        # anchor rather than embedding the whole merged blob.
        anchor_sender_name = resolved_names.get(
            anchor_msg.sender_id or "", anchor_msg.sender_id or ""
        )
        channel_tag = ChannelTag(
            channel=self.channel_name,
            sender_name=anchor_sender_name,
            sender_id=anchor_msg.sender_id,
            room_id=chat_id,
            is_agent_peer=self.is_agent_peer(anchor_msg),
        )

        owner_user_id = await self._resolve_agent_owner(agent_id) or agent_id

        # No context builder on this path (the batch is merged text, not a
        # single inbound message), so no turn envelope → step_3 treats the
        # turn as non-DM and runs no fallback. Correct here: a silent batch
        # run is explicitly not answering anyone (`silent=True` below).
        extra_data = self.build_trigger_extra_data(
            channel_tag=channel_tag,
            retrieval_anchor=(anchor_msg.content or "").strip() or None,
            trigger_id=(
                f"{self.channel_name}_batch_{anchor_msg.message_id}"
                if anchor_msg.message_id
                else f"{self.channel_name}_batch_unknown"
            ),
            batch_messages=batch_messages,
            silent_batch_size=len(batch_messages),
        )

        try:
            result = await get_agent_runtime_client().run_and_collect(
                agent_id=agent_id,
                user_id=owner_user_id,
                input_content=merged_input,
                working_source=self.working_source,
                trigger_extra_data=extra_data,
                silent=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"{type(self).__name__}[{agent_id}] silent batch runtime raised: "
                f"{type(e).__name__}: {e}"
            )
            return SILENT_BATCH_RUNTIME_RAISED

        if result.is_error:
            logger.warning(
                f"{type(self).__name__}[{agent_id}] silent batch runtime error "
                f"({result.error.error_type}): {result.error.error_message}"
            )
            return SILENT_BATCH_RUNTIME_ERROR
        return None

    # ────────────────────────────────────────────────────────────────────
    # Owner resolution + agent output extraction (subclass override hooks)
    # ────────────────────────────────────────────────────────────────────

    async def _resolve_agent_owner(self, agent_id: str) -> Optional[str]:
        """Look up the agent's owner user_id from the agents table.
        Returns "" when the agent is unknown, None when the lookup failed
        (resolve_owner's split) — callers gate on truthiness / `or agent_id`,
        so both degrade identically.
        Delegates to the shared AgentRepository.resolve_owner seam so
        ownership semantics have one home."""
        if self._db is None or not agent_id:
            return ""
        from narranexus.platform.repository.agent_repository import AgentRepository
        return await AgentRepository(self._db).resolve_owner(agent_id)

    # ────────────────────────────────────────────────────────────────────
    # Agent circuit-breaker gate (shared by every channel turn entry)
    # ────────────────────────────────────────────────────────────────────

    async def _breaker_db(self) -> Any:
        """The DB client the breaker reads through: the trigger's own when
        it has one, else the process client."""
        if self._db is not None:
            return self._db
        from narranexus.platform.utils.db.db_factory import get_db_client

        return await get_db_client()

    async def _circuit_admission(
        self, credential: Any, message: ParsedMessage, agent_id: str
    ) -> "tuple[TurnAdmission, Optional[str]]":
        """The agent circuit-breaker's two-step gate for a channel turn that
        is about to start (``circuit_breaker.admit_turn``).

        Call it immediately before the runtime call, after every branch
        that returns without a turn. Returns ``(admission, refusal)``:
        ``refusal`` is None when the turn may run — hand
        ``admission.probe_token`` to the runtime call (it settles the
        probe) and call ``_release_unsettled_probe`` on the way out. When
        refused, the person is told in the chat (never a silent drop) with
        ``format_circuit_refusal`` — a group chat once per breaker window,
        see ``_claim_circuit_refusal_send`` — and ``refusal`` is that text,
        which the caller returns as the turn's output (the inbox records it).
        Refused messages are not retried: a paused agent's retry storm is
        exactly what the breaker exists to stop.
        """
        from narranexus.platform.agent_framework.loop.circuit_breaker import (
            admit_turn,
        )

        admission = await admit_turn(agent_id, db=await self._breaker_db())
        if admission.allowed:
            return admission, None
        logger.info(
            f"{type(self).__name__}[{agent_id}] turn refused by the agent "
            f"circuit-breaker ({admission.reason})"
        )
        refusal = self.format_circuit_refusal(admission.reason)
        claim = self._claim_circuit_refusal_send(message, agent_id, admission.window)
        if claim is None:
            logger.info(
                f"{type(self).__name__}[{agent_id}] refusal reply to "
                f"{message.chat_id} throttled (already claimed in this "
                f"window: {admission.window})"
            )
        elif not await self._send_circuit_refusal(credential, message, refusal):
            self._unclaim_circuit_refusal_send(claim)
        return admission, refusal

    def _claim_circuit_refusal_send(
        self, message: ParsedMessage, agent_id: str, window: Optional[str]
    ) -> Optional[RefusalClaim]:
        """Claim this refusal as the one the chat hears in the current
        breaker window (#394 fourth review I-C). Returns the claim (hand it
        to ``_unclaim_circuit_refusal_send`` if the send fails), or None
        when the chat was already told in this window.

        A group chat hears the refusal once per (channel, agent, chat) per
        breaker window. ``window`` is ``TurnAdmission.window``: the row's
        ``cb_status`` + ``cooldown_until`` from the same read that refused
        the turn (#394 fifth review M-2 — no second read), so a new pause
        (repaired, then broken again) or a doubled backoff after a failed
        probe is a new window and is told again. A 1:1 chat is never
        throttled: its only sender needs the answer. Only the SEND is
        throttled — the caller still returns the refusal text as the
        turn's output, so the inbox records it.

        Claim first, roll back on a failed send (#394 fifth review M-1):
        check-and-set has no ``await`` in between, so concurrent messages
        never both send; a send the channel reports as failed hands the
        window back and the next message in the chat tries again. Messages
        that arrived while that failed send was in flight stay untold.
        Process-local on purpose: one channel's triggers run in one process
        (the channels supervisor); a lost entry costs one repeated notice.
        No window (the refusal carries none) sends — fail toward telling."""
        key = (self.channel_name, agent_id, message.chat_id or "")
        is_group = getattr(message, "chat_type", None) in (
            ChatType.GROUP, ChatType.TOPIC_GROUP,
        )
        if not is_group or window is None:
            return RefusalClaim(key, None)
        sent = ChannelTriggerBase._circuit_refusal_windows
        if sent.get(key) == window:
            return None
        sent[key] = window
        sent.move_to_end(key)
        while len(sent) > self._CIRCUIT_REFUSAL_WINDOWS_MAX:
            sent.popitem(last=False)
        return RefusalClaim(key, window)

    @staticmethod
    def _unclaim_circuit_refusal_send(
        claim: RefusalClaim,
    ) -> None:
        """Hand back a refusal-window claim whose send failed — only if the
        entry is still this claim's window (a newer window stays)."""
        key, window = claim.key, claim.window
        if window is None:
            return
        sent = ChannelTriggerBase._circuit_refusal_windows
        if sent.get(key) == window:
            del sent[key]

    async def _send_circuit_refusal(
        self, credential: Any, message: ParsedMessage, text: str
    ) -> bool:
        """Deliver a circuit-breaker refusal to the chat; True iff the
        channel accepted it. Default: the channel's ``send_channel_reply``
        (a raise is a failed send, logged and swallowed). A channel whose
        ``send_channel_reply`` is a no-op (NarraMessenger) overrides this
        with its own sender."""
        try:
            await self.send_channel_reply(credential, message, text)
        except Exception as e:  # noqa: BLE001 — never break the turn path
            logger.warning(
                f"{type(self).__name__}: circuit refusal send failed: "
                f"{type(e).__name__}: {e}"
            )
            return False
        return True

    async def _release_unsettled_probe(
        self, agent_id: str, admission: "TurnAdmission"
    ) -> None:
        """Exit belt for an admitted turn: hand back a probe claim the
        runtime call never settled (it raised before reaching its own
        settlement). A no-op without a token and once settled — the
        release is a token CAS."""
        if admission.probe_token is None:
            return
        from narranexus.platform.agent_framework.loop.circuit_breaker import (
            release_probe,
        )

        await release_probe(agent_id, admission.probe_token, db=await self._breaker_db())

    def format_circuit_refusal(self, reason: Optional[str]) -> str:
        """The chat reply for a turn the agent circuit-breaker refused.

        Addressed to whoever wrote in — often not the owner, so it names
        no remedy (the owner was alerted when the agent paused and fixes
        it in Settings). A pause reads as "can't reply right now"; cooling
        and a probe in flight read as "try again shortly"."""
        if (reason or "").startswith("paused"):
            return (
                "⚠️ This agent is paused and can't reply right now. "
                "Please contact the bot's owner."
            )
        return (
            "⚠️ This agent is briefly unavailable. Please try again shortly."
        )

    def format_error_reply(self, error: "RunError") -> str:
        """
        Render an AgentRuntime failure as a user-facing IM reply.
        Default returns a generic apology. Subclasses may override per-channel.
        """
        return (
            "⚠️ I hit an internal error and can't reply to this message. "
            "Please try again in a bit, or contact the bot's owner."
        )

    async def send_channel_reply(
        self, credential: Any, message: ParsedMessage, text: str
    ) -> None:
        """Send ``text`` into the originating chat OUTSIDE the agent's own
        reply tool.

        Used by the error-fallback path (:meth:`_send_error_fallback`) so a run
        that failed before the agent could call its reply tool still surfaces
        *something* to the user — distinguishing "agent failed" from "agent
        stayed silent". IM subclasses override this with the SDK client they
        already hold (keyed by subscriber), addressing the reply via
        ``message.chat_id`` / ``message.sender_id`` / ``message.raw``.

        Default: no-op. Channels that must not be sent to from the trigger
        (message_bus stays quiet, job has its own delivery) simply inherit it.
        """
        return None

    def managed_reply_kwargs(self, trigger_extra_data: dict) -> dict:
        """Channel-specific delivery kwargs for a MANAGED turn's envelope.

        Native turns get ``channel_reply_kwargs`` from the context
        builder's ``reply_kwargs()``; managed turns never run a builder,
        so the ingress coordinator asks the trigger instead (it knows its
        channel's addressing, the coordinator stays channel-agnostic).
        Default: empty — step_3's fallback then delivers with the
        channel_tag's ``room_id`` alone, which is right for room-addressed
        channels (matrix/telegram); token-addressed channels override
        (wechat needs the inbound ``reply_token``).
        """
        return {}

    async def _send_error_fallback(
        self,
        credential: Any,
        message: ParsedMessage,
        error_text: str,
        *,
        already_replied: bool,
    ) -> None:
        """Surface a run failure into the channel — unless the agent already
        replied this turn (then don't double-message).

        Best-effort: a send failure must not mask the original error or break
        inbox recording, so it is logged and swallowed.
        """
        if already_replied:
            return
        try:
            await self.send_channel_reply(credential, message, error_text)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"{type(self).__name__}: error-fallback send_channel_reply "
                f"failed: {type(e).__name__}: {e}"
            )

    @staticmethod
    def platform_written_reply(result: Any) -> str:
        """Reply text the PLATFORM wrote and delivered this turn, if any.

        `step_3`'s 1:1 DM no-reply fallback sends through
        `ChannelSenderRegistry` and records a synthetic tool-call frame
        carrying `PLATFORM_REPLY_TEXT_KEY`. That frame reaches the trigger
        via `result.raw_items`, but every channel's `extract_output` scrapes
        its own tool's argument shape and cannot recognise it:
        `_extract_wechat_reply` reads `arguments["text"]` and comes back
        empty, Lark's requires `+messages-send` inside `command`, and the
        rest are structurally identical. All of them then fall through to
        `CHANNEL_SILENT_SENTINEL`.

        That sentinel used to be what `InboxRecorder` persisted as the
        turn's outbound row — so a reply that really was delivered got
        recorded as "(stayed silent)". On WeChat it is worse than a cosmetic
        record: `WeChatContextBuilder.get_conversation_history` reads recent
        turns back out of `inbox_thread_messages`, so the NEXT turn's
        Conversation History showed the bot saying "(stayed silent)" — the
        same placeholder-poisons-the-context failure this change removes one
        layer up.

        Closed on 2026-08-18 at the source: the managed call site passes the
        reply text as-is, and an empty one writes no outbound row at all (the
        recorder's documented contract, and what the other call site always
        did). The sentinel now lives only on the send path's
        `already_replied` comparisons, where nobody reads it back.

        Checked BEFORE `extract_output` for exactly the reason the handler
        layer checks it before `extract_reply_fn`: this text is
        authoritative because we wrote it. Teaching each channel's
        extractor about the key is the approach this change already
        rejected once.
        """
        for raw in getattr(result, "raw_items", []) or []:
            if not isinstance(raw, dict):
                continue
            item = raw.get("item", {})
            if not isinstance(item, dict) or item.get("type") != "tool_call_item":
                continue
            args: Any = item.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (ValueError, TypeError):
                    continue
            if not isinstance(args, dict):
                continue
            text = args.get(PLATFORM_REPLY_TEXT_KEY)
            if isinstance(text, str) and text.strip():
                return text
        return ""

    def resolve_agent_response(
        self, result, message: ParsedMessage, credential: Any
    ) -> str:
        """What this turn said, for the inbox record.

        Platform-written replies win; otherwise the channel's own
        `extract_output` runs unchanged. Keeping the precedence here means
        no channel subclass has to know the platform-reply key exists.
        """
        platform = self.platform_written_reply(result)
        if platform:
            logger.info(
                f"{type(self).__name__}: recording platform-written reply "
                f"({len(platform)} chars) for the inbox"
            )
            return platform
        return self.extract_output(result, message, credential)

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
    # Managed-ingress seams (platform-held connection; start() never runs)
    # ────────────────────────────────────────────────────────────────────
    #
    # Under Manyfold managed mode the platform owns the connection and the
    # cleaning pipeline (dedup / echo / mention gate) and forwards inbound
    # IM as chat turns (backend/routes/openai_compat.py). These seams let
    # that ingress path reuse the trigger's per-message BUSINESS hooks —
    # owner claim, authorization, inbox write, error fallback — without
    # running the subscribe loops: the ingress constructs the trigger,
    # never calls start(), and invokes before/after around the agent run.
    # Design: specs/2026-08-03-manyfold-managed-im-ingress-design.md §3.2.

    def _managed_bind(self, db) -> None:
        """Adopt a db handle + audit repo outside start() (managed mode).
        Mirrors start()'s first steps for the state the business hooks
        read; idempotent."""
        self._db = db
        if self._audit_repo is None:
            self._audit_repo = ChannelTriggerAuditRepository(self.channel_name, db)

    async def _credential_for_agent(self, agent_id: str) -> Optional[Any]:
        """First active credential bound to ``agent_id``, or None."""
        try:
            creds = await self.load_active_credentials()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"{type(self).__name__}: load_active_credentials failed for "
                f"managed ingress ({type(e).__name__}: {e})"
            )
            return None
        for cred in creds or []:
            if getattr(cred, "agent_id", None) == agent_id:
                return cred
        return None

    async def managed_before_run(
        self,
        *,
        agent_id: str,
        message: ParsedMessage,
        db: Any,
        is_mention: bool = True,
    ) -> tuple[bool, str]:
        """Business gate before a managed (platform-forwarded) run.

        Default: allow. Channel subclasses override for the inbound side
        effects that natively live in their ``_process_message`` (WeChat
        owner claim, NarraMessenger authorization). Returning
        ``(False, receipt)`` blocks the agent run; the receipt is streamed
        back to the platform transcript."""
        self._managed_bind(db)
        return True, ""

    async def managed_after_run(
        self,
        *,
        agent_id: str,
        message: ParsedMessage,
        db: Any,
        reply_text: str,
        error_text: str = "",
        audit_details: Optional[dict] = None,
    ) -> None:
        """Post-run bookkeeping for a managed inbound: error fallback when
        the run failed before any reply, then the native inbox write and a
        minimal audit row. Best-effort throughout — this runs after the
        reply stream closed and must never raise.

        ``audit_details`` carries turn facts only the completions endpoint
        knows (route, duration) into the ``managed_ingress_processed``
        row — merged under the caller's keys so this method's own
        ``replied`` / ``error`` stay authoritative."""
        self._managed_bind(db)
        replied = bool((reply_text or "").strip())
        if error_text and not replied:
            credential = await self._credential_for_agent(agent_id)
            if credential is not None:
                from narranexus.platform.agent_runtime.run_collector import RunError

                await self._send_error_fallback(
                    credential,
                    message,
                    self.format_error_reply(
                        RunError(
                            error_type="managed_run_failed",
                            error_message=error_text,
                        )
                    ),
                    already_replied=False,
                )
        try:
            await self._inbox_recorder.record_turn(
                db=db,
                thread_id=im_thread_id(self.channel_name, agent_id, message.chat_id),
                chat_id=message.chat_id,
                chat_type=message.chat_type,
                owner_user_id=await resolve_owner_for_agent(db, agent_id),
                agent_id=agent_id,
                counterpart_id=message.sender_id,
                # Sanitized like the other two record_turn sites: a display name
                # is a counterpart-controlled string, and reach recording now
                # PERSISTS it into `entity_name` (rendered into context on every
                # future social search) — so the prompt-injection seam
                # `sanitize_display_name` guards must not be bypassed here.
                counterpart_name=self.sanitize_display_name(message.sender_name),
                inbound_text=message.content,
                # As-is, NOT `or CHANNEL_SILENT_SENTINEL`. The recorder's
                # contract is that an empty outbound writes no outbound row —
                # which is what the other call site relies on, and what the
                # sentinel defeated: it wrote `(stayed silent)` as an OUTBOUND
                # row attributed to the agent. Telegram and WeChat read
                # `inbox_thread_messages` as their conversation memory, so the
                # agent was handed that string back as its own previous reply,
                # which is exactly what the warning further up this file exists
                # to prevent. The sentinel's real job is elsewhere — the
                # `already_replied` comparisons on the send path — not in the
                # user's transcript.
                outbound_text=(reply_text or "").strip(),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"{type(self).__name__}: managed inbox write failed "
                f"({type(e).__name__}: {e})"
            )
        details = dict(audit_details or {})
        details["replied"] = replied
        # An audit row, so the audit cap. This text is the caller's
        # already-formatted run error (the same one format_error_reply sent
        # to the user); it deliberately does NOT go through safe_error_text,
        # whose 32+ char rule would mask run / message ids.
        details["error"] = (error_text or "")[:AUDIT_ERROR_MAX_CHARS]
        await self._audit(
            EVENT_MANAGED_INGRESS_PROCESSED,
            agent_id=agent_id,
            message_id=message.message_id,
            chat_id=message.chat_id,
            sender_id=message.sender_id,
            details=details,
        )

    async def managed_silent_ingest(
        self,
        *,
        agent_id: str,
        message: ParsedMessage,
        db: Any,
        attachments: Optional[List[Attachment]] = None,
    ) -> str:
        """Memory-only ingestion for a managed non-mention group message.

        Mirrors the native ``group_silent`` path: narrative routing + memory
        write run via ``_build_and_run_agent_silent_batch``, the agent LLM
        step is skipped, and NOTHING is sent to the room. Lives on the
        trigger (not the ingress coordinator) so the batch-call shape stays
        this class's private knowledge and a channel can override the
        behaviour (e.g. opt out of silent ingestion entirely). Returns a
        transcript receipt for the platform-facing completion that says
        what happened: "ingested" only when the silent pass ran."""
        self._managed_bind(db)
        credential = await self._credential_for_agent(agent_id)
        if credential is None:
            return "(silent group message dropped - no channel credential)"
        sender_names = (
            {message.sender_id: message.sender_name} if message.sender_id else None
        )
        skipped = await self._build_and_run_agent_silent_batch(
            credential,
            [message],
            sender_name_by_id=sender_names,
            attachments_by_index=[attachments] if attachments else None,
        )
        if skipped:
            return f"(silent group message not ingested - {skipped})"
        return "(silent group message ingested to memory - no reply)"

    # ────────────────────────────────────────────────────────────────────
    # Audit helpers
    # ────────────────────────────────────────────────────────────────────

    def _content_fingerprint(self, message: ParsedMessage) -> str:
        """Stable identity of a message independent of its platform id.

        Only computed when CONTENT_DEDUP_WINDOW_SECONDS opts the channel in;
        empty string disables the fingerprint layer for this message (e.g.
        no content to fingerprint).

        The hashing itself moved to ``ingress_guard.content_fingerprint`` so
        the dedup layer and the ingress breaker cannot drift into two
        definitions of "the same message". The opt-in gate stays HERE: it
        governs the dedup question ("did the platform re-dispatch this under
        a new id?"), which the breaker does not ask.
        """
        if self.CONTENT_DEDUP_WINDOW_SECONDS <= 0:
            return ""
        return content_fingerprint(
            message.chat_id, message.sender_id, message.content
        )

    # ────────────────────────────────────────────────────────────────────
    # Ingress circuit breaker
    # ────────────────────────────────────────────────────────────────────

    def build_ingress_guard(self, db: Any) -> IngressGuard:
        """Construct this channel's guard from the class-level tunables.

        PUBLIC on purpose, unlike most of this class's internals: the
        managed-mode coordinator — which never calls ``start()`` — builds
        its guard through this, so it is a cross-component contract like
        ``managed_before_run`` and the rest of that family. Named private,
        it reads to the next reader as a layering violation to be tidied
        away, and tidying it away silently removes the breaker from the
        managed surface.

        Existing so that coordinator does not hand-copy seven constants.
        """
        return IngressGuard(
            repo=ChannelIngressBreakerRepository(db) if db is not None else None,
            window_seconds=self.INGRESS_WINDOW_SECONDS,
            rate_threshold=self.INGRESS_RATE_THRESHOLD,
            dup_ratio_threshold=self.INGRESS_DUP_RATIO_THRESHOLD,
            agent_rate_threshold=self.INGRESS_AGENT_RATE_THRESHOLD,
            agent_dup_ratio_threshold=self.INGRESS_AGENT_DUP_RATIO_THRESHOLD,
            schedule_seconds=self.INGRESS_BREAKER_SCHEDULE_SECONDS,
            recovery_windows=self.INGRESS_RECOVERY_WINDOWS,
        )

    async def _ingress_admitted(self, credential: Any, message: ParsedMessage) -> bool:
        """THE ingress gate. Returns False when the caller must drop.

        Every receive path must route through this one method. There is no
        single chokepoint in the trigger layer to place it in — Lark owns
        its whole ``_process_message`` without calling ``super()``, and
        managed mode bypasses the subscriber path entirely — so the seam is
        the method rather than the location. ``test_ingress_guard_all_paths``
        holds that line; the equivalent copy-paste drift in
        ``build_trigger_extra_data`` is why that test style exists.

        Placed AFTER the unbound / echo / empty gates: an echo of our own
        reply or a payload we could not parse is not the peer talking, and
        counting it toward their rate would let the agent trip its own
        breaker.

        Never raises. The guard is not an authorization gate — if it breaks,
        traffic flows (contrast: narramessenger's managed authorize hook,
        which fails closed because it IS one).
        """
        guard = self._ingress_guard
        if guard is None:
            return True
        agent_id = getattr(credential, "agent_id", "")
        app_id = getattr(credential, "app_id", "")
        try:
            verdict = await guard.admit(
                agent_id=agent_id,
                channel=self.channel_name,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                fingerprint=content_fingerprint(
                    message.chat_id, message.sender_id, message.content
                ),
                is_agent_peer=self.is_agent_peer(message),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"{type(self).__name__}: ingress guard raised "
                f"({type(e).__name__}: {e}) — failing open"
            )
            return True
        await self._audit_ingress_verdict(verdict, message, agent_id, app_id)
        return verdict.admit

    async def _audit_ingress_verdict(
        self,
        verdict: IngressVerdict,
        message: ParsedMessage,
        agent_id: str,
        app_id: str,
    ) -> None:
        """One audit row per verdict that changed something or dropped a
        message. The quiet 'ok' case writes nothing — that is already
        covered by ``ingress_processed``."""
        event = verdict.audit_event()
        if event is None:
            return
        await self._audit(
            event,
            message_id=message.message_id,
            agent_id=agent_id,
            app_id=app_id,
            chat_id=message.chat_id,
            sender_id=message.sender_id,
            details=verdict.audit_details(),
        )

    async def _audit(self, event_type: str, **kwargs) -> None:
        if self._audit_repo is None:
            return
        await self._audit_repo.append(event_type, **kwargs)

    async def _on_unparsed(self, credential: Any, raw: dict) -> None:
        """Audit a raw event that ``parse_event`` rejected (unsupported type).

        Keys only, never payloads — enough to answer "the sticker you sent
        at 12:03 was dropped as unsupported" without shipping media bytes
        or message text into the audit table.
        """
        raw_keys = sorted(raw)[:10] if isinstance(raw, dict) else [type(raw).__name__]
        await self._audit(
            EVENT_INGRESS_DROPPED_UNPARSED,
            agent_id=getattr(credential, "agent_id", ""),
            app_id=getattr(credential, "app_id", ""),
            details={"raw_keys": raw_keys},
        )

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
            # Bound credentials deliberately NOT running. Carried on every
            # beat so isolation shows up as a standing state in the DB, not
            # only as the single row written when it tripped (lesson #4:
            # health has to reach L2, and "process up, queue empty" hides an
            # agent whose IM channel is quietly parked).
            "breaker_isolated_count": len(self._breaker_blocked_until),
            "unstartable_count": len(self._unstartable_fingerprint),
            # Conversations whose ingress is currently suppressed, and those
            # merely carrying escalation memory. Same reasoning as the line
            # above — the 8/14 loop burned 70 hours behind a green board, so
            # "we are ignoring someone right now" must be a standing number,
            # not an event you had to be watching for.
            "ingress_breaker_cooling_count": (
                self._ingress_guard.cooling_session_count()
                if self._ingress_guard else 0
            ),
            "ingress_breaker_open_count": (
                self._ingress_guard.open_session_count()
                if self._ingress_guard else 0
            ),
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

        if self._ingress_guard is not None and self._db is not None:
            try:
                # Only CLOSED rows age out — see the repository docstring.
                deleted = await ChannelIngressBreakerRepository(
                    self._db
                ).cleanup_older_than_days(
                    self.INGRESS_BREAKER_RETENTION_DAYS,
                    # Scoped, like the two sweeps above it. Unscoped, this
                    # class attribute would not be per-trigger at all: six
                    # native triggers in one process would each sweep the
                    # whole table, five of them redundantly, and the
                    # shortest retention would silently win for everyone.
                    channel=self.channel_name,
                )
                if deleted:
                    logger.info(
                        f"{type(self).__name__}: cleaned {deleted} closed ingress "
                        f"breaker rows older than "
                        f"{self.INGRESS_BREAKER_RETENTION_DAYS} days"
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"{type(self).__name__}: ingress breaker cleanup failed: {e}"
                )

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
            return UNKNOWN_SENDER_NAME
        cleaned = _CONTROL_CHARS_RE.sub(" ", name)
        cleaned = " ".join(cleaned.split())
        return cleaned[:128] or UNKNOWN_SENDER_NAME
