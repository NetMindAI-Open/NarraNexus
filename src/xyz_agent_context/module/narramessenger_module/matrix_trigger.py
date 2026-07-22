"""
@file_name: matrix_trigger.py
@date: 2026-07-02
@description: NarraMessenger MatrixTrigger — replaces the polling long-poll
              on the message plane with a real Matrix client (matrix-nio)
              talking to matrix.netmind.chat directly.

Scope of THIS FILE (Phase 1, Commits 3 + 4b + 5 + 6 + 7):
  ✓  Class + ChannelTriggerBase wiring, all abstract methods present
  ✓  ``connect()`` implements the sync loop with the strict cursor-save
     ordering (see :meth:`connect` docstring)
  ✓  ``parse_event()`` handles ``m.room.message`` (text)
  ✓  ``is_echo()`` filters our own agent's messages back out of the
     sync stream
  ✓  Auto-persist ``device_id`` on first sync (matrix-nio populates
     ``client.device_id`` after the server ack)
  ✓  (4b) Room member count + display name caches populated by
     ``m.room.member`` state events flowing through the sync loop
  ✓  (4b) DM / group_mention / group_silent classification, with
     mention detection covering MSC3952 intentional mentions, raw
     MXID inline, and ``@displayname`` inline
  ✓  (4b) Silent-batch buffer + debounce (5s idle OR 20 msgs) → hands
     off to :meth:`ChannelTriggerBase._build_and_run_agent_silent_batch`
     from Commit 4a; drained on reconnect burst AND on ``stop()``
  ✓  (4b) Reply sending via ``client.room_send`` with M_LIMIT_EXCEEDED
     retry-after honoring and permanent-auth-failure short-circuit;
     transient failures logged to ``EVENT_TRANSPORT_SEND_FAILED`` audit
  ✓  (4b) ``extract_output()`` reads
     ``send_message_to_user_directly`` tool call args — no more
     accidental agent-thinking spill into the room
  ✓  (4b) Silent-not-reply fix: agent that skips the reply tool sends
     nothing
  ✓  (5)  Narra ``authorize-event`` gate before every event; deny +
     notice forwards ``m.notice`` back to the room, else silent drop
  ✓  (6)  Auto-join invited rooms during the sync loop
     (``autoJoin: always`` per NarraMessenger's OpenClaw config)

  ✓  (Phase 3) Multimodal: ``m.image`` / ``m.file`` / ``m.audio`` /
     ``m.video`` → ``_wrap_event`` marshals the mxc URI, ``parse_event``
     populates ``attachment_refs``, ``fetch_attachments`` downloads via
     the authenticated ``/_matrix/client/v1/media/download`` endpoint and
     hands bytes to the base ``_persist_attachment`` (workspace store +
     MIME sniff + audio STT). The agent reads the file via its Read tool
     at the path the ``Attachment`` marker announces.

Deferred to Phase 4:
  ✗  Progressive update via ``m.replace``
  ✗  Typing indicator via ``PUT /rooms/{room}/typing/{user}``
  ✗  ``NarramessengerContextBuilder`` adapting to matrix event shape
     (currently shared with the polling trigger — good enough for
     text-only Phase 1)

Design ref: [[Work/Narranexus/2026-07-02 NarraMessenger Matrix Adapter spec]]
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, List, Literal, Optional

import aiohttp
from loguru import logger

# matrix-nio is added as a hard dep in ``pyproject.toml`` (2026-07-02).
# Non-``[e2e]`` variant on purpose — matrix.netmind.chat rooms are
# plaintext-by-default (verified 2026-07-02), so we skip libolm and its
# native-C dependency.
from nio import (
    AsyncClient,
    AsyncClientConfig,
    LocalProtocolError,
    RoomMemberEvent,
    RoomMessageMedia,
    RoomMessageText,
    RoomSendError,
    RoomSendResponse,
    SyncResponse,
)

from xyz_agent_context.channel.channel_audit_events import (
    EVENT_ATTACHMENT_FETCH_FAILED,
    EVENT_ATTACHMENT_PERSISTED,
    EVENT_INGRESS_DROPPED_OVERSIZED,
    EVENT_TRANSPORT_SEND_FAILED,
)
from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
    ChannelHistoryConfig,
)
from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
from xyz_agent_context.schema.attachment_schema import Attachment
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)
from xyz_agent_context.schema.runtime_message import MessageType

from ._matrix_send import (
    MatrixSendError,
    matrix_room_send,
)
from ._narramessenger_credential_manager import (
    NarramessengerCredential,
    NarramessengerCredentialManager,
)
from .narramessenger_context_builder import NarramessengerContextBuilder


_ClassifyTarget = Literal["dm", "group_mention", "group_silent"]


@dataclass
class _StreamReplyState:
    """Streaming reply state for one agent turn.

    2026-07-08 UX refactor: no placeholder, no progressive edits. The
    trigger accumulates ``narra_reply.text`` from the tool call stream,
    plus any error signal, and decides at finalize whether to fresh-send
    a reply, a fresh error marker, or nothing. State lives for one turn;
    no cross-turn shared mutable state.

    - ``narra_reply_text`` — the reply the agent produced, empty if silent.
    - ``error_seen`` — set True by ``_handle_stream_event`` on any
      ``MessageType.ERROR`` OR by the outer stream try/except catching a
      Python exception. Signals finalize to surface ``STREAM_ERROR_MARKER``.
    - ``last_error_message`` — short excerpt for the log line only;
      never rendered in the room (which just gets the generic marker).
    """
    narra_reply_text: str = ""
    error_seen: bool = False
    last_error_message: str = ""

# Authenticated Matrix media download (MSC3916 / Matrix 1.11). The room's
# homeserver requires a bearer token on media fetches — the legacy
# unauthenticated ``/_matrix/media/r0/download`` path is gone on
# matrix.netmind.chat (verified 2026-06-30). Format-filled with
# ``{server_name}`` + ``{media_id}`` parsed from the ``mxc://`` URI.
_MEDIA_DOWNLOAD_PATH = "/_matrix/client/v1/media/download/{server_name}/{media_id}"


class MatrixMediaError(Exception):
    """Raised by ``_download_mxc`` on a failed / oversized media fetch.

    ``code`` distinguishes the failure so ``fetch_attachments`` can audit
    ``EVENT_INGRESS_DROPPED_OVERSIZED`` (platform/size cap) apart from
    ``EVENT_ATTACHMENT_FETCH_FAILED`` (transport / HTTP error), mirroring
    the ``DiscordSDKError`` code split.
    """

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def _media_content_type(msgtype: str, mimetype: str) -> "MessageContentType":
    """Coarse content_type for an attachment — msgtype first, MIME family
    fallback. Shared by the inline-media and compound parse paths so they
    agree. Only a UX hint; the agent's Read tool re-validates the bytes."""
    if msgtype == "m.image" or mimetype.startswith("image/"):
        return MessageContentType.IMAGE
    if msgtype == "m.audio" or mimetype.startswith("audio/"):
        return MessageContentType.AUDIO
    if msgtype == "m.video" or mimetype.startswith("video/"):
        return MessageContentType.VIDEO
    return MessageContentType.FILE


def _parse_mxc(mxc_url: str) -> tuple[str, str]:
    """Split ``mxc://{server_name}/{media_id}`` into its parts.

    Returns ``("", "")`` for anything that isn't a well-formed mxc URI so
    the caller can audit + skip rather than build a broken download URL.
    """
    if not mxc_url.startswith("mxc://"):
        return "", ""
    rest = mxc_url[len("mxc://"):]
    server_name, _, media_id = rest.partition("/")
    if not server_name or not media_id:
        return "", ""
    return server_name, media_id


@dataclass(frozen=True)
class _AuthorizeVerdict:
    """Result of the Narra ``authorize-event`` gate for one Matrix event.

    - ``allow=True`` → proceed with normal handling (silent OR full).
    - ``allow=False`` + ``notice_send=True`` → send ``notice_text`` back
      as an ``m.notice`` (server-suggested denial message) then stop.
    - ``allow=False`` + ``notice_send=False`` → drop silently. This is
      the fail-closed default for HTTP errors, timeouts, invalid JSON,
      and any ``allow != true`` response without a notice payload.
    """
    allow: bool
    notice_send: bool = False
    notice_text: Optional[str] = None


class MatrixTrigger(ChannelTriggerBase):
    """NarraMessenger channel trigger — Matrix client transport.

    Sole NarraMessenger trigger as of Commit 7 (2026-07-02); the legacy
    Gateway/polling trigger was deleted in the same commit. NarraMessenger
    hosts a Matrix homeserver at ``matrix.netmind.chat`` and its setup
    guide explicitly designates Direct Matrix as the default bind path.
    """

    # ── ChannelTriggerBase contract ───────────────────────────────────────
    channel_name = "narramessenger"
    brand_display = "NarraMessenger"
    working_source = WorkingSource.NARRAMESSENGER

    # ── Worker pool ──────────────────────────────────────────────────────
    # Concurrency tuning inherited from the pre-Matrix era; migration
    # kept identical characteristics so throughput per agent is unchanged.
    MIN_WORKERS = 3
    WORKERS_PER_SUBSCRIBER = 2
    MAX_WORKERS = 50
    PROCESS_MESSAGE_TIMEOUT_SECONDS = 1800
    CLEANUP_INTERVAL_SECONDS = 24 * 3600
    HEARTBEAT_INTERVAL_SECONDS = 600
    DEDUP_RETENTION_DAYS = 7
    AUDIT_RETENTION_DAYS = 30

    DEDUP_TTL_SECONDS = 600
    HISTORY_BUFFER_MS = 5 * 60 * 1000

    DEBOUNCE_WINDOW_MS = 0

    # ── Matrix sync tuning ───────────────────────────────────────────────
    # Server long-poll timeout. Synapse holds up to 30s idle; new events
    # flush earlier. Full events, not lazy_load_members — the room member
    # list is needed for mention-vs-DM routing (Commit 4).
    SYNC_TIMEOUT_MS = 30000
    # Initial sync (empty since token) — full state might be large on
    # bounded but active accounts; a short server timeout prevents the
    # first connect from hanging on a quiet server.
    FIRST_SYNC_TIMEOUT_MS = 5000

    # ── Silent-batch buffering ───────────────────────────────────────────
    # Group non-@ messages accumulate in a per-(agent, room) buffer and
    # flush on N seconds idle OR N messages, whichever comes first. Values
    # kept modest so the memory-write side (chat_history, observations,
    # entity descriptions) is never more than N seconds behind reality.
    SILENT_DEBOUNCE_SECONDS = 5.0
    SILENT_FLUSH_BURST_SIZE = 20

    # ── Reply sender retry policy ────────────────────────────────────────
    # room_send failures come in three shapes: (a) M_LIMIT_EXCEEDED
    # (transient, server tells us retry_after_ms), (b) auth failure
    # (M_UNKNOWN_TOKEN etc — permanent, the base's watcher already flips
    # enabled=False on next sync tick when it sees the same code), (c)
    # network / server 5xx (retriable with exponential backoff). We cap
    # at 3 attempts total so a legitimately down homeserver doesn't hold
    # a worker for minutes; the missed reply lands in audit and the owner
    # can see it after the fact.
    SEND_MAX_ATTEMPTS = 3
    SEND_INITIAL_BACKOFF_MS = 500

    # ── Silent-first streaming (no placeholder) ──────────────────────────
    # 2026-07-08 UX refactor: the "💭 Thinking…" placeholder is gone.
    # Behaviour matrix:
    #   - Agent calls ``narra_reply(text)`` → we ``room_send`` the text as
    #     a fresh message when the turn ends. One message, no edit dance.
    #   - Agent stays silent (no ``narra_reply``) → the room stays empty.
    #     No dot marker, no redact, no "message deleted".
    #   - Agent's runtime crashes mid-turn (``MessageType.ERROR`` or the
    #     outer try/except catches a Python exception) → we ``room_send``
    #     ``STREAM_ERROR_MARKER`` fresh. Failures MUST stay visible so the
    #     sender knows to retry rather than assume they were ignored.
    #   - Agent calls ``narra_progress(text)`` → NO-OP on the room. The
    #     tool still exists and returns ``{"ok": true}`` so agent prompts
    #     don't have to change, but the trigger no longer edits anything.
    #     Kept as a "backend log only" signal in case we want to surface
    #     it elsewhere (analytics, owner dashboard) later.
    #
    # Why gone: the placeholder-then-silent-marker dance ("Thinking…" then
    # "·") looked broken to real users on dev — worse UX than just staying
    # silent when there's nothing to say. Deferred/lazy placeholder was
    # explored but our typical agent turn is 20-60s (see the timing data
    # from 2026-07-08), so no reasonable delay window skips the placeholder
    # on the silent path without also delaying it on the reply path.
    #
    # Kill switch: flip ``STREAMING_ENABLED`` to False to fall back to the
    # atomic path (run_and_collect → final text → one room_send).
    STREAMING_ENABLED = True
    # Marker fresh-sent when the agent's runtime emitted an ``ErrorMessage``
    # during the stream, or the outer try/except caught a Python-level
    # exception. Tells the sender to retry rather than assume they were
    # ignored. ``LineTooLong`` on oversized `tool_call_output_item` is a
    # real failure mode we've observed (128 KiB anyio line limit + Claude
    # Code's Read on media returning base64 > 128 KiB).
    STREAM_ERROR_MARKER = (
        "⚠️ Sorry — I hit an internal error and couldn't finish. "
        "Please try again in a moment."
    )

    # ── Narra authorize-event gate ───────────────────────────────────────
    # Every Matrix event must clear ``POST /api/agent-runtime/matrix/
    # authorize-event`` before we read history, write memory, invoke
    # tools, call the model, or send a Matrix reply. Fail-closed on
    # ANY non-2xx / invalid-JSON / timeout / ``allow != true`` — those
    # all count as "do not process". 401 during a pending bind is
    # expected fail-closed behaviour, NOT a signal to disable the
    # credential (the base's is_permanent_auth_failure only fires on
    # Matrix auth codes, not Narra ones).
    AUTHORIZE_EVENT_TIMEOUT_SECONDS = 10.0
    AUTHORIZE_EVENT_PATH = "/api/agent-runtime/matrix/authorize-event"

    # ── Silent-path authorize bypass (owner override, 2026-07-02) ────────
    # NarraMessenger's setup guide requires calling authorize-event
    # BEFORE reading history, writing memory, invoking tools, calling
    # the model, or sending a reply. In practice Narra's policy denies
    # group events with ``mentioned=False`` outright (verified by live
    # E2E on agent_62cf67080ad4: authorize-event returned
    # ``allow=False`` on every non-@ group message).
    #
    # The owner's product intent is different: "if the agent is in the
    # room, it has the right to hear what's said, it just shouldn't
    # reply unless addressed." So we override Narra's decision for the
    # silent-batch memory-write path only:
    #
    #   dm / group_mention  → authorize-event REQUIRED (we'll reply,
    #                          call tools, invoke the model — those
    #                          are exactly what the gate is for)
    #   group_silent        → authorize-event SKIPPED (memory-only,
    #                          no reply, no tool calls, no model
    #                          invocation — the guide's rationale
    #                          doesn't apply)
    #
    # This is a KNOWN CONFLICT with the setup guide's contract.
    # Renegotiation with the NarraMessenger team is in flight; if
    # they tighten enforcement or push back, flip to False and the
    # memory path collapses to "only @-mentioned turns write memory"
    # (Slack-parity behavior — matches Narra's stated policy).
    SILENT_BYPASS_AUTHORIZE = True

    def __init__(self, max_workers: int = 3):
        super().__init__(
            base_workers=max_workers,
            # Matrix /sync already carries recent room timeline per response,
            # so we don't need the base's separate per-room history fetch;
            # the context builder reads history off ParsedMessage.raw the
            # same way NarramessengerTrigger does.
            history_config=ChannelHistoryConfig(
                load_conversation_history=False,
                history_limit=20,
                history_max_chars=20000,
            ),
        )
        # Kept per-credential so stop() can close each connection cleanly.
        self._clients: dict[str, AsyncClient] = {}

        # ── Caches populated by sync-response state / timeline walks ─────
        # room_id -> current joined member count. Used to classify DM
        # (==2) vs group (>2). Populated lazily on first sight; kept
        # accurate by watching ``m.room.member`` events for join/leave.
        self._room_member_count: dict[str, int] = {}
        # (room_id, mxid) -> current displayname. Populated the same way,
        # since displayname lives on the ``m.room.member`` event content.
        # Used for @-mention detection AND per-message attribution when
        # writing chat_history from silent batches.
        self._display_name_cache: dict[tuple[str, str], str] = {}

        # ── Silent-batch buffer ──────────────────────────────────────────
        # Keyed by (agent_id, room_id) so a single-process trigger managing
        # multiple agents keeps their group-silent streams isolated. Each
        # buffer entry is a ParsedMessage kept in arrival order.
        self._silent_buffer: dict[tuple[str, str], list[ParsedMessage]] = {}
        # Corresponding credential per buffer key — we need it at flush
        # time (which fires on a timer, not in the enqueuing coroutine).
        self._silent_creds: dict[tuple[str, str], NarramessengerCredential] = {}
        # Pending debounce timer per buffer key. Cancelled and replaced on
        # every new enqueue so 5s idle == "no new msg in 5s".
        self._silent_flush_tasks: dict[tuple[str, str], asyncio.Task] = {}
        # Per-key lock — flush is async and buffer mutation must not race
        # a concurrent enqueue that arrives while we're draining.
        self._silent_locks: dict[tuple[str, str], asyncio.Lock] = {}

    # ────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ────────────────────────────────────────────────────────────────────

    async def start(self, db) -> None:
        await super().start(db)
        logger.info(
            f"MatrixTrigger started: {len(self._workers)} workers, "
            f"watching channel_narramessenger_credentials for active rows"
        )

    async def stop(self) -> None:
        # Drain any pending silent buffers BEFORE closing clients — the
        # flush path uses the runtime client (which is in-process today
        # and doesn't need the matrix client), but we still want the
        # memory writes to land before we tear down. If drain takes too
        # long the outer stop timeout will surface it.
        try:
            await asyncio.wait_for(
                self._drain_all_silent_buffers(), timeout=10.0
            )
        except asyncio.TimeoutError:
            logger.warning(
                "MatrixTrigger.stop: silent buffer drain timed out; "
                "some group-silent messages may not be persisted"
            )
        # Cancel any leftover debounce timers regardless of drain state.
        for key, task in list(self._silent_flush_tasks.items()):
            if not task.done():
                task.cancel()
        self._silent_flush_tasks.clear()

        for key, client in list(self._clients.items()):
            try:
                await asyncio.wait_for(client.close(), timeout=3.0)
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                logger.warning(
                    f"[matrix:{key}] client close during stop: {e}"
                )
        self._clients.clear()
        await super().stop()

    # ────────────────────────────────────────────────────────────────────
    # Abstract-method implementations
    # ────────────────────────────────────────────────────────────────────

    async def load_active_credentials(
        self,
    ) -> list[NarramessengerCredential]:
        """Return every enabled NarraMessenger credential.

        No mode filter after Commit 7 — Matrix is the only transport,
        and pre-existing ``connection_mode='gateway'`` rows are treated
        as needing a fresh bind (MatrixTrigger.connect will raise on the
        missing ``matrix_access_token`` and the base will disable the
        credential, prompting the owner to re-run the bind flow).
        """
        if not self._db:
            return []
        mgr = NarramessengerCredentialManager(self._db)
        return await mgr.list_active()

    def _subscriber_key(  # type: ignore[override]
        self, credential: NarramessengerCredential
    ) -> str:
        # ``matrix:`` prefix retained as provenance (the base's
        # ``_subscriber_tasks`` map only needs uniqueness per agent, and
        # this documents which transport owns the key).
        return f"matrix:{credential.agent_id}"

    def is_permanent_auth_failure(  # type: ignore[override]
        self, exc: BaseException
    ) -> bool:
        """M_UNKNOWN_TOKEN and friends — a re-bind is required.

        matrix-nio surfaces ``M_UNKNOWN_TOKEN`` and other terminal auth
        errors in the response object; when we call ``.transport_response``
        directly (rare) it may raise ``LocalProtocolError``. The base
        treats these as "disable the credential, stop reconnecting".
        """
        text = str(exc)
        return (
            "M_UNKNOWN_TOKEN" in text
            or "M_MISSING_TOKEN" in text
            or "M_FORBIDDEN" in text
            or isinstance(exc, LocalProtocolError)
        )

    async def disable_credential(  # type: ignore[override]
        self, credential: NarramessengerCredential
    ) -> None:
        if not self._db:
            return
        mgr = NarramessengerCredentialManager(self._db)
        await mgr.set_enabled(credential.agent_id, False)

    def create_context_builder(  # type: ignore[override]
        self,
        message: ParsedMessage,
        credential: NarramessengerCredential,
        agent_id: str,
    ) -> ChannelContextBuilderBase:
        # Reuse the same builder as the polling trigger. Commit 4 will
        # decide whether it needs a matrix-specific variant or whether
        # the differences can be papered over via ParsedMessage.raw
        # fields.
        return NarramessengerContextBuilder(
            message=message,
            credential=credential,
            agent_id=agent_id,
        )

    async def is_echo(  # type: ignore[override]
        self,
        message: ParsedMessage,
        credential: NarramessengerCredential,
    ) -> bool:
        """Drop events sent by our own agent identity.

        Matrix's /sync returns every event in every room we're in,
        including the ones our own client just sent. Without this
        filter the trigger would immediately re-process every reply
        it just sent, creating a feedback loop.

        matrix-nio also filters our own device's events out of the
        default sync response; this check is belt-and-braces in case
        the server's device model produces an ordering edge case.
        """
        if not credential.matrix_user_id:
            return False
        return message.sender_id == credential.matrix_user_id

    async def resolve_sender_name(  # type: ignore[override]
        self, sender_id: str, credential: NarramessengerCredential
    ) -> str:
        """Room-state-backed display name lookup with MXID fallback.

        Walks the per-room display name cache first (populated by member
        state events flowing through the sync loop). If the cache misses
        we return the MXID unchanged — better than blocking a worker on
        an ad-hoc ``room_get_state_event`` call, and the next member
        event in the sync stream will backfill the cache anyway.
        """
        # No room context in this signature; the cache is per-(room, mxid),
        # so scan for any room where we've seen this mxid. In practice each
        # sender has one canonical display name across all rooms of a
        # homeserver anyway.
        for (_room, mxid), name in self._display_name_cache.items():
            if mxid == sender_id and name:
                return name
        return sender_id

    # ────────────────────────────────────────────────────────────────────
    # Room-state consumption (populates member count + display name caches)
    # ────────────────────────────────────────────────────────────────────

    def _apply_member_event(self, room_id: str, event: RoomMemberEvent) -> None:
        """Update caches from a single ``m.room.member`` state event.

        Called from the sync loop for both timeline events (live changes)
        and state events (initial snapshot on first sync). Idempotent —
        replaying the same event twice sets the cache to the same value.

        Membership transitions:
          - ``join``  → +1 member if we hadn't counted them; refresh name
          - ``leave`` → -1 member (never negative); drop the name entry
          - ``ban`` / ``knock`` / ``invite`` → member-count changes only
            when transitioning INTO join/OUT of join, so we skip these
            for the count and just handle name refresh.
        """
        mxid = event.state_key or event.sender or ""
        membership = event.membership or ""
        prev_membership = event.prev_membership or ""
        content = event.content if isinstance(event.content, dict) else {}
        name = content.get("displayname") or ""

        # Display name: update on any join / knock-with-name, drop on leave.
        if membership == "join" and name:
            self._display_name_cache[(room_id, mxid)] = name
        elif membership == "leave":
            self._display_name_cache.pop((room_id, mxid), None)

        # Member count deltas — only transitions INTO/OUT of "join" matter.
        was_joined = prev_membership == "join"
        is_joined = membership == "join"
        if not was_joined and is_joined:
            self._room_member_count[room_id] = (
                self._room_member_count.get(room_id, 0) + 1
            )
        elif was_joined and not is_joined:
            self._room_member_count[room_id] = max(
                0, self._room_member_count.get(room_id, 1) - 1
            )

    async def _get_member_count(
        self, client: AsyncClient, room_id: str
    ) -> int:
        """Return current joined-member count for ``room_id``.

        Cache-first. On miss, one authenticated GET to
        ``/_matrix/client/v3/rooms/{room}/joined_members`` populates the
        cache. Returns 0 on failure — callers must treat 0 as "unknown,
        default to group_silent" so we NEVER accidentally auto-reply to a
        room whose shape we can't verify.
        """
        cached = self._room_member_count.get(room_id)
        if cached is not None:
            return cached
        try:
            resp = await client.joined_members(room_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[matrix] joined_members({room_id}) raised: "
                f"{type(e).__name__}: {e}"
            )
            return 0
        # nio returns JoinedMembersResponse (success) or JoinedMembersError
        # (failure). Success has .members (list of RoomMember dataclasses).
        members = getattr(resp, "members", None)
        if not isinstance(members, list):
            logger.warning(
                f"[matrix] joined_members({room_id}) returned no members "
                f"(status={getattr(resp, 'status_code', 'unknown')})"
            )
            return 0
        count = len(members)
        self._room_member_count[room_id] = count
        # Backfill display names from the roster too so the first message
        # in a fresh room doesn't miss @-mention detection.
        for m in members:
            mxid = getattr(m, "user_id", None)
            name = getattr(m, "display_name", None)
            if mxid and name:
                self._display_name_cache[(room_id, mxid)] = name
        return count

    # ────────────────────────────────────────────────────────────────────
    # Classification (DM / group_mention / group_silent)
    # ────────────────────────────────────────────────────────────────────

    def _is_mentioning_us(
        self,
        message: ParsedMessage,
        credential: NarramessengerCredential,
    ) -> bool:
        """True if the message @-mentions our agent.

        Split out from ``_classify`` in Commit 5 so the Narra
        authorize-event gate can compute the ``mentioned`` payload flag
        BEFORE classification runs. (Authorize-event must fire before we
        do any of "read history / write memory / call tool / call model
        / send reply" — see [[matrix_trigger.py]] Commit 5 note.)

        Mention detection covers three surfaces different clients emit:

        1. MSC3952 intentional mentions — the modern protocol,
           ``m.mentions.user_ids`` on the raw event content.
        2. Raw MXID inline in body — some clients still do this
           (``hey @agent-abc:h can you...``).
        3. ``@displayname`` inline — most human-typed mentions
           (``hey @Agent Bot ...``); requires the display name cache
           to have been populated by prior member state events.
        """
        body = (message.content or "").strip()
        my_id = credential.matrix_user_id or ""

        # (1) Intentional Mentions on the raw event.
        raw = message.raw or {}
        nio_event = raw.get("_nio_event")
        if nio_event is not None:
            source = getattr(nio_event, "source", None)
            if isinstance(source, dict):
                mentions = (
                    source.get("content", {}).get("m.mentions", {})
                    if isinstance(source.get("content"), dict)
                    else {}
                )
                user_ids = mentions.get("user_ids") if isinstance(mentions, dict) else None
                if isinstance(user_ids, list) and my_id and my_id in user_ids:
                    return True

        # (2) Raw MXID in body.
        if my_id and my_id in body:
            return True

        # (3) ``@displayname`` in body — ROOM-SCOPED, ``@``-prefix ONLY.
        #     Use only THIS room's cached display name (a name from another
        #     room must not leak in), and require the explicit ``@name`` form.
        #     Bare-word matching ("Agent"/"Bot" as a plain word) is
        #     intentionally NOT accepted — it false-fired group_mention on
        #     sentences like "I called the agent" → a full agent run + group
        #     reply. The three legitimate mention surfaces are @-prefix / raw
        #     MXID (2) / MSC3952 (1).
        my_name = self._display_name_cache.get((message.chat_id, my_id), "")
        if my_name and f"@{my_name}" in body:
            return True

        return False

    async def _classify(
        self,
        client: AsyncClient,
        message: ParsedMessage,
        credential: NarramessengerCredential,
        *,
        mentioned: Optional[bool] = None,
    ) -> _ClassifyTarget:
        """Route a message to the right handler.

        - Member count 2 → DM (1:1 owner-agent room, or any 2-person room)
        - Member count >2 AND agent explicitly mentioned → group_mention
        - Member count >2 AND agent NOT mentioned → group_silent
        - Member count 0 (unknown) → group_silent (safe default: don't
          auto-reply to a room we can't verify the shape of; the
          silent-batch path still writes memory)

        Args:
            mentioned: If already computed by the caller (e.g. the
                authorize-event gate already needed it for its payload),
                reuse rather than re-scanning the body. If None, we
                compute via :meth:`_is_mentioning_us`.
        """
        count = await self._get_member_count(client, message.chat_id)
        if count == 2:
            return "dm"
        if mentioned is None:
            mentioned = self._is_mentioning_us(message, credential)
        return "group_mention" if mentioned else "group_silent"

    # ────────────────────────────────────────────────────────────────────
    # Silent-batch buffer + debounce
    # ────────────────────────────────────────────────────────────────────

    async def _enqueue_silent(
        self,
        credential: NarramessengerCredential,
        message: ParsedMessage,
    ) -> None:
        """Append to the per-(agent, room) silent buffer + (re)start debounce.

        Two exit triggers race: burst-cap (immediate flush when the
        buffer hits SILENT_FLUSH_BURST_SIZE) and idle (SILENT_DEBOUNCE_
        SECONDS after the LAST enqueue — every new enqueue cancels and
        replaces the pending flush timer).
        """
        key = (credential.agent_id, message.chat_id)
        lock = self._silent_locks.setdefault(key, asyncio.Lock())
        async with lock:
            buf = self._silent_buffer.setdefault(key, [])
            buf.append(message)
            self._silent_creds[key] = credential
            size = len(buf)
        # Cancel any pending debounce; a new one starts below, or the
        # burst-cap path flushes immediately.
        existing = self._silent_flush_tasks.pop(key, None)
        if existing is not None and not existing.done():
            existing.cancel()
        if size >= self.SILENT_FLUSH_BURST_SIZE:
            logger.info(
                f"[matrix:{credential.agent_id}] SILENT enqueue+burst-flush "
                f"(room={message.chat_id}, buf_size={size}, "
                f"burst_cap={self.SILENT_FLUSH_BURST_SIZE})"
            )
            await self._flush_silent(key)
        else:
            logger.info(
                f"[matrix:{credential.agent_id}] SILENT enqueue "
                f"(room={message.chat_id}, event={message.message_id}, "
                f"buf_size={size}, debounce={self.SILENT_DEBOUNCE_SECONDS}s)"
            )
            task = asyncio.create_task(self._debounce_flush(key))
            # Fire-and-forget mine (incident lesson #2): without a done
            # callback, an exception escaping _debounce_flush → _flush_silent
            # is only surfaced as a GC "task exception was never retrieved"
            # warning. Attach a logger so it's an ERROR we can act on.
            task.add_done_callback(self._on_flush_task_done)
            self._silent_flush_tasks[key] = task

    @staticmethod
    def _on_flush_task_done(task: "asyncio.Task") -> None:
        """Done-callback for the debounce flush task — surface any crash."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                f"[matrix] silent-debounce flush task crashed: "
                f"{type(exc).__name__}: {exc}"
            )

    async def _debounce_flush(self, key: tuple[str, str]) -> None:
        """Sleep-then-flush task. Cancelled by every new enqueue."""
        try:
            await asyncio.sleep(self.SILENT_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            logger.info(
                f"[matrix:silent-debounce cancelled for key={key}]"
            )
            return
        logger.info(
            f"[matrix:silent-debounce fired for key={key}] "
            f"→ flushing after {self.SILENT_DEBOUNCE_SECONDS}s idle"
        )
        await self._flush_silent(key)

    async def _flush_silent(self, key: tuple[str, str]) -> None:
        """Drain the buffer for ``key`` and hand it off to the 4a silent-
        batch runtime call. Errors are swallowed — a dropped batch is
        recoverable via reconnect + since_token replay, but a raised
        exception here would break the debounce timer for the room."""
        lock = self._silent_locks.setdefault(key, asyncio.Lock())
        async with lock:
            msgs = self._silent_buffer.pop(key, [])
            cred = self._silent_creds.pop(key, None)
        self._silent_flush_tasks.pop(key, None)
        if not msgs or cred is None:
            logger.info(
                f"[matrix:silent-flush noop for key={key}] "
                f"(msgs={len(msgs)}, cred={'set' if cred else 'None'})"
            )
            return
        logger.info(
            f"[matrix:{cred.agent_id}] SILENT flush → batch of {len(msgs)} "
            f"(room={key[1]})"
        )
        # Pre-resolve display names once for the whole batch.
        sender_names: dict[str, str] = {}
        for m in msgs:
            sid = m.sender_id or ""
            if not sid or sid in sender_names:
                continue
            # (room, mxid) lookup; fall back to MXID.
            name = self._display_name_cache.get((m.chat_id, sid))
            sender_names[sid] = name or sid
        try:
            await self._build_and_run_agent_silent_batch(
                credential=cred,
                messages=msgs,
                sender_name_by_id=sender_names,
            )
            logger.info(
                f"[matrix:{cred.agent_id}] SILENT flush OK "
                f"(batch={len(msgs)}, room={key[1]})"
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(
                f"[matrix:{cred.agent_id}] silent flush raised "
                f"(batch of {len(msgs)}): {e}"
            )

    async def _drain_all_silent_buffers(self) -> None:
        """Flush every pending silent buffer. Called from stop() so we
        don't lose in-flight memory writes on shutdown, and after
        reconnect burst so backfill lands immediately."""
        keys = list(self._silent_buffer.keys())
        if keys:
            logger.info(
                f"[matrix:silent-drain] draining {len(keys)} buffer(s): "
                f"{[str(k) for k in keys]}"
            )
        for key in keys:
            task = self._silent_flush_tasks.pop(key, None)
            if task is not None and not task.done():
                task.cancel()
            await self._flush_silent(key)

    # ────────────────────────────────────────────────────────────────────
    # Narra authorize-event gate + m.notice sender
    # ────────────────────────────────────────────────────────────────────

    def _room_member_mxids(self, room_id: str) -> list[str]:
        """Return the joined-member MXIDs we've seen for ``room_id``.

        Narra explicitly says it does NOT trust caller-supplied
        membership as authority (see setup-guide.md `Direct Group
        Context` section) — the server re-verifies. So this is a hint,
        not a source of truth. Empty list is fine; the server will
        still authorize based on its own view of the room.
        """
        return [
            mxid
            for (rid, mxid) in self._display_name_cache.keys()
            if rid == room_id
        ]

    async def _authorize_event(
        self,
        credential: NarramessengerCredential,
        message: ParsedMessage,
        *,
        mentioned: bool,
    ) -> _AuthorizeVerdict:
        """Call Narra's ``authorize-event`` gate for one Matrix event.

        Contract (from NarraMessenger setup guide, ``Matrix Authorize
        Event`` section):

            POST {backend}/api/agent-runtime/matrix/authorize-event
            Authorization: Bearer <Narra agent secret token>
            {
                roomId, senderMatrixUserId,
                memberMatrixUserIds, mentioned
            }

        Response shape:
            {"allow": bool, "notice": {"send": bool, "text": str}}

        Fail-closed rules (all → allow=False):
        - non-2xx status (401 during pending bind is EXPECTED here —
          not a permanent auth failure, just wait for owner to
          complete runtime-ready)
        - transport error / timeout
        - non-JSON body
        - allow field missing or not exactly ``True``

        Narra intentionally fails closed on unknown roomId, suspended
        bind, guide contract mismatch. We honour that unconditionally.
        """
        base_url = getattr(credential, "backend_base_url", "") or ""
        bearer = getattr(credential, "bearer_token", "") or ""
        if not base_url or not bearer:
            # Missing bind bearer means the credential row is not fully
            # provisioned yet; fail-closed with no notice.
            logger.warning(
                f"[matrix:{credential.agent_id}] authorize-event skipped: "
                f"missing backend_base_url or bearer_token; treating as deny"
            )
            return _AuthorizeVerdict(allow=False)

        url = f"{base_url.rstrip('/')}{self.AUTHORIZE_EVENT_PATH}"
        members = self._room_member_mxids(message.chat_id)
        # Guarantee at least sender + our agent are present, even if
        # cache is empty (fresh room, first event).
        if credential.matrix_user_id and credential.matrix_user_id not in members:
            members = members + [credential.matrix_user_id]
        if message.sender_id and message.sender_id not in members:
            members = members + [message.sender_id]

        payload = {
            "roomId": message.chat_id,
            "senderMatrixUserId": message.sender_id,
            "memberMatrixUserIds": members,
            "mentioned": bool(mentioned),
        }
        headers = {
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(
            total=self.AUTHORIZE_EVENT_TIMEOUT_SECONDS
        )

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    status = resp.status
                    if status < 200 or status >= 300:
                        # 401 during pending bind is expected; log at
                        # info so it doesn't spam ERROR during the
                        # bind-in-progress window.
                        level = "info" if status == 401 else "warning"
                        getattr(logger, level)(
                            f"[matrix:{credential.agent_id}] authorize-event "
                            f"non-2xx status={status} room={message.chat_id} "
                            f"→ fail-closed deny"
                        )
                        return _AuthorizeVerdict(allow=False)
                    try:
                        data = await resp.json()
                    except (aiohttp.ContentTypeError, ValueError) as e:
                        logger.warning(
                            f"[matrix:{credential.agent_id}] authorize-event "
                            f"invalid JSON: {type(e).__name__}: {e} "
                            f"→ fail-closed deny"
                        )
                        return _AuthorizeVerdict(allow=False)
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(
                f"[matrix:{credential.agent_id}] authorize-event transport "
                f"error: {type(e).__name__}: {e} → fail-closed deny"
            )
            return _AuthorizeVerdict(allow=False)

        allow = data.get("allow") is True
        notice = data.get("notice") if isinstance(data, dict) else None
        notice_send = False
        notice_text: Optional[str] = None
        if isinstance(notice, dict):
            notice_send = notice.get("send") is True
            raw_text = notice.get("text")
            if isinstance(raw_text, str) and raw_text.strip():
                notice_text = raw_text
        return _AuthorizeVerdict(
            allow=allow,
            notice_send=notice_send if not allow else False,
            notice_text=notice_text if not allow else None,
        )

    async def _send_matrix_notice(
        self,
        credential: NarramessengerCredential,
        room_id: str,
        text: str,
    ) -> None:
        """Send Narra's server-suggested denial notice as ``m.notice``.

        Per guide: this is the ONLY allowed side effect for a denied
        event. Do NOT read history, write memory, invoke tools, call
        the model, or send anything other than exactly this text as
        ``m.notice``. If the send itself fails, we log — Narra told
        us to deliver this text and we tried; audit it in the standard
        transport_send_failed path so ops can see it, but do NOT
        retry the underlying event handling.
        """
        key = self._subscriber_key(credential)
        client = self._clients.get(key)
        if client is None:
            logger.warning(
                f"[matrix:{credential.agent_id}] cannot send authorize "
                f"notice: no active client for room={room_id}"
            )
            return
        try:
            resp = await client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content={"msgtype": "m.notice", "body": text},
            )
            if not isinstance(resp, RoomSendResponse):
                code = str(getattr(resp, "status_code", "unknown") or "unknown")
                logger.warning(
                    f"[matrix:{credential.agent_id}] authorize-event "
                    f"notice send failed: {code} (room={room_id})"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[matrix:{credential.agent_id}] authorize-event notice "
                f"send raised: {type(e).__name__}: {e} (room={room_id})"
            )

    # ────────────────────────────────────────────────────────────────────
    # Reply sender + failure notification
    # ────────────────────────────────────────────────────────────────────

    async def _send_matrix_reply(
        self,
        credential: NarramessengerCredential,
        room_id: str,
        content: str,
    ) -> bool:
        """Send ``content`` back into ``room_id`` as ``m.room.message``.

        Retry policy:
          - M_LIMIT_EXCEEDED → obey ``retry_after_ms``, then retry.
          - M_UNKNOWN_TOKEN / M_MISSING_TOKEN / M_FORBIDDEN → audit +
            give up (the base's watcher will disable the credential on
            the next sync tick when it sees the same code).
          - Anything else (network, 5xx, unknown status) → exponential
            backoff, cap SEND_MAX_ATTEMPTS.

        Returns True iff the reply was accepted by the homeserver.
        A False return does NOT re-raise — the sync loop must not stall
        on a single failed reply; the audit event carries the diagnostic.
        """
        key = self._subscriber_key(credential)
        client = self._clients.get(key)
        if client is None:
            logger.warning(
                f"[matrix:{credential.agent_id}] cannot send reply: "
                f"no active client (sync loop torn down?)"
            )
            await self._audit(
                EVENT_TRANSPORT_SEND_FAILED,
                agent_id=credential.agent_id,
                app_id=getattr(credential, "app_id", ""),
                chat_id=room_id,
                details={
                    "error_code": "no_active_client",
                    "attempts": 0,
                    "body_preview": (content or "")[:120],
                },
            )
            return False

        backoff_ms = self.SEND_INITIAL_BACKOFF_MS
        last_code: str = ""
        last_error: str = ""

        for attempt in range(1, self.SEND_MAX_ATTEMPTS + 1):
            try:
                resp = await client.room_send(
                    room_id=room_id,
                    message_type="m.room.message",
                    content={"msgtype": "m.text", "body": content},
                )
            except Exception as e:  # noqa: BLE001
                last_code = "transport_exception"
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    f"[matrix:{credential.agent_id}] room_send raised on "
                    f"attempt {attempt}/{self.SEND_MAX_ATTEMPTS}: {last_error}"
                )
                if attempt < self.SEND_MAX_ATTEMPTS:
                    await asyncio.sleep(backoff_ms / 1000)
                    backoff_ms *= 2
                continue

            if isinstance(resp, RoomSendResponse):
                return True

            if isinstance(resp, RoomSendError):
                last_code = str(getattr(resp, "status_code", "") or "unknown")
                last_error = str(
                    getattr(resp, "message", "") or last_code
                )
                if last_code == "M_LIMIT_EXCEEDED":
                    retry_ms = int(getattr(resp, "retry_after_ms", 1000) or 1000)
                    logger.info(
                        f"[matrix:{credential.agent_id}] rate-limited; "
                        f"sleeping {retry_ms}ms before retry "
                        f"(attempt {attempt}/{self.SEND_MAX_ATTEMPTS})"
                    )
                    await asyncio.sleep(retry_ms / 1000)
                    continue
                if last_code in (
                    "M_UNKNOWN_TOKEN",
                    "M_MISSING_TOKEN",
                    "M_FORBIDDEN",
                ):
                    logger.error(
                        f"[matrix:{credential.agent_id}] permanent send "
                        f"failure {last_code}; leaving credential for "
                        f"the sync loop to disable"
                    )
                    break
                logger.warning(
                    f"[matrix:{credential.agent_id}] room_send returned "
                    f"{last_code} on attempt {attempt}/"
                    f"{self.SEND_MAX_ATTEMPTS}: {last_error}"
                )
                if attempt < self.SEND_MAX_ATTEMPTS:
                    await asyncio.sleep(backoff_ms / 1000)
                    backoff_ms *= 2
                continue

            # Neither success nor structured error — nio returned something
            # unexpected. Log and treat as transient.
            last_code = "unknown_response"
            last_error = f"{type(resp).__name__}"
            logger.warning(
                f"[matrix:{credential.agent_id}] room_send returned "
                f"unexpected {last_error} on attempt {attempt}"
            )
            if attempt < self.SEND_MAX_ATTEMPTS:
                await asyncio.sleep(backoff_ms / 1000)
                backoff_ms *= 2

        await self._audit(
            EVENT_TRANSPORT_SEND_FAILED,
            agent_id=credential.agent_id,
            app_id=getattr(credential, "app_id", ""),
            chat_id=room_id,
            details={
                "error_code": last_code,
                "error_message": last_error[:400],
                "attempts": self.SEND_MAX_ATTEMPTS,
                "body_preview": (content or "")[:120],
            },
        )
        return False

    # ────────────────────────────────────────────────────────────────────
    # Overrides: classify → route silent vs full-agent; then send reply
    # ────────────────────────────────────────────────────────────────────

    async def _process_message(  # type: ignore[override]
        self,
        credential: NarramessengerCredential,
        message: ParsedMessage,
    ) -> None:
        """Gate → classify → route.

        Pipeline (Commit 5):

        1. Drop if no active client (sync torn down mid-flight).
        2. Drop if echo (our own agent's message).
        3. **Narra authorize-event gate**. Per NarraMessenger's Direct
           Matrix contract, MUST call ``POST /api/agent-runtime/matrix/
           authorize-event`` BEFORE reading history, writing memory,
           invoking tools, calling the model, or sending a reply. Applies
           to both silent AND full paths because silent still writes
           memory. On deny + notice, forward the notice as ``m.notice``
           then stop; on deny + no notice, drop silently.
        4. Classify DM / group_mention / group_silent; route accordingly.

        Echo filtering happens IN the classifier's client lookup /
        super(); duplicating it here would risk drift. The base already
        also handles unbound-credential and empty-content guards, so
        neither path re-implements them.
        """
        key = self._subscriber_key(credential)
        client = self._clients.get(key)
        if client is None:
            # No active client → sync loop torn down while events were
            # in flight. Drop the message; the base's _subscribe_loop
            # will replay from since_token on reconnect. INFO level so
            # a missing agent run for an event has a paper trail.
            logger.info(
                f"[matrix:{credential.agent_id}] DROP: no active client "
                f"(room={message.chat_id}, event={message.message_id})"
            )
            return

        # Echo filter FIRST — silent path should also drop our own
        # agent's replies before they hit the buffer.
        if await self.is_echo(message, credential):
            logger.info(
                f"[matrix:{credential.agent_id}] DROP: echo of own message "
                f"(room={message.chat_id}, event={message.message_id})"
            )
            return

        # Classify FIRST (2026-07-02): the silent-memory path bypasses
        # authorize-event per the owner override (see
        # ``SILENT_BYPASS_AUTHORIZE``). Computing target before the gate
        # lets us split the two dispositions cleanly.
        mentioned = self._is_mentioning_us(message, credential)
        target = await self._classify(
            client, message, credential, mentioned=mentioned
        )
        logger.info(
            f"[matrix:{credential.agent_id}] CLASSIFY target={target} "
            f"(room={message.chat_id}, event={message.message_id}, "
            f"mentioned={mentioned})"
        )

        # Silent path: memory-only, no reply / tool / model. Owner
        # policy override — do not call authorize-event. See
        # SILENT_BYPASS_AUTHORIZE docstring for the full rationale.
        if target == "group_silent":
            if not self.SILENT_BYPASS_AUTHORIZE:
                # Fallback to guide-strict behaviour: authorize-event
                # first, drop if denied. Flip this constant to False
                # if Narra tightens enforcement.
                verdict = await self._authorize_event(
                    credential, message, mentioned=mentioned
                )
                if not verdict.allow:
                    logger.info(
                        f"[matrix:{credential.agent_id}] AUTHZ deny silent "
                        f"(strict mode; room={message.chat_id})"
                    )
                    return
            await self._enqueue_silent(credential, message)
            return

        # dm / group_mention → we WILL reply, invoke tools, call the
        # model. That's exactly what authorize-event is designed to
        # gate. Skipping here would violate the guide's contract in
        # its unambiguously-intended domain.
        verdict = await self._authorize_event(
            credential, message, mentioned=mentioned
        )
        if not verdict.allow:
            if verdict.notice_send and verdict.notice_text:
                logger.info(
                    f"[matrix:{credential.agent_id}] AUTHZ deny + notice "
                    f"(room={message.chat_id}, event={message.message_id}, "
                    f"notice={verdict.notice_text[:80]!r})"
                )
                await self._send_matrix_notice(
                    credential, message.chat_id, verdict.notice_text
                )
            else:
                logger.info(
                    f"[matrix:{credential.agent_id}] AUTHZ deny silent "
                    f"(room={message.chat_id}, event={message.message_id}, "
                    f"mentioned={mentioned})"
                )
            return

        # Full-agent pipeline.
        await super()._process_message(credential, message)

    async def _build_and_run_agent(  # type: ignore[override]
        self,
        credential: NarramessengerCredential,
        message: ParsedMessage,
        sender_name: str,
        *,
        attachments: Optional[list] = None,
    ) -> str:
        """Dispatch: streaming (m.replace edits) or atomic (single send).

        Toggled by :data:`STREAMING_ENABLED`. The streaming path uses
        ``run_stream`` from the runtime client and edits the placeholder
        message as the agent generates text; the atomic path retains
        the pre-2026-07-03 behaviour of one final ``room_send`` after
        the runtime completes. Both paths still let the base's
        ``_process_message`` write to the inbox on the returned string.
        """
        if self.STREAMING_ENABLED:
            return await self._build_and_run_agent_streaming(
                credential, message, sender_name, attachments=attachments
            )
        return await self._build_and_run_agent_atomic(
            credential, message, sender_name, attachments=attachments
        )

    async def _build_and_run_agent_atomic(
        self,
        credential: NarramessengerCredential,
        message: ParsedMessage,
        sender_name: str,
        *,
        attachments: Optional[list] = None,
    ) -> str:
        """Pre-streaming atomic path: base returns text, we send once.

        Kept as the ``STREAMING_ENABLED=False`` fallback so a Matrix
        rate-limit spike or a debugging session can trivially bypass the
        streaming state machine.
        """
        text = await super()._build_and_run_agent(
            credential, message, sender_name, attachments=attachments
        )
        if not text or not text.strip():
            logger.info(
                f"[matrix:{credential.agent_id}] agent chose silent "
                f"reply (room={message.chat_id}); nothing sent"
            )
            return text
        await self._send_matrix_reply(credential, message.chat_id, text)
        return text

    async def _build_and_run_agent_streaming(
        self,
        credential: NarramessengerCredential,
        message: ParsedMessage,
        sender_name: str,
        *,
        attachments: Optional[list] = None,
    ) -> str:
        """Silent-first streaming path — no placeholder.

        Structure mirrors ``ChannelTriggerBase._build_and_run_agent`` from
        prompt-building onward — replicated here because the base uses
        ``run_and_collect`` (drives to completion) and we need
        ``run_stream`` (yields events live to catch ``MessageType.ERROR``).

        Flow (see :class:`_StreamReplyState`):
          1. NOTHING sent up front. The room stays as-is while the agent works.
          2. Consume the runtime stream:
               - ``narra_reply(text)`` → captured as the final answer.
               - ``narra_progress(text)`` → recorded to log only, no room
                 side-effect (2026-07-08: room-facing progress removed).
               - ``MessageType.ERROR`` → set ``state.error_seen``.
             Raw token deltas and thinking events are ignored.
          3. Stream ends. Finalize:
               - narra_reply captured → fresh ``room_send`` the reply text.
               - No reply + error_seen → fresh ``room_send`` STREAM_ERROR_MARKER.
               - No reply + no error → NO-OP (room stays untouched).
        """
        # Lazy import — see top-of-file comment about circular dependency.
        from xyz_agent_context.agent_runtime.client import (
            get_agent_runtime_client,
        )
        from xyz_agent_context.schema.channel_tag import ChannelTag

        agent_id = getattr(credential, "agent_id", "")
        builder = self.create_context_builder(message, credential, agent_id)
        prompt = await builder.build_prompt(self._history_config)
        try:
            anchor = await builder.build_retrieval_anchor()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"build_retrieval_anchor failed; using input fallback: {e}"
            )
            anchor = None

        channel_tag = ChannelTag(
            channel=self.channel_name,
            sender_name=sender_name,
            sender_id=message.sender_id,
            room_id=message.chat_id,
        )
        tagged_prompt = f"{channel_tag.format()}\n{prompt}"
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
        if attachments:
            extra_data["attachments"] = [
                a.model_dump(mode="json") for a in attachments
            ]

        state = _StreamReplyState()

        client_stream = get_agent_runtime_client().run_stream(
            agent_id=agent_id,
            user_id=owner_user_id,
            input_content=tagged_prompt,
            working_source=self.working_source,
            trigger_extra_data=extra_data,
        )

        try:
            async for event in client_stream:
                await self._handle_stream_event(
                    event, state, credential, message.chat_id
                )
        except Exception as e:  # noqa: BLE001
            # Runtime blew up mid-stream. Belt-and-suspenders for the case
            # where the runtime doesn't emit a ``MessageType.ERROR`` before
            # dying — mark ``error_seen`` so finalize surfaces the error
            # marker rather than staying silent (which would look identical
            # to a normal no-reply turn).
            state.error_seen = True
            state.last_error_message = f"{type(e).__name__}: {e}"[:200]
            logger.warning(
                f"[matrix:{credential.agent_id}] run_stream raised: "
                f"{type(e).__name__}: {e}"
            )

        # Finalize.
        final_text = (state.narra_reply_text or "").strip()
        if final_text:
            # No placeholder to edit — fresh-send via the retry-aware
            # reply sender (same one the atomic path uses).
            await self._send_matrix_reply(
                credential, message.chat_id, final_text
            )
        else:
            await self._finalize_stream_silent(
                credential, message.chat_id, state
            )
        return final_text

    async def _handle_stream_event(
        self,
        event: Any,
        state: _StreamReplyState,
        credential: NarramessengerCredential,
        room_id: str,
    ) -> None:
        """Dispatch one runtime event.

        Only the agent's explicit tool calls touch the room. Tool calls in
        this codebase arrive as ``ProgressMessage`` (``message_type=PROGRESS``)
        with ``details.tool_name`` / ``details.arguments`` (the nominal
        ``AgentToolCall`` schema type is never constructed — see
        ``run_collector.py:143-165``). ``AGENT_RESPONSE`` token deltas and
        ``AGENT_THINKING`` are ignored on purpose: the room shows intentional
        updates (``narra_progress`` / ``narra_reply``), never streamed output.
        """
        mt = getattr(event, "message_type", None)
        # ERROR events surface framework-level failures (agent-loop fatal,
        # LineTooLong from oversized tool_call_output_item, transport
        # crashes, etc.). Record them so finalize can distinguish
        # "crashed silent" from "chose silent" — the UX branches on this.
        if mt == MessageType.ERROR:
            state.error_seen = True
            err_msg = (
                getattr(event, "error_message", "")
                or getattr(event, "message", "")
                or ""
            )
            if isinstance(err_msg, str) and err_msg.strip():
                state.last_error_message = err_msg.strip()[:200]
            err_type = getattr(event, "error_type", "") or "unknown"
            logger.warning(
                f"[matrix:{credential.agent_id}] streaming saw ERROR event "
                f"(type={err_type}, room={room_id}); finalize will surface "
                f"the error marker"
            )
            return
        if mt != MessageType.PROGRESS:
            return
        details = getattr(event, "details", None)
        if not isinstance(details, dict):
            return
        tool_name = details.get("tool_name") or ""
        arguments = details.get("arguments")
        # args may be a dict OR a JSON string (some SDK paths serialise args
        # mid-stream); anything else is a shape we don't recognise → skip.
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, ValueError):
                return
        if not isinstance(arguments, dict):
            return
        text = arguments.get("text")
        if not (isinstance(text, str) and text.strip()):
            return

        if "narra_reply" in tool_name:
            # LAST-writer wins if the agent calls it twice (rare). The
            # captured text is fresh-sent to the room at finalize.
            state.narra_reply_text = text
            logger.info(
                f"[matrix:{credential.agent_id}] streaming captured "
                f"narra_reply.text (len={len(text)}, room={room_id})"
            )

    async def _finalize_stream_silent(
        self,
        credential: NarramessengerCredential,
        room_id: str,
        state: _StreamReplyState,
    ) -> None:
        """Turn ended without ``narra_reply``.

        - ``state.error_seen`` set → fresh-send ``STREAM_ERROR_MARKER`` so
          the sender knows to retry rather than assume they were ignored.
          Covers both ``MessageType.ERROR`` events and outer stream
          exceptions (e.g. ``LineTooLong`` on oversized tool output).
        - Otherwise → NO-OP. The agent chose silence; the room stays as it
          was. No dot marker, no redact, no "message deleted" indicator.
        """
        if not state.error_seen:
            logger.info(
                f"[matrix:{credential.agent_id}] silent stream — no reply, "
                f"no error, staying quiet (room={room_id})"
            )
            return

        if state.last_error_message:
            logger.info(
                f"[matrix:{credential.agent_id}] silent stream — surfacing "
                f"error marker (room={room_id}, "
                f"last_error={state.last_error_message[:80]!r})"
            )
        try:
            await matrix_room_send(
                homeserver=credential.matrix_homeserver_url,
                token=credential.matrix_access_token,
                room_id=room_id,
                content={"msgtype": "m.text", "body": self.STREAM_ERROR_MARKER},
            )
            logger.info(
                f"[matrix:{credential.agent_id}] silent stream — fresh "
                f"error marker sent (room={room_id})"
            )
        except MatrixSendError as e:
            logger.warning(
                f"[matrix:{credential.agent_id}] silent stream — fresh "
                f"error marker send failed ({e.code})"
            )

    def extract_output(  # type: ignore[override]
        self, result: Any, message: ParsedMessage, credential: Any
    ) -> str:
        """Extract the reply text from the agent's ``narra_reply`` tool call.

        NarraMessenger is **trigger-driven**: the agent calls ``narra_reply``
        as a *marker* (its ``text`` arg IS the reply), and THIS trigger does
        the actual ``room_send`` afterwards (see ``_build_and_run_agent`` →
        ``_send_matrix_reply``). This is deliberately different from Lark /
        Slack / Telegram (where the channel CLI tool sends and we only
        scrape): owning delivery in the trigger is what makes progressive
        ``m.replace`` streaming possible later.

        ``narra_reply`` (NOT the generic ``send_message_to_user_directly``,
        which the shared channel prompt reserves for OWNER messages). Returning
        "" means "the agent did not reply this turn" — silent-not-reply —
        which prevents the base from posting the agent's internal thinking
        (``result.output_text``) into the room by mistake.

        raw_items shape comes from ``run_collector.collect_run``: tool calls
        are dicts ``{"item": {"type": "tool_call_item", "tool_name",
        "arguments"}}`` (same shape Lark's extractor reads). Getting this
        wrong is silent — a mismatch just yields "" and the reply is dropped
        as "nothing sent" (the 2026-07-03 bug this replaced).
        """
        raw_items = getattr(result, "raw_items", None) or []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item = raw.get("item")
            if not isinstance(item, dict) or item.get("type") != "tool_call_item":
                continue
            if "narra_reply" not in str(item.get("tool_name") or ""):
                continue
            args = item.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:  # noqa: BLE001
                    args = {}
            if isinstance(args, dict):
                text = args.get("text")
                if isinstance(text, str) and text.strip():
                    return text
        # No explicit reply tool call → stay silent. Do NOT fall back to
        # result.output_text (agent's thinking is not for the room).
        return ""

    # ────────────────────────────────────────────────────────────────────
    # Sync loop — the piece that replaces polling
    # ────────────────────────────────────────────────────────────────────

    async def connect(  # type: ignore[override]
        self, credential: NarramessengerCredential
    ) -> AsyncIterator[dict]:
        """Drive a Matrix ``/sync`` loop, yielding one raw event per event.

        Cursor-save ordering (CRITICAL — this is what makes restart safe):

            resp = await client.sync(since=X, timeout=30s)
            for event in resp:
                yield event      # base awaits _dedup_and_handle inline
            update_since_token(next_batch)   # ONLY after ALL events yielded

        If the process dies mid-batch, the cursor stays at ``X``, the
        server replays the batch on next connect, and
        :class:`ChannelTriggerBase`'s subscriber-level dedup filters the
        already-handled events by ``event_id``. Persisting the cursor
        before the async-for completes would lose crashed events forever.

        We do NOT catch exceptions in this loop; the base's
        ``_subscribe_loop`` handles transient errors with exponential
        backoff and treats permanent auth failures via
        :meth:`is_permanent_auth_failure` → :meth:`disable_credential`.
        Our job here is: talk Matrix, yield events in causal order,
        persist the cursor once we're sure the base has consumed them.
        """
        homeserver = credential.matrix_homeserver_url
        user_id = credential.matrix_user_id
        access_token = credential.matrix_access_token
        agent_id = credential.agent_id
        key = self._subscriber_key(credential)

        if not homeserver or not user_id or not access_token:
            # Guard against a mode='matrix' row whose bind never wrote real
            # matrix creds (e.g. a stale pre-Matrix bind migrated to
            # `connection_mode='matrix'` without an access token). Explicitly
            # disable the
            # credential BEFORE raising — the base's `_subscribe_loop`
            # backoff loop otherwise treats ValueError as transient and
            # retries every 120s forever, generating an ERROR + stack
            # trace on every retry. Disabling flips ``enabled=False`` so
            # the credential watcher stops re-spawning the subscriber
            # against this row until the owner re-binds.
            logger.warning(
                f"MatrixTrigger[{agent_id}] disabling credential — "
                f"missing matrix creds (homeserver={bool(homeserver)}, "
                f"user_id={bool(user_id)}, "
                f"access_token={bool(access_token)}). Re-run the bind "
                f"flow to restore the credential."
            )
            try:
                await self.disable_credential(credential)
            except Exception as e:  # noqa: BLE001
                # A DB write failure here is non-fatal — the ValueError
                # below still surfaces and the retry loop is bounded by
                # the base's exponential backoff.
                logger.warning(
                    f"MatrixTrigger[{agent_id}] disable_credential failed "
                    f"(non-fatal): {type(e).__name__}: {e}"
                )
            raise ValueError(
                f"MatrixTrigger[{agent_id}] cannot connect: missing "
                f"matrix credentials (homeserver={bool(homeserver)}, "
                f"user_id={bool(user_id)}, "
                f"access_token={bool(access_token)})"
            )

        # AsyncClientConfig: request_timeout is per-HTTP-call. Keep it a
        # bit longer than SYNC_TIMEOUT_MS so a legitimate long-poll
        # doesn't get cancelled by the client-side timeout before the
        # server returns.
        config = AsyncClientConfig(
            request_timeout=(self.SYNC_TIMEOUT_MS / 1000) + 10,
            # max_timeouts=1: on a transport error nio raises after ONE
            # attempt instead of retrying internally forever. 0 means "retry
            # transport errors internally, never return" — which would make
            # client.sync() a black hole: the base's backoff/reconnect (and
            # its per-subscriber transport_disconnected/backoff/connected
            # audit — the L2 liveness signal) would NEVER run, so a wedged
            # connection zombies silently (incident lesson #1/#4). With 1,
            # the base loop owns retry and the audit trail stays honest.
            max_timeouts=1,
        )

        client = AsyncClient(
            homeserver=homeserver,
            user=user_id,
            device_id=credential.matrix_device_id or None,
            config=config,
        )
        # matrix-nio's login-flow methods set these; we're bypassing
        # login (we already have a token from the bind flow) so we set
        # them by hand. ``access_token`` on the client instance is what
        # gets sent as ``Authorization: Bearer ...``.
        client.access_token = access_token
        client.user_id = user_id

        self._clients[key] = client
        mgr = NarramessengerCredentialManager(self._db) if self._db else None

        logger.info(
            f"[matrix:{agent_id}] connecting to {homeserver} as {user_id}"
        )

        try:
            cursor = credential.matrix_since_token or None
            is_first_sync = cursor is None

            while self.running:
                # First sync (no cursor) uses a short server timeout so a
                # cold restart doesn't block for 30s on an idle account
                # before the base can report "connected" upstream. All
                # subsequent syncs long-poll normally.
                timeout_ms = (
                    self.FIRST_SYNC_TIMEOUT_MS
                    if is_first_sync
                    else self.SYNC_TIMEOUT_MS
                )
                resp = await client.sync(
                    timeout=timeout_ms,
                    since=cursor,
                    full_state=False,
                )
                is_first_sync = False

                # A Matrix-level failure (revoked/expired token, server
                # error) comes back as a SyncError object, NOT an exception —
                # nio only raises on transport errors. Without this guard the
                # code below reads the error as a success with empty rooms and
                # loops forever, so a revoked token would never reach
                # is_permanent_auth_failure → disable_credential (it'd just
                # 120s-reconnect indefinitely). Raise with the errcode in the
                # message so the base's _subscribe_loop classifies it: an auth
                # code (M_UNKNOWN_TOKEN / M_MISSING_TOKEN / M_FORBIDDEN) trips
                # is_permanent_auth_failure → disable; anything else backs off.
                if not isinstance(resp, SyncResponse):
                    detail = (
                        f"{getattr(resp, 'status_code', '') or ''} "
                        f"{getattr(resp, 'message', '') or ''}"
                    ).strip() or type(resp).__name__
                    raise RuntimeError(f"Matrix /sync failed: {detail}")

                # Auto-join invited rooms FIRST. The guide's OpenClaw
                # config sets ``autoJoin: "always"``: when the owner
                # invites the agent to a new group, the invite arrives
                # via ``resp.rooms.invite`` and we accept it here so the
                # next sync includes that room in ``resp.rooms.join``.
                # Without this, the owner has to invite twice (or the
                # agent never appears in the room at all). We do NOT
                # audit-log accepts individually — auto-join is expected
                # behaviour, not an anomaly worth per-room storage.
                invite_rooms = getattr(
                    getattr(resp, "rooms", None), "invite", None
                ) or {}
                for room_id in list(invite_rooms.keys()):
                    try:
                        await client.join(room_id)
                        logger.info(
                            f"[matrix:{agent_id}] auto-joined invited room "
                            f"{room_id}"
                        )
                    except Exception as e:  # noqa: BLE001
                        # Join failures are non-fatal: the invite stays
                        # in resp.rooms.invite for the next sync tick,
                        # which will retry naturally. Log for visibility
                        # but do NOT propagate.
                        logger.warning(
                            f"[matrix:{agent_id}] auto-join failed for "
                            f"{room_id}: {type(e).__name__}: {e}"
                        )

                # Yield each interesting event from joined rooms.
                for room_id, room_info in resp.rooms.join.items():
                    # Consume state-block member events FIRST so the
                    # member count + display name caches are populated
                    # BEFORE any message from the same batch gets
                    # classified. On first sync this is the whole room
                    # roster; on incremental sync it's changes only.
                    state_events = getattr(
                        getattr(room_info, "state", None), "events", []
                    ) or []
                    for event in state_events:
                        if isinstance(event, RoomMemberEvent):
                            self._apply_member_event(room_id, event)

                    # Now walk the timeline. Member events here are LIVE
                    # membership changes (join/leave during this batch);
                    # apply them BEFORE the message events that follow,
                    # so classification sees the right room shape even
                    # when someone joined and immediately spoke.
                    for event in room_info.timeline.events:
                        if isinstance(event, RoomMemberEvent):
                            self._apply_member_event(room_id, event)
                            continue
                        raw = self._wrap_event(
                            event=event,
                            room_id=room_id,
                            credential=credential,
                        )
                        if raw is not None:
                            yield raw

                # ── After the async-for above returns, ALL events in
                # this batch have flowed through _dedup_and_handle in
                # the base loop. Only NOW is it safe to advance the
                # cursor; a crash between here and the next sync loses
                # nothing (we replay next_batch's precursor next time).
                #
                # Reconnect / burst flush: cold-sync backlog and post-
                # disconnect catchup should NOT wait 5s for a debounce
                # timer to fire — the backlog is already stale, get it
                # into memory now. Steady-state ticks with 0 silent
                # events skip this (drain is a no-op when buffers are
                # empty).
                if self._silent_buffer:
                    await self._drain_all_silent_buffers()

                cursor = resp.next_batch
                if mgr is not None and cursor:
                    try:
                        await mgr.update_since_token(agent_id, cursor)
                    except Exception as e:  # noqa: BLE001
                        # DB hiccup on the narrow cursor write is
                        # recoverable — worst case we replay this batch
                        # on next restart. Do NOT propagate; propagating
                        # would trigger the base's backoff+reconnect
                        # over a transient sqlite lock.
                        logger.warning(
                            f"[matrix:{agent_id}] update_since_token "
                            f"failed (non-fatal, next tick will retry): "
                            f"{e}"
                        )

                # First-sync device_id auto-persist. matrix-nio picks the
                # device from either what we set at construction or from
                # the sync response's implicit device tracking; we mirror
                # whatever it settles on into the credentials row so a
                # restart doesn't spawn a fresh device.
                if (
                    mgr is not None
                    and not credential.matrix_device_id
                    and client.device_id
                ):
                    try:
                        await mgr.update_device_id(
                            agent_id, client.device_id
                        )
                        credential.matrix_device_id = client.device_id
                        logger.info(
                            f"[matrix:{agent_id}] auto-registered "
                            f"device_id={client.device_id}"
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            f"[matrix:{agent_id}] device_id persist "
                            f"failed (non-fatal): {e}"
                        )
        finally:
            self._clients.pop(key, None)
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass

    # ────────────────────────────────────────────────────────────────────
    # Event → raw dict wrapper
    # ────────────────────────────────────────────────────────────────────

    def _wrap_event(
        self,
        *,
        event: Any,
        room_id: str,
        credential: NarramessengerCredential,
    ) -> Optional[dict]:
        """Marshal a matrix-nio event object into a dict for the base
        pipeline.

        Handles: plain text, NarraMessenger *compound* messages (the way
        document/image attachments actually arrive — see below), and
        standard inline media (m.audio voice notes / m.image / m.file / …).
        Anything unrecognised is dropped (returning ``None`` skips the
        base's parse_event call).

        **Compound messages** — a document/image attachment does NOT arrive
        as a standard inline m.image/m.file event. It arrives as a SINGLE
        event with the custom msgtype ``ai.netmind.compound``, whose
        ``content["ai.netmind.compound"]`` block self-describes the media
        (``media_url`` / ``mime_type`` / ``file_name`` / ``size``) plus the
        REAL user text; the @-mention rides in ``content["m.mentions"]`` on
        the same event. Because the msgtype is custom, nio has no factory
        for it and parses the event as ``RoomMessageUnknown`` — so we key off
        the raw ``source`` content, NOT the nio class. An isinstance() gate
        would miss it and the attachment would be silently dropped (the
        agent never sees it, and an @-mention riding on it never becomes
        group_mention → no reply). The same shape covers every attachment
        mime; only ``mime_type`` differs (pdf / image/jpeg / video/mp4 / …).
        Verified on the wire 2026-07-20 (agent_743423ca551b: PDF + image).

        (History: until 2026-07-03 the same payload arrived as a hidden
        ``m.text`` + ``content["ai.netmind.hint"].compound_trigger`` preview
        alongside an ignored ``ai.netmind.compound`` sibling. NarraMessenger
        dropped that two-event hint shape for the single self-describing
        event above; per 铁律 #2 the old hint path is removed, not kept.)

        Voice notes are a DIFFERENT path: they arrive as a standard
        ``m.audio`` event (RoomMessageMedia, payload under ``content.info``),
        handled by the media branch below — not compound.
        """
        # Pull the raw event content up front. The compound branch below
        # matches a CUSTOM msgtype that nio parses as RoomMessageUnknown, so
        # it cannot be gated on isinstance() — we read content["msgtype"].
        source = getattr(event, "source", None)
        content = source.get("content") if isinstance(source, dict) else None
        content = content if isinstance(content, dict) else {}

        # ── NarraMessenger compound message (document / image / …) ───────
        # A single ``ai.netmind.compound`` event self-describes: the payload
        # block holds the real user text + media mxc + mime + filename +
        # size; the @-mention rides in content["m.mentions"] (read later by
        # _is_mentioning_us off the raw _nio_event.source). One branch covers
        # every attachment mime. See the method docstring for the wire
        # format + why we key off content, not the nio class.
        compound = content.get("ai.netmind.compound")
        if content.get("msgtype") == "ai.netmind.compound" and isinstance(
            compound, dict
        ):
            mxc = compound.get("media_url", "") or ""
            logger.info(
                f"[matrix:{credential.agent_id}] compound "
                f"(media={bool(mxc)}, mime={compound.get('mime_type', '')}, "
                f"room={room_id})"
            )
            return {
                "kind": "m.room.message.compound",
                "event_id": event.event_id,
                "room_id": room_id,
                "sender_id": event.sender,
                "server_ts": event.server_timestamp,
                "text": compound.get("text", "") or "",
                "mxc_url": mxc,
                "mimetype": compound.get("mime_type", "") or "",
                "file_name": compound.get("file_name", "") or "",
                "size": int(compound.get("size", 0) or 0),
                "_nio_event": event,
                "_agent_id": credential.agent_id,
                "_our_user_id": credential.matrix_user_id,
            }

        if isinstance(event, RoomMessageText):
            return {
                "kind": "m.room.message.text",
                "event_id": event.event_id,
                "room_id": room_id,
                "sender_id": event.sender,
                "server_ts": event.server_timestamp,
                "body": event.body,
                # Attach the raw nio object too so downstream can pull
                # msgtype-specific fields (formatted_body, m.relates_to,
                # m.mentions.user_ids) without re-parsing.
                "_nio_event": event,
                # Credential surface so parse_event / is_echo don't need
                # a separate credential lookup — they get everything on
                # the raw dict.
                "_agent_id": credential.agent_id,
                "_our_user_id": credential.matrix_user_id,
            }
        # Media events (m.image / m.file / m.audio / m.video) — all share
        # the RoomMessageMedia base. We carry the mxc URI + the info block
        # (mimetype / size) forward; parse_event turns them into the
        # base's attachment_refs and fetch_attachments does the
        # authenticated download. mimetype here is only a hint —
        # _persist_attachment re-sniffs the real MIME from the bytes.
        if isinstance(event, RoomMessageMedia):
            source = getattr(event, "source", None) or {}
            inner = source.get("content") or {}
            info = inner.get("info") or {}
            mxc_url = getattr(event, "url", "") or inner.get("url", "") or ""
            return {
                "kind": "m.room.message.media",
                "event_id": event.event_id,
                "room_id": room_id,
                "sender_id": event.sender,
                "server_ts": event.server_timestamp,
                # body is the filename / description for media events.
                "body": event.body or "",
                "mxc_url": mxc_url,
                "mimetype": info.get("mimetype", "") or "",
                "size": int(info.get("size", 0) or 0),
                "msgtype": inner.get("msgtype", "") or "",
                "_nio_event": event,
                "_agent_id": credential.agent_id,
                "_our_user_id": credential.matrix_user_id,
            }
        # Explicit no-op for unknown types — cheap to log at debug so a
        # future "why isn't my agent seeing X" can be traced.
        logger.debug(
            f"[matrix:{credential.agent_id}] skipping event "
            f"type={type(event).__name__}"
        )
        return None

    # ────────────────────────────────────────────────────────────────────
    # parse_event (text + media)
    # ────────────────────────────────────────────────────────────────────

    def parse_event(  # type: ignore[override]
        self, raw: dict
    ) -> Optional[ParsedMessage]:
        """Convert a wrapped event dict into a ParsedMessage.

        Handles three shapes emitted by :meth:`_wrap_event`:
          - ``m.room.message.text`` → plain text
          - ``m.room.message.compound`` → NarraMessenger multimodal: the
            real user text becomes ``content`` and the preview's mxc becomes
            an attachment_ref (the actual way pictures/files arrive)
          - ``m.room.message.media`` → standard inline media (kept for any
            room that DOES send real m.image events)

        The latter two populate ``raw["attachment_refs"]`` so the base's
        ``fetch_attachments`` downloads the mxc payload.

        Own-message echo drop is done later via :meth:`is_echo` on the
        ParsedMessage; here we let all senders through. Mention filtering
        for groups lives in ``_classify`` (silent-batch vs full run).
        """
        kind = raw.get("kind")
        if kind not in (
            "m.room.message.text",
            "m.room.message.compound",
            "m.room.message.media",
        ):
            return None

        event_id = raw.get("event_id") or ""
        room_id = raw.get("room_id") or ""
        sender_id = raw.get("sender_id") or ""
        if not event_id or not room_id or not sender_id:
            return None

        # Room shape from the cache populated by _apply_member_event
        # during the sync loop. 0 (unknown) treated as GROUP so we don't
        # accidentally auto-reply to a room we can't verify — the
        # authoritative check happens again in _process_message via
        # _classify, which does an async fallback lookup on cache miss.
        member_count = self._room_member_count.get(room_id, 0)
        chat_type = ChatType.PRIVATE if member_count == 2 else ChatType.GROUP

        # Display name from the cache (populated by state events); fall
        # back to MXID until the roster arrives.
        sender_name = self._display_name_cache.get(
            (room_id, sender_id), sender_id
        )

        # event_id is the natural dedup key across Matrix — same event
        # replayed after a since_token rewind hashes to the same key in
        # the base's dedup store.
        timestamp_ms = int(raw.get("server_ts") or 0)

        if kind == "m.room.message.text":
            body = raw.get("body") or ""
            if not body.strip():
                return None
            return ParsedMessage(
                message_id=event_id,
                chat_id=room_id,
                sender_id=sender_id,
                sender_name=sender_name,
                content=body,
                content_type=MessageContentType.TEXT,
                chat_type=chat_type,
                timestamp_ms=timestamp_ms,
                raw=raw,
            )

        # ── NarraMessenger compound (real user text + media preview) ────
        if kind == "m.room.message.compound":
            text = raw.get("text") or ""
            mxc_url = raw.get("mxc_url") or ""
            if not text.strip() and not mxc_url:
                return None
            if not mxc_url:
                # Text-only compound → an ordinary text turn.
                return ParsedMessage(
                    message_id=event_id,
                    chat_id=room_id,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    content=text,
                    content_type=MessageContentType.TEXT,
                    chat_type=chat_type,
                    timestamp_ms=timestamp_ms,
                    raw=raw,
                )
            mimetype = raw.get("mimetype") or ""
            original_name = raw.get("file_name") or mxc_url.rsplit("/", 1)[-1]
            compound_raw = dict(raw)
            compound_raw["attachment_refs"] = [
                {
                    "kind": "media",
                    "mxc_url": mxc_url,
                    "original_name": original_name,
                    "mime_hint": mimetype,
                    "size_hint": int(raw.get("size", 0) or 0),
                }
            ]
            return ParsedMessage(
                message_id=event_id,
                chat_id=room_id,
                sender_id=sender_id,
                sender_name=sender_name,
                # The compound's text IS a real caption/question — keep it as
                # content (unlike inline media, whose body is just a filename).
                content=text,
                content_type=_media_content_type("", mimetype),
                chat_type=chat_type,
                timestamp_ms=timestamp_ms,
                raw=compound_raw,
            )

        # ── standard inline media ───────────────────────────────────────
        mxc_url = raw.get("mxc_url") or ""
        if not mxc_url:
            # A media msgtype with no mxc URI is unusable — nothing to
            # download. Drop rather than emit a content-less turn.
            return None

        mimetype = raw.get("mimetype") or ""
        msgtype = raw.get("msgtype") or ""
        content_type = _media_content_type(msgtype, mimetype)

        # body is the filename for media events — carry it as the
        # attachment's original_name, NOT as message text (a bare
        # filename is not something the agent should "read" as a caption).
        original_name = raw.get("body") or mxc_url.rsplit("/", 1)[-1]
        refs = [
            {
                "kind": "media",
                "mxc_url": mxc_url,
                "original_name": original_name,
                "mime_hint": mimetype,
                "size_hint": int(raw.get("size", 0) or 0),
            }
        ]
        # Copy so we don't mutate the dict the base still holds for dedup.
        media_raw = dict(raw)
        media_raw["attachment_refs"] = refs

        return ParsedMessage(
            message_id=event_id,
            chat_id=room_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content="",  # caption-less; the attachment marker carries it
            content_type=content_type,
            chat_type=chat_type,
            timestamp_ms=timestamp_ms,
            raw=media_raw,
        )

    # ────────────────────────────────────────────────────────────────────
    # fetch_attachments — mxc download → _persist_attachment → workspace
    # ────────────────────────────────────────────────────────────────────

    async def fetch_attachments(  # type: ignore[override]
        self, message: ParsedMessage, credential: NarramessengerCredential
    ) -> List[Attachment]:
        """Download the message's mxc attachments and persist them.

        Structure mirrors ``DiscordTrigger.fetch_attachments``; the only
        NarraMessenger-specific part is the download source — an
        authenticated Matrix media endpoint instead of a CDN URL. The
        base ``_persist_attachment`` handles MIME sniff / on-disk store /
        audio STT and returns a fully-populated ``Attachment`` whose
        ``synthesize_marker`` later tells the agent the on-disk path +
        "use Read tool to view" — so we must NOT invent our own path or
        file_id here.

        Never raises (base contract): every failure is audited and
        reduced to a partial list; the agent run continues against
        ``message.content`` text.
        """
        refs = (message.raw or {}).get("attachment_refs") or []
        if not refs:
            return []

        from backend.config import settings as backend_settings

        max_bytes = backend_settings.max_upload_bytes

        out: List[Attachment] = []
        for ref in refs:
            mxc_url = ref.get("mxc_url") or ""
            server_name, media_id = _parse_mxc(mxc_url)
            if not server_name or not media_id:
                await self._audit(
                    EVENT_ATTACHMENT_FETCH_FAILED,
                    message_id=message.message_id,
                    agent_id=credential.agent_id,
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    details={"mxc_url": mxc_url, "stage": "parse", "error": "bad_mxc"},
                )
                continue

            original_name = ref.get("original_name") or media_id
            mime_hint = ref.get("mime_hint", "") or ""
            size_hint = int(ref.get("size_hint", 0) or 0)

            # Cheap pre-check on the advertised size so we never start a
            # download the backend would reject anyway.
            if size_hint and size_hint > max_bytes:
                await self._audit(
                    EVENT_INGRESS_DROPPED_OVERSIZED,
                    message_id=message.message_id,
                    agent_id=credential.agent_id,
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    details={
                        "mxc_url": mxc_url,
                        "size_hint": size_hint,
                        "max_upload_bytes": max_bytes,
                        "reason": "backend_max_upload_bytes",
                    },
                )
                continue

            try:
                raw_bytes = await self._download_mxc(
                    credential=credential,
                    server_name=server_name,
                    media_id=media_id,
                    max_bytes=max_bytes,
                )
            except MatrixMediaError as e:
                event = (
                    EVENT_INGRESS_DROPPED_OVERSIZED
                    if e.code == "oversized"
                    else EVENT_ATTACHMENT_FETCH_FAILED
                )
                await self._audit(
                    event,
                    message_id=message.message_id,
                    agent_id=credential.agent_id,
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    details={"mxc_url": mxc_url, "stage": "download", "error": f"{e.code}:{e}"},
                )
                continue

            try:
                att = await self._persist_attachment(
                    agent_id=credential.agent_id,
                    raw_bytes=raw_bytes,
                    original_name=original_name,
                    mime_hint=mime_hint,
                )
            except Exception as e:  # noqa: BLE001
                await self._audit(
                    EVENT_ATTACHMENT_FETCH_FAILED,
                    message_id=message.message_id,
                    agent_id=credential.agent_id,
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    details={"mxc_url": mxc_url, "stage": "persist", "error": f"{type(e).__name__}:{e}"},
                )
                continue

            out.append(att)
            await self._audit(
                EVENT_ATTACHMENT_PERSISTED,
                message_id=message.message_id,
                agent_id=credential.agent_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details={"mxc_url": mxc_url, "file_id": att.file_id, "mime": att.mime_type},
            )

        return out

    async def _download_mxc(
        self,
        *,
        credential: NarramessengerCredential,
        server_name: str,
        media_id: str,
        max_bytes: int,
    ) -> bytes:
        """GET the authenticated Matrix media endpoint, capped at
        ``max_bytes``. Raises :class:`MatrixMediaError` on HTTP error,
        transport error, or when the stream exceeds the cap.

        Split out from ``fetch_attachments`` as the single network seam —
        tests monkeypatch this to avoid touching a homeserver.
        """
        homeserver = (credential.matrix_homeserver_url or "").rstrip("/")
        token = credential.matrix_access_token or ""
        if not homeserver or not token:
            raise MatrixMediaError(
                "no_credential",
                f"missing homeserver({bool(homeserver)})/token({bool(token)})",
            )

        url = homeserver + _MEDIA_DOWNLOAD_PATH.format(
            server_name=server_name, media_id=media_id
        )
        headers = {"Authorization": f"Bearer {token}"}
        timeout = aiohttp.ClientTimeout(total=60)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        raise MatrixMediaError(
                            "http_error", f"status {resp.status}"
                        )
                    chunks: List[bytes] = []
                    total = 0
                    async for chunk in resp.content.iter_chunked(65536):
                        total += len(chunk)
                        if total > max_bytes:
                            raise MatrixMediaError(
                                "oversized",
                                f"stream exceeded {max_bytes} bytes",
                            )
                        chunks.append(chunk)
                    return b"".join(chunks)
        except MatrixMediaError:
            raise
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            raise MatrixMediaError(
                "client_error", f"{type(e).__name__}: {e}"
            ) from e
