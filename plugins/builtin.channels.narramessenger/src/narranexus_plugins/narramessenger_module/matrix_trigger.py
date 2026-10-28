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
     ``notify_owner`` tool call args — no more
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
import time
from dataclasses import dataclass, field, replace
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
    JoinError,
    LocalProtocolError,
    RoomGetStateEventError,
    RoomGetStateEventResponse,
    RoomMemberEvent,
    RoomMessageMedia,
    RoomMessageText,
    RoomSendError,
    RoomSendResponse,
    SyncResponse,
    UnknownEvent,
)

from narranexus.platform.channel.channel_audit_events import (
    EVENT_ATTACHMENT_FETCH_FAILED,
    EVENT_ATTACHMENT_PERSISTED,
    EVENT_INGRESS_DROPPED_OVERSIZED,
    EVENT_TRANSPORT_SEND_FAILED,
)
from narranexus.platform.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
    ChannelHistoryConfig,
)
from narranexus.platform.channel.channel_trigger_base import ChannelTriggerBase
from narranexus.platform.channel.message_source_handler import (
    PLATFORM_REPLY_TEXT_KEY,
)
from narranexus.platform.schema.attachment_schema import Attachment
from narranexus.platform.schema.hook_schema import WorkingSource
from narranexus.platform.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)
from narranexus.platform.schema.runtime_message import MessageType
from narranexus.platform.schema.turn_profile import TurnProfile

from ._matrix_send import (
    MatrixSendError,
    matrix_room_send,
)
from ._narramessenger_credential_manager import (
    NarramessengerCredential,
    NarramessengerCredentialManager,
)
from ._rtc_voice import (
    extract_common_voice_instructions,
    parse_rtc_voice_input,
    split_narra_system_prompt,
)
from ._voice_delivery import VoiceDeliveryBridge
from .narramessenger_context_builder import NarramessengerContextBuilder


def _detect_voice_turn(body: str, source: Any) -> Optional[tuple[str, dict]]:
    """Two-level detection shared by parse_event and the late upgrade.

    Level 1 — strict v1 metadata parses: full voice turn with the four
    binding IDs (correlation, per-call serialization, [voice-timing]).
    Level 2 — metadata failed but the common trigger holds (non-blank
    backend-controlled voice_instructions inside the metadata object):
    DEGRADED voice turn — voice behavior applies, binding IDs stay empty
    and MUST NOT feed correlation or security decisions (handoff §3.4);
    serialization falls back to the per-room key downstream.
    Neither level => None (plain text; voice_instructions absent/blank).
    The transcript may be empty — callers decide to drop or skip.
    """
    if not isinstance(source, dict):
        return None
    rtc = parse_rtc_voice_input(source)
    if rtc is not None:
        envelope, transcript = split_narra_system_prompt(body)
        return transcript, {
            "rtc_session_id": rtc.rtc_session_id,
            "turn_id": rtc.turn_id,
            "invocation_id": rtc.invocation_id,
            "agent_profile_id": rtc.agent_profile_id,
            "voice_instructions": rtc.voice_instructions or envelope,
            "degraded": False,
        }
    instructions = extract_common_voice_instructions(source)
    if instructions is None:
        return None
    envelope, transcript = split_narra_system_prompt(body)
    return transcript, {
        "rtc_session_id": "",
        "turn_id": "",
        "invocation_id": "",
        "agent_profile_id": "",
        "voice_instructions": instructions,
        "degraded": True,
    }


def _is_voice_reply_tool(tool_name: str) -> bool:
    """Reply tools whose delivery THIS trigger owns (marker tools).

    Only these may claim the voice bridge: their text never reaches the
    room unless the trigger sends it, so bridging is delivery — not a
    duplicate. ``narra_send`` is excluded on purpose (it performs its own
    ``room_send``). Exact bare-name or exact ``__``-suffix match,
    mirroring the managed-ingress filter: a future ``narra_reply_*``
    sibling is never collaterally claimed.
    """
    return tool_name in ("speak", "narra_reply") or tool_name.endswith(
        ("__speak", "__narra_reply")
    )


def _voice_profile_for(message: ParsedMessage) -> Optional[TurnProfile]:
    """Map RTC voice detection to the one-shot fast profile.

    The profile exists ONLY for the turn whose event carried valid
    metadata (parse_event stamps ``raw["rtc_voice"]``) — nothing is
    persisted, so the next plain message runs the normal path untouched
    (handoff: the override must never be written to any session store).
    """
    if (message.raw or {}).get("rtc_voice"):
        return TurnProfile.voice_fast()
    return None


_ClassifyTarget = Literal["dm", "group_mention", "group_silent"]


def _format_voice_timing(
    *,
    agent_id: str,
    room_id: str,
    rtc_session_id: str,
    received_at: float,
    applied_at: float,
    request_started_at: float,
    first_delta_at: Optional[float],
    first_sent_at: Optional[float],
    finalized_at: Optional[float],
) -> str:
    """The [voice-timing] log line — handoff section 9's six timestamps as
    durations from matrix_voice_input_received. Grep-stable pure function
    (same treatment as [turn-timing]); missing stamps emit -1.00. Carries
    only non-sensitive correlation IDs — never transcript content."""

    def _d(stamp: Optional[float]) -> float:
        return (stamp - received_at) if stamp is not None else -1.0

    return (
        "[voice-timing] agent={} room={} rtc_session={} "
        "applied_s={:.2f} request_s={:.2f} first_token_s={:.2f} "
        "first_live_s={:.2f} finalized_s={:.2f}".format(
            agent_id,
            room_id,
            rtc_session_id,
            _d(applied_at),
            _d(request_started_at),
            _d(first_delta_at),
            _d(first_sent_at),
            _d(finalized_at),
        )
    )


@dataclass
class _VoiceCallState:
    """Per-call serialization state (F28): one drain loop at a time."""

    running: bool = False
    pending: list = field(default_factory=list)  # (message, sender_name, attachments)


def _merge_voice_batch(batch: list) -> tuple:
    """Merge buffered utterances of one call into a single turn.

    The caller kept talking while the agent worked — the queued
    transcripts concatenate in arrival order into ONE message. The last
    utterance's metadata (event id, rtc ids, timestamps) is the base:
    correlation follows the newest turn_id, matching Hybrid's view that
    earlier turns were superseded. When a batch mixes strict and degraded
    turns this means the newest utterance's metadata wins outright — a
    degraded tail drops the earlier turns' correlation IDs and the
    [voice-timing] line stamps "degraded". Deliberate trade-off:
    correlation follows the newest turn, never a stitched-together one.
    """
    if len(batch) == 1:
        return batch[0]
    messages = [b[0] for b in batch]
    _, last_sender, _ = batch[-1]
    merged_content = "\n".join(
        m.content for m in messages if (m.content or "").strip()
    )
    merged_message = replace(messages[-1], content=merged_content)
    merged_attachments: list = []
    for _, _, atts in batch:
        if atts:
            merged_attachments.extend(atts)
    return merged_message, last_sender, (merged_attachments or None)


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
    - ``platform_reply_text`` — a reply the PLATFORM wrote and ALREADY
      delivered (step_3's 1:1 DM no-reply fallback, which sends through
      ``ChannelSenderRegistry`` itself). Deliberately a separate field from
      ``narra_reply_text``: finalize fresh-sends the latter to the room, so
      reusing it would deliver the same message twice. This one is only
      used as the return value, so the inbox records what was said instead
      of an empty string.
    """
    narra_reply_text: str = ""
    error_seen: bool = False
    last_error_message: str = ""
    platform_reply_text: str = ""
    # F28 voice turn only: the live-delivery bridge. None on text turns —
    # every legacy branch below checks nothing and behaves exactly as
    # before (the guard is "bridge is None", not a mode flag).
    voice_bridge: Optional["VoiceDeliveryBridge"] = None
    # Leaf name of the reply tool that claimed the live voice DELTA
    # stream (first speak/narra_reply deltas win; completed texts are not
    # gated). Keeps two tools' delta streams from interleaving into one
    # segment view.
    voice_stream_tool: str = ""

# Authenticated Matrix media download (MSC3916 / Matrix 1.11). The room's
# homeserver requires a bearer token on media fetches — the legacy
# unauthenticated ``/_matrix/media/r0/download`` path is gone on
# matrix.netmind.chat (verified 2026-06-30). Format-filled with
# ``{server_name}`` + ``{media_id}`` parsed from the ``mxc://`` URI.
_MEDIA_DOWNLOAD_PATH = "/_matrix/client/v1/media/download/{server_name}/{media_id}"

# Localpart prefix the platform mints agent identities under —
# ``@agent-e7726996:matrix.netmind.chat``. The one place that spelling
# lives on the receive path; see ``MatrixTrigger.is_agent_peer``.
AGENT_MXID_PREFIX = "@agent-"

# NarraMessenger's room-kind contract (the rule its own runtimes use), in
# priority order:
#   1. the ``ai.netmind.room_creation.v1`` state event (state_key ""),
#      content ``{"message_mode": "managed", "room_type": "dm" | "group"}``;
#   2. otherwise the joined-member count, NOT counting the appservice bot
#      ``@hybrid-im-bot:<server_name>``. Since 2026-09-16 that bot joins
#      every managed room, so a real 1:1 room has three joined members.
ROOM_CREATION_STATE_TYPE = "ai.netmind.room_creation.v1"
_SERVICE_SENDER_LOCALPART = "hybrid-im-bot"
_ROOM_KINDS = ("dm", "group")


def _service_sender_for(own_mxid: str) -> str:
    """``@hybrid-im-bot:<server_name>`` on the server ``own_mxid`` lives on.

    Empty when ``own_mxid`` has no server part: nothing is then excluded,
    so an unparseable identity can only make a room look larger.
    """
    _, sep, server_name = (own_mxid or "").partition(":")
    if not sep or not server_name:
        return ""
    return f"@{_SERVICE_SENDER_LOCALPART}:{server_name}"


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

    # ``matrix_since_token`` is rewritten on EVERY sync response (see the
    # field comment in _narramessenger_credential_manager). Left in the
    # breaker fingerprint it would make our own sync traffic look like the
    # owner re-binding, clearing the breaker on the poll right after it
    # tripped — i.e. no breaker at all for this channel.
    BREAKER_VOLATILE_CREDENTIAL_FIELDS = (
        ChannelTriggerBase.BREAKER_VOLATILE_CREDENTIAL_FIELDS
        | frozenset({"matrix_since_token"})
    )

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

    # Bounded joined_members attempts per room-shape lookup. A membership
    # event applied while a lookup is in flight invalidates its response;
    # one immediate re-fetch usually lands in a quiet window, and the bound
    # keeps a room with constant membership churn from looping.
    ROSTER_LOOKUP_ATTEMPTS = 2

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
        # room_id -> MXIDs currently joined. The room's shape (2 = DM,
        # >2 = group) is the size of this set. Member events add/discard
        # MXIDs, so replaying an event is a no-op. A roster built only from
        # sync member events may be PARTIAL (a fresh room's first sync can
        # carry just the agent's own join), so it is a hint until the room
        # appears in ``_room_roster_authoritative``.
        self._room_joined: dict[str, set[str]] = {}
        # Room ids whose roster was replaced by a successful
        # ``joined_members`` REST lookup. One successful lookup per room per
        # process; concurrent workers may each issue one, the first verified
        # roster wins. Member events keep the roster current afterwards.
        self._room_roster_authoritative: set[str] = set()
        # room_id -> roster change counter, bumped by _apply_member_event
        # only when the set actually changes. _get_member_count compares it
        # across the joined_members await: a change means the response may
        # predate a membership event we already applied.
        self._room_roster_gen: dict[str, int] = {}
        # room_id -> MXIDs seen leaving / banned while the room is NOT
        # authoritative, whether or not their join was ever seen. A partial
        # roster cannot tell "never joined" from "join not synced", so such
        # a leave does not change the set (no generation bump) — yet a
        # joined_members snapshot in flight may still list them. An entry
        # added WHILE a lookup is in flight invalidates that response (it
        # is fetched again, like a generation change); entries recorded
        # before the request, including initial-sync tombstones, never do.
        # The room's entry is dropped once the roster is adopted. A later
        # join removes the MXID again.
        self._room_left_unverified: dict[str, set[str]] = {}
        # room_id -> room kind declared by the room's
        # ``ai.netmind.room_creation.v1`` state. Outranks the member count.
        # Written by the sync loop (state block + timeline) and by the
        # REST lookup in _ensure_room_kind.
        self._room_kind: dict[str, Literal["dm", "group"]] = {}
        # Room ids whose creation state we have a definitive answer for:
        # seen on /sync, fetched, or a 404 (the room has none). A transient
        # REST failure leaves the room out so the next message asks again.
        # Same lifecycle as the roster authority (_drop_roster_authority).
        self._room_kind_checked: set[str] = set()
        # room_id -> counter bumped by _drop_roster_authority. A creation
        # state response whose request started before a drop predates it
        # and is not recorded.
        self._room_kind_gen: dict[str, int] = {}
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

        # ── F28 voice-call serialization ─────────────────────────────────
        # Keyed by agent_id:room_id — room ≡ call under the 1:1 gate, so
        # strict and degraded turns of one call share the key. One worker
        # holds a call's drain loop; overlapping utterances buffer and
        # merge into one follow-up turn. Entries die when the call idles.
        self._voice_calls: dict[str, _VoiceCallState] = {}

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
        self, credential: NarramessengerCredential, reason: str = ""
    ) -> None:
        if not self._db:
            return
        mgr = NarramessengerCredentialManager(self._db)
        ok = await mgr.set_enabled(credential.agent_id, False, reason=reason)
        self.log_disable_outcome("narramessenger", credential.agent_id, ok, reason)

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

    def is_agent_peer(self, message: ParsedMessage) -> bool:  # type: ignore[override]
        """NarraMessenger encodes the answer in the MXID.

        The platform mints agent identities as ``@agent-<id>:<homeserver>``
        (see ``_narramessenger_service._MATRIX_USER_RE``), so "am I talking
        to a machine?" is a prefix check rather than a guess. Humans on the
        same homeserver get ordinary localparts.

        This is the channel where it matters most: it is the one place two
        agents routinely hold a 1:1 conversation with nobody else in the
        room, and the 8/14 ping-pong loop was exactly that — two agents in
        a NarraMessenger DM reciting at each other for 70 hours. Saying
        "the far side is an agent" tightens the ingress breaker, disables
        the invent-a-reply DM fallback, and tells the model it may stay
        silent.
        """
        return message.sender_id.startswith(AGENT_MXID_PREFIX)

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

        Called from the sync loop for both state-block events and timeline
        events. Idempotent — the roster is a set keyed by MXID, so replaying
        the same event (state block + timeline, since-token rewind) leaves
        it unchanged.

        Membership (the event's NEW state; ``prev_membership`` is not
        consulted):
          - ``join``  → add the MXID to the roster; refresh the name
          - ``leave`` (incl. kick) → discard the MXID; drop the name entry
          - ``ban``   → discard the MXID
          - ``invite`` / ``knock`` → roster unchanged (not joined yet)

        This only edits the roster; it never makes it authoritative. A
        roster seeded purely from sync events can be partial, so
        :meth:`_get_member_count` still performs the one-time REST lookup.
        A real change bumps the room's roster generation (so an in-flight
        lookup knows its response may be stale). In a non-authoritative
        room a ``leave`` / ``ban`` is also recorded in
        ``_room_left_unverified`` (and a ``join`` clears it), so a leave
        synced while a lookup is in flight invalidates that lookup even
        when the leaver's join was never synced. Leaves recorded before
        the request (including initial-sync tombstones) do not: a snapshot
        taken after them already reflects them.

        One of our own agents leaving or being banned drops the room's
        authority, but this only fires when that event shows up in a
        joined room's timeline (e.g. another of our agents is still in the
        room): our own leave is normally delivered under ``rooms.leave``,
        which the sync loop does not walk. The reliable path is the
        auto-join in :meth:`connect`, which drops authority for every room
        it (re)joins.
        """
        mxid = event.state_key or event.sender or ""
        if not mxid:
            return
        membership = event.membership or ""
        content = event.content if isinstance(event.content, dict) else {}
        name = content.get("displayname") or ""

        # Display name: update on any join / knock-with-name, drop on leave.
        if membership == "join" and name:
            self._display_name_cache[(room_id, mxid)] = name
        elif membership == "leave":
            self._display_name_cache.pop((room_id, mxid), None)

        joined = self._room_joined.get(room_id)
        changed = False
        if membership == "join":
            if joined is None:
                joined = self._room_joined[room_id] = set()
            if mxid not in joined:
                joined.add(mxid)
                changed = True
            left = self._room_left_unverified.get(room_id)
            if left is not None:
                left.discard(mxid)
        elif membership in ("leave", "ban"):
            if joined is not None and mxid in joined:
                joined.discard(mxid)
                changed = True
            if mxid in self._own_mxids():
                self._drop_roster_authority(room_id)
            elif room_id not in self._room_roster_authoritative:
                self._room_left_unverified.setdefault(room_id, set()).add(mxid)
        if changed:
            self._room_roster_gen[room_id] = self._room_roster_gen.get(room_id, 0) + 1

    def _drop_roster_authority(self, room_id: str) -> None:
        """Make the next classification re-verify ``room_id`` via REST.

        Also forgets the room's unverified leaves. This is bookkeeping:
        only leaves recorded while a lookup is in flight invalidate it, so
        older entries have no effect anyway; dropping them keeps the map
        from carrying state for a stretch we no longer trust.
        """
        self._room_roster_authoritative.discard(room_id)
        self._room_left_unverified.pop(room_id, None)
        # The creation state is re-read with the roster: we may have missed
        # a change while we were out of the room.
        self._room_kind.pop(room_id, None)
        self._room_kind_checked.discard(room_id)
        self._room_kind_gen[room_id] = self._room_kind_gen.get(room_id, 0) + 1

    @staticmethod
    def _is_room_creation_event(event: Any) -> bool:
        """True for an ``ai.netmind.room_creation.v1`` event from /sync.

        nio has no class for the custom state type and parses it as
        ``UnknownEvent``.
        """
        return (
            isinstance(event, UnknownEvent)
            and event.type == ROOM_CREATION_STATE_TYPE
        )

    def _apply_room_creation_event(self, room_id: str, event: Any) -> None:
        """Record the room kind from a synced creation state event.

        Only the ``state_key == ""`` instance is the room's creation state.
        The latest one wins; a ``room_type`` outside ``dm`` / ``group``
        clears the kind (count fallback) rather than keeping a stale one.
        """
        source = getattr(event, "source", None)
        if not isinstance(source, dict) or source.get("state_key") != "":
            return
        self._set_room_kind(room_id, source.get("content"))

    def _set_room_kind(self, room_id: str, content: Any) -> None:
        room_type = content.get("room_type") if isinstance(content, dict) else None
        if room_type in _ROOM_KINDS:
            self._room_kind[room_id] = room_type
        else:
            self._room_kind.pop(room_id, None)
        self._room_kind_checked.add(room_id)

    def _own_mxids(self) -> set[str]:
        """MXIDs of the agents this trigger currently runs a client for."""
        return {
            user_id
            for user_id in (getattr(c, "user_id", None) for c in self._clients.values())
            if user_id
        }

    def _known_member_count(self, room_id: str) -> int:
        """Raw size of the roster seen so far, authoritative or not.

        Includes the ``@hybrid-im-bot`` service sender; 0 for a room we
        have no member data for. Room shape decisions use
        :meth:`_effective_member_count` / :meth:`_hinted_private`, which
        exclude it.
        """
        return len(self._room_joined.get(room_id, ()))

    def _service_sender_mxid(self, credential: NarramessengerCredential) -> str:
        """The appservice bot MXID on the agent's own homeserver."""
        return _service_sender_for(credential.matrix_user_id or "")

    def _effective_member_count(
        self, room_id: str, credential: NarramessengerCredential
    ) -> int:
        """Roster size without the ``@hybrid-im-bot`` service sender.

        Exact MXID match only: a lookalike (another localpart, or the same
        localpart on another server) is a real member and is counted.
        """
        return self._count_without_service_sender(
            room_id, self._service_sender_mxid(credential)
        )

    def _count_without_service_sender(self, room_id: str, service_sender: str) -> int:
        roster = self._room_joined.get(room_id, ())
        return len(roster) - (1 if service_sender and service_sender in roster else 0)

    def _hinted_private(self, room_id: str, own_mxid: str) -> bool:
        """Pre-classification room shape for :meth:`parse_event`.

        Same rule order as routing, on whatever is known so far: a recorded
        room kind decides, else the (possibly partial) roster without the
        service sender must be exactly 2. Unknown reads as group.
        """
        kind = self._room_kind.get(room_id)
        if kind is not None:
            return kind == "dm"
        return (
            self._count_without_service_sender(room_id, _service_sender_for(own_mxid))
            == 2
        )

    def _hinted_voice_one_to_one(self, room_id: str, own_mxid: str) -> bool:
        """parse_event's RTC voice gate: hinted PRIVATE AND exactly two
        participants on the known roster (service sender excluded).

        Stricter than :meth:`_hinted_private` on purpose: a declared
        ``room_type: dm`` routes a room as dm whatever its size, but the
        voice register is an injection surface and stays 1:1-only.
        """
        return (
            self._hinted_private(room_id, own_mxid)
            and self._count_without_service_sender(
                room_id, _service_sender_for(own_mxid)
            )
            == 2
        )

    async def _resolve_room_shape(
        self,
        client: AsyncClient,
        room_id: str,
        credential: NarramessengerCredential,
    ) -> tuple[Optional[Literal["dm", "group"]], int]:
        """Return ``(room_kind, effective_member_count)`` for routing.

        Verifies the roster first (:meth:`_get_member_count` also backfills
        display names, which mention detection needs), then the creation
        state. The count is 0 when the roster could not be verified; the
        kind is None when the room declares none or the lookup failed.
        """
        count = await self._get_member_count(client, room_id)
        await self._ensure_room_kind(client, room_id)
        effective = self._effective_member_count(room_id, credential) if count else 0
        return self._room_kind.get(room_id), effective

    async def _ensure_room_kind(self, client: AsyncClient, room_id: str) -> None:
        """Fetch the room's creation state once, unless already known.

        Only a 2xx response whose content is a JSON object without
        ``errcode`` is a successful lookup. nio maps just 404 to
        ``RoomGetStateEventError``; for 403 / 429 / 5xx it returns a
        ``RoomGetStateEventResponse`` whose ``content`` is the error body,
        so the class alone proves nothing. A 404 means the room has no
        such state (kind stays unknown, not asked again). Any other
        outcome, including a raised exception, records nothing, so the
        next message retries.

        A creation event the sync loop applies while the request is in
        flight is newer and wins; a response whose request started before
        :meth:`_drop_roster_authority` ran for the room is discarded.
        """
        if room_id in self._room_kind_checked:
            return
        generation = self._room_kind_gen.get(room_id, 0)
        try:
            resp = await client.room_get_state_event(
                room_id, ROOM_CREATION_STATE_TYPE, ""
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[matrix] room_get_state_event({room_id}, "
                f"{ROOM_CREATION_STATE_TYPE}) raised: {type(e).__name__}: {e}"
            )
            return
        if (
            room_id in self._room_kind_checked
            or self._room_kind_gen.get(room_id, 0) != generation
        ):
            return
        http_status = getattr(getattr(resp, "transport_response", None), "status", None)
        content = getattr(resp, "content", None)
        if (
            isinstance(resp, RoomGetStateEventResponse)
            and isinstance(http_status, int)
            and http_status // 100 == 2
            and isinstance(content, dict)
            and "errcode" not in content
        ):
            self._set_room_kind(room_id, content)
            return
        errcode = getattr(resp, "status_code", None)
        if http_status == 404 or (
            isinstance(resp, RoomGetStateEventError) and errcode == "M_NOT_FOUND"
        ):
            self._room_kind_checked.add(room_id)
            return
        if errcode is None and isinstance(content, dict):
            errcode = content.get("errcode")
        logger.warning(
            f"[matrix] room_get_state_event({room_id}, "
            f"{ROOM_CREATION_STATE_TYPE}) failed (http={http_status}, "
            f"errcode={errcode or 'unknown'}); retried on the next message"
        )

    async def _get_member_count(
        self, client: AsyncClient, room_id: str
    ) -> int:
        """Return the authoritative joined-member count for ``room_id``.

        The first call for a room does one authenticated GET to
        ``/_matrix/client/v3/rooms/{room}/joined_members`` and REPLACES the
        roster with the response, even if sync member events already
        seeded it — those can be partial (a freshly auto-joined room's
        first sync may carry only the agent's own join, which used to pin
        a new 1:1 room at 1 member and classify it group_silent forever).
        After that the room is authoritative and later calls return the
        roster size, kept current by :meth:`_apply_member_event`.

        Returns 0 when the lookup raises or yields no members, WITHOUT
        marking the room authoritative, so the next message retries.
        Callers must treat 0 as "unknown, default to group_silent" so we
        NEVER accidentally auto-reply to a room whose shape we can't verify.

        Races across the await (the sync loop and other workers keep
        running while the REST call is in flight):
          - another lookup for the same room verified it first → keep that
            roster (since maintained by member events) and return its size;
            ours started earlier or concurrently and must not overwrite it.
          - a member event changed the roster meanwhile, or a leave/ban
            arrived for a member the partial roster never saw join → the
            response may predate it, so it is discarded and the lookup is
            repeated immediately, at most :attr:`ROSTER_LOOKUP_ATTEMPTS`
            times in total. If every attempt is invalidated, return 0 and
            stay non-authoritative (retried on the next message).
        """
        for attempt in range(1, self.ROSTER_LOOKUP_ATTEMPTS + 1):
            if room_id in self._room_roster_authoritative:
                return self._known_member_count(room_id)
            generation = self._room_roster_gen.get(room_id, 0)
            left_before = set(self._room_left_unverified.get(room_id, ()))
            members = await self._fetch_joined_members(client, room_id)
            if room_id in self._room_roster_authoritative:
                return self._known_member_count(room_id)
            if members is None:
                return 0
            if self._room_roster_gen.get(room_id, 0) != generation:
                logger.info(
                    f"[matrix] joined_members({room_id}) discarded "
                    f"(attempt {attempt}/{self.ROSTER_LOOKUP_ATTEMPTS}): "
                    f"membership changed while the lookup was in flight"
                )
                continue
            # A leave of a member we never saw join changes neither the set
            # nor the generation, but the snapshot may still list them (or
            # list them correctly, if they came straight back). Fetch again:
            # this leave is then in the next attempt's left_before, so it does
            # not re-trigger. The entry is only dropped on adoption below.
            left_in_flight = (
                self._room_left_unverified.get(room_id, set()) - left_before
            )
            if left_in_flight:
                logger.info(
                    f"[matrix] joined_members({room_id}) discarded "
                    f"(attempt {attempt}/{self.ROSTER_LOOKUP_ATTEMPTS}): "
                    f"a member left while the lookup was in flight"
                )
                continue
            self._room_left_unverified.pop(room_id, None)
            self._room_joined[room_id] = {
                m.user_id for m in members if getattr(m, "user_id", None)
            }
            self._room_roster_authoritative.add(room_id)
            # Backfill display names from the roster too. _process_message
            # primes the room shape before mention detection, so the first
            # message in a fresh room already sees our display name.
            for m in members:
                mxid = getattr(m, "user_id", None)
                name = getattr(m, "display_name", None)
                if mxid and name:
                    self._display_name_cache[(room_id, mxid)] = name
            return self._known_member_count(room_id)
        return 0

    async def _fetch_joined_members(
        self, client: AsyncClient, room_id: str
    ) -> Optional[list]:
        """One ``joined_members`` call; the member list, or None on failure.

        An empty roster counts as a failure: we are a member ourselves, so
        it is not a shape, and treating it as one would cache 0.
        """
        try:
            resp = await client.joined_members(room_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[matrix] joined_members({room_id}) raised: "
                f"{type(e).__name__}: {e}"
            )
            return None
        # nio returns JoinedMembersResponse (success) or JoinedMembersError
        # (failure). Success has .members (list of RoomMember dataclasses).
        members = getattr(resp, "members", None)
        if not isinstance(members, list) or not any(
            getattr(m, "user_id", None) for m in members
        ):
            logger.warning(
                f"[matrix] joined_members({room_id}) returned no members "
                f"(status={getattr(resp, 'status_code', 'unknown')})"
            )
            return None
        return members

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
        member_count: Optional[int] = None,
        room_kind: Optional[Literal["dm", "group"]] = None,
    ) -> _ClassifyTarget:
        """Route a message to the right handler.

        Rule order (NarraMessenger's room-kind contract):

        - Creation state ``room_type: dm`` → DM, whatever the count
        - Creation state ``room_type: group`` → mention rules below
        - No creation state → member count without the ``@hybrid-im-bot``
          service sender: 2 → DM (1:1 owner-agent room, or any 2-person
          room); anything else → mention rules
        - Mention rules: agent explicitly mentioned → group_mention,
          otherwise group_silent. An unknown count (0) without a mention
          is group_silent (safe default: don't auto-reply to a room we
          can't verify the shape of; the silent-batch path still writes
          memory)

        Args:
            mentioned: If already computed by the caller (e.g. the
                authorize-event gate already needed it for its payload),
                reuse rather than re-scanning the body. If None, we
                compute via :meth:`_is_mentioning_us`.
            member_count: Effective member count (service sender
                excluded) the caller already resolved via
                :meth:`_resolve_room_shape`, reused so a failed lookup is
                not silently retried within one message. If None, the
                shape (count AND ``room_kind``) is resolved here and the
                ``room_kind`` argument is ignored.
            room_kind: Creation-state kind from the same resolution; None
                means the room declares none. Passing it without
                ``member_count`` raises ``ValueError``: it would be
                silently discarded by the re-resolution.

        The production caller (:meth:`_process_message`) always passes
        ``member_count``, ``room_kind`` and ``mentioned``; the ``None``
        fallbacks exist for direct unit tests of the classification rules.
        """
        if room_kind is not None and member_count is None:
            raise ValueError("room_kind requires member_count from the same resolution")
        if member_count is None:
            room_kind, member_count = await self._resolve_room_shape(
                client, message.chat_id, credential
            )
        if room_kind == "dm" or (room_kind is None and member_count == 2):
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

        Prefers the roster (sorted), falling back to the display-name
        cache keys when no member data was recorded for the room.

        Narra explicitly says it does NOT trust caller-supplied
        membership as authority (see setup-guide.md `Direct Group
        Context` section) — the server re-verifies. So this is a hint,
        not a source of truth. Empty list is fine; the server will
        still authorize based on its own view of the room.
        """
        roster = self._room_joined.get(room_id)
        if roster:
            return sorted(roster)
        return [
            mxid
            for (rid, mxid) in self._display_name_cache.keys()
            if rid == room_id
        ]

    async def managed_before_run(
        self,
        *,
        agent_id: str,
        message: ParsedMessage,
        db: Any,
        is_mention: bool = True,
    ) -> tuple[bool, str]:
        """Managed-mode inbound gate: run Narra's authorize-event exactly as
        the native path does (fail-closed). Non-mention group traffic mirrors
        SILENT_BYPASS_AUTHORIZE — memory-only handling needs no authorization
        and must not spam the gate."""
        self._managed_bind(db)
        if not is_mention and message.chat_type == ChatType.GROUP:
            return True, ""
        credential = await self._credential_for_agent(agent_id)
        if credential is None:
            # Fail-closed: without the bind credential we cannot authorize.
            return False, (
                "narramessenger authorization unavailable (no active credential)"
            )
        verdict = await self._authorize_event(credential, message, mentioned=is_mention)
        if verdict.allow:
            return True, ""
        if verdict.notice_send and verdict.notice_text:
            # The native path posts the server-suggested denial via the live
            # client; managed mode has no client — same credential, plain
            # HTTP sender.
            try:
                await matrix_room_send(
                    homeserver=credential.matrix_homeserver_url,
                    token=credential.matrix_access_token,
                    room_id=message.chat_id,
                    content={"msgtype": "m.notice", "body": verdict.notice_text},
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[matrix:{agent_id}] managed denial notice send failed: "
                    f"{type(e).__name__}: {e}"
                )
        return False, "narramessenger authorization denied"

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

    async def _send_circuit_refusal(
        self, credential: Any, message: Any, text: str
    ) -> bool:
        """The base's refusal hook: this channel's ``send_channel_reply`` is
        a no-op, so a circuit-breaker refusal goes out through the same
        sender as a normal answer. True iff the homeserver accepted it (a
        False hands the group window back, see the base)."""
        return await self._send_matrix_reply(credential, message.chat_id, text)

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
        # The room shape is resolved BEFORE mention detection: the first
        # lookup of a fresh room backfills display names from the REST
        # roster, which ``@displayname`` detection needs for this very
        # message. _classify then reuses the shape instead of looking up
        # again.
        room_kind, member_count = await self._resolve_room_shape(
            client, message.chat_id, credential
        )
        mentioned = self._is_mentioning_us(message, credential)
        target = await self._classify(
            client,
            message,
            credential,
            mentioned=mentioned,
            member_count=member_count,
            room_kind=room_kind,
        )
        logger.info(
            f"[matrix:{credential.agent_id}] CLASSIFY target={target} "
            f"(room={message.chat_id}, event={message.message_id}, "
            f"mentioned={mentioned}, room_kind={room_kind}, "
            f"members={member_count})"
        )

        # parse_event stamped chat_type (and possibly the voice register)
        # from a roster hint; align the message with the authoritative
        # verdict so every consumer downstream sees the same shape.
        message = self._reconcile_room_shape(message, target)

        if target == "dm":
            # Routing follows the room kind, but the voice register is
            # granted by the EFFECTIVE member count alone: a declared
            # ``room_type: dm`` beats the count (and its sender is not
            # validated), so "dm" no longer implies two participants.
            # _reconcile_room_shape has stamped PRIVATE even when voice is
            # refused here — the intended conservative direction: the turn
            # is answered, as plain text. An unknown count (0) stays closed.
            voice_one_to_one = member_count == 2
            if not voice_one_to_one:
                # parse_event may have stamped voice off a partial roster.
                message = self._strip_voice_stamp(
                    message, f"dm with {member_count} effective members"
                )
            # Cold-roster fallback (review #17): parse_event refuses voice
            # for an unseen room, degrading the FIRST turn of a real call.
            # The authoritative lookup just ran; re-run the same pure
            # detection so a cold cache only costs time, not correctness.
            # None = valid metadata with an envelope-only body → drop the
            # turn, same rule as parse_event (review #21).
            upgraded = self._late_voice_upgrade(
                message, authoritative_dm=voice_one_to_one
            )
            if upgraded is None:
                return
            message = upgraded

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
            # The silent path returns BEFORE super()._process_message, so
            # the base class's ingress gate never sees it — but it still
            # runs a pipeline (memory ingestion), so a repeat storm in a
            # group room would still burn one. Gate it here; the reply
            # paths below inherit the base's call via super().
            if not await self._ingress_admitted(credential, message):
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
        # F28 voice turns serialize per call BEFORE dispatch: the trigger's
        # worker pool runs same-room messages concurrently (by design for
        # text), but two overlapping voice runs would interleave two TTS
        # streams on one call. Modeled on the group_silent per-(agent, room)
        # buffer; the call key is agent_id:room_id (room ≡ call under the
        # 1:1 gate).
        if (getattr(message, "raw", None) or {}).get("rtc_voice"):
            if not self.STREAMING_ENABLED:
                # The streaming kill switch governs voice too: live
                # delivery IS streaming, so degrade the whole turn to a
                # plain text turn (no voice register, no speak surface,
                # no serialization) instead of half-activating voice on
                # the atomic path.
                message = replace(
                    message,
                    raw={
                        k: v
                        for k, v in (message.raw or {}).items()
                        if k != "rtc_voice"
                    },
                )
            else:
                return await self._run_voice_serialized(
                    credential, message, sender_name, attachments=attachments
                )
        if self.STREAMING_ENABLED:
            return await self._build_and_run_agent_streaming(
                credential, message, sender_name, attachments=attachments
            )
        return await self._build_and_run_agent_atomic(
            credential, message, sender_name, attachments=attachments
        )

    def _reconcile_room_shape(
        self, message: ParsedMessage, target: _ClassifyTarget
    ) -> ParsedMessage:
        """Re-stamp the room shape parse_event guessed from the roster hint.

        ``dm`` → PRIVATE. Any group verdict (including the fail-closed
        unknown-shape group_silent) → GROUP, matching how the room is
        being routed. Consumers keyed on ``chat_type`` (context builder
        room_type, inbox thread type, circuit-refusal throttling) would
        otherwise act on the guess.

        A group verdict also undoes a voice stamp: parse_event's 1:1 voice
        gate may have read a partial roster as 2 members, and RTC metadata
        has no source binding, so a group turn must run as plain text with
        its original body. A dm verdict keeps the stamp here; the dm branch
        of :meth:`_process_message` strips it unless the effective member
        count is exactly 2, and :meth:`_late_voice_upgrade` handles the
        upgrade direction.
        """
        if target == "dm":
            if message.chat_type == ChatType.PRIVATE:
                return message
            return replace(message, chat_type=ChatType.PRIVATE)
        message = self._strip_voice_stamp(message, f"room classified {target}")
        if message.chat_type == ChatType.GROUP:
            return message
        return replace(message, chat_type=ChatType.GROUP)

    @staticmethod
    def _strip_voice_stamp(message: ParsedMessage, reason: str) -> ParsedMessage:
        """Undo parse_event's voice stamp: plain text with the original body.

        Voice mode is 1:1-only; ``chat_type`` is left as it is.
        """
        raw = message.raw or {}
        if not raw.get("rtc_voice"):
            return message
        logger.info(
            f"[matrix:{message.chat_id}] voice stamp dropped: {reason}, "
            f"voice mode is 1:1-only"
        )
        return replace(
            message,
            content=raw.get("body") or message.content,
            raw={k: v for k, v in raw.items() if k != "rtc_voice"},
        )

    def _late_voice_upgrade(
        self, message: ParsedMessage, *, authoritative_dm: bool
    ) -> Optional[ParsedMessage]:
        """Re-run voice detection once the room is verified as 1:1.

        parse_event's voice gate is conservative on a cold roster cache
        (unknown member count refuses voice); this recovers the voice
        register for verified 1:1 rooms. Inert on plain messages and
        idempotent on already-stamped ones.

        ``authoritative_dm`` must be the CALLER'S declaration that
        _classify returned "dm" AND the verified effective member count
        (service sender excluded) is exactly 2. A dm verdict alone is not
        enough: a declared ``room_type: dm`` routes larger rooms as dm too.
        The 1:1 injection boundary is asserted here, not by call-site
        position alone (review #23). Returns None
        for a metadata-valid but envelope-only body: the turn is DROPPED,
        same rule as parse_event — the envelope must never run the agent
        as user content (review #21).
        """
        raw = message.raw or {}
        if not authoritative_dm or raw.get("rtc_voice"):
            return message
        detected = _detect_voice_turn(
            message.content, getattr(raw.get("_nio_event"), "source", None)
        )
        if detected is None:
            return message
        transcript, rtc_voice = detected
        if not transcript.strip():
            logger.info(
                f"[matrix:{getattr(message, 'chat_id', '?')}] late voice "
                f"turn with empty transcript dropped"
            )
            return None
        upgraded_raw = dict(raw)
        upgraded_raw["rtc_voice"] = rtc_voice
        logger.info(
            f"[matrix:{getattr(message, 'chat_id', '?')}] voice turn "
            f"late-upgraded after authoritative dm classify"
        )
        return replace(message, content=transcript, raw=upgraded_raw)

    async def _run_voice_serialized(
        self,
        credential: NarramessengerCredential,
        message: ParsedMessage,
        sender_name: str,
        *,
        attachments: Optional[list] = None,
    ) -> str:
        """Serialize voice turns per room ≡ per call under the 1:1 gate.

        One worker holds a call's drain loop; messages arriving while a
        run is active are buffered and merged into ONE follow-up turn
        (consecutive utterances concatenate — the caller kept talking).
        Different rooms (hence different calls) stay fully parallel.
        State is per-trigger-instance and dies with the call going idle.
        """
        # One live RTC call per room is guaranteed by the PRIVATE 1:1 gate, so the
        # room is the call: keying on it serializes strict and degraded turns of the
        # SAME call together (a per-session key would split-brain the moment one
        # utterance's metadata fails validation — two concurrent TTS streams).
        key = f"{getattr(credential, 'agent_id', '?')}:{message.chat_id}"
        call = self._voice_calls.setdefault(key, _VoiceCallState())
        call.pending.append((message, sender_name, attachments))
        if call.running:
            # The draining worker will pick this up; nothing to return for
            # the buffered message itself (its content is merged).
            return ""
        call.running = True
        last_text = ""
        try:
            while call.pending:
                batch = call.pending
                call.pending = []
                merged_message, merged_sender, merged_attachments = (
                    _merge_voice_batch(batch)
                )
                last_text = await self._build_and_run_agent_streaming(
                    credential,
                    merged_message,
                    merged_sender,
                    attachments=merged_attachments,
                )
        finally:
            call.running = False
            if not call.pending:
                self._voice_calls.pop(key, None)
        return last_text

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
        streaming state machine. A circuit-breaker refusal is NOT sent here:
        the base gate already delivered it through ``_send_circuit_refusal``
        (throttled per group window), so sending the returned text again
        would double it and bypass the throttle (#394 fifth review N-3).
        """
        output = await self._run_agent_turn(
            credential, message, sender_name, attachments=attachments
        )
        text = output.text
        if output.refused:
            return text
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
        from narranexus.platform.agent_runtime.client import (
            get_agent_runtime_client,
        )
        from narranexus.platform.schema.channel_tag import ChannelTag

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
            is_agent_peer=self.is_agent_peer(message),
        )
        tagged_prompt = f"{channel_tag.format()}\n{prompt}"
        owner_user_id = await self._resolve_agent_owner(agent_id) or agent_id

        # Through the base builder. This streaming path is the DEFAULT
        # (STREAMING_ENABLED), and hand-rolling the dict here is how
        # NarraMessenger missed the 2026-08-06 turn envelope: DMs read as
        # group rooms, so the 1:1 no-reply fallback never ran — and this
        # path's terminal case for "no reply + no error" is precisely the
        # NO-OP silence that fallback exists to remove.
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
        )

        # F28 voice turn: the one-shot fast profile rides this run only;
        # rtc_voice reaches modules via extra_data (expressive surface
        # ordering) without any session write.
        _t_voice_received = time.monotonic()
        turn_profile = _voice_profile_for(message)
        _t_voice_applied = time.monotonic()
        rtc_voice = (message.raw or {}).get("rtc_voice")
        if rtc_voice:
            extra_data["rtc_voice"] = rtc_voice

        state = _StreamReplyState()

        if rtc_voice:
            # The bridge owns the live m.text / m.replace lifecycle for
            # this turn; its sender is a closure over this credential so
            # the pure lifecycle logic stays homeserver-free (testable).
            room_id_for_send = message.chat_id

            async def _voice_send(content: dict) -> str:
                return await matrix_room_send(
                    homeserver=credential.matrix_homeserver_url,
                    token=credential.matrix_access_token,
                    room_id=room_id_for_send,
                    content=content,
                )

            state.voice_bridge = VoiceDeliveryBridge(send=_voice_send)

        run_kwargs: dict[str, Any] = {}
        if turn_profile is not None:
            run_kwargs["turn_profile"] = turn_profile

        # The base's circuit-breaker gate — this path replaces the base's
        # run_and_collect with run_stream, so it calls the gate itself. The
        # gate sends the refusal (throttled per group window) through
        # ``_send_circuit_refusal``, overridden below for this channel.
        admission, refusal = await self._circuit_admission(
            credential, message, agent_id
        )
        if refusal is not None:
            return refusal

        client_stream = get_agent_runtime_client().run_stream(
            agent_id=agent_id,
            user_id=owner_user_id,
            input_content=tagged_prompt,
            working_source=self.working_source,
            trigger_extra_data=extra_data,
            probe_token=admission.probe_token,
            **run_kwargs,
        )
        _t_voice_request = time.monotonic()

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
        finally:
            # run_stream settles every exit it sees; this covers a consumer
            # that stopped iterating before the stream could settle.
            await self._release_unsettled_probe(agent_id, admission)

        # Finalize.
        if state.voice_bridge is not None:
            spoken, finalized_ok = await state.voice_bridge.close()
            logger.info(_format_voice_timing(
                agent_id=agent_id,
                room_id=message.chat_id,
                rtc_session_id=str(
                    (rtc_voice or {}).get("rtc_session_id")
                    or ("degraded" if (rtc_voice or {}).get("degraded") else "")
                ),
                received_at=_t_voice_received,
                applied_at=_t_voice_applied,
                request_started_at=_t_voice_request,
                first_delta_at=state.voice_bridge.first_delta_at,
                first_sent_at=state.voice_bridge.first_sent_at,
                finalized_at=(
                    state.voice_bridge.finalized_at if finalized_ok else None
                ),
            ))
            if spoken:
                if not finalized_ok:
                    # Live lifecycle broke (base or final edit failed) —
                    # handoff 6.3: never leave the answer undelivered; the
                    # plain retry-aware sender is the fallback path.
                    logger.warning(
                        f"[matrix:{credential.agent_id}] voice live "
                        f"delivery failed; falling back to plain send "
                        f"(room={message.chat_id})"
                    )
                    await self._send_matrix_reply(
                        credential, message.chat_id, spoken
                    )
                # Deliberately NOTHING else once the bridge delivered: on
                # a live call the plain sender is a DELIVERY channel (the
                # smoke report's plain-fallback turns were heard by the
                # caller), so any supplement here would be a second
                # playback that reads URLs/code aloud — bypassing the
                # sanitizer's structural guarantee. Known cost: a link the
                # model put in narra_reply does not reach the chat record;
                # tracked in reference/self_notebook/todo/ pending a
                # written worker-consumption contract from Hybrid.
                return spoken
            # Nothing spoken: fall through to the legacy finalize so a
            # narra_reply answer, an error marker, or a silent turn all
            # behave exactly as on a text turn.

        final_text = (state.narra_reply_text or "").strip()
        if final_text:
            # No placeholder to edit — fresh-send via the retry-aware
            # reply sender (same one the atomic path uses).
            await self._send_matrix_reply(
                credential, message.chat_id, final_text
            )
            return final_text

        platform_text = (state.platform_reply_text or "").strip()
        if platform_text:
            # Already in the room: step_3's fallback delivered it through
            # ChannelSenderRegistry before this stream ended. Return it so
            # the base writes the real text to the inbox, and touch the room
            # NOT AT ALL — sending here would double-message the person.
            logger.info(
                f"[matrix:{credential.agent_id}] stream ended with a "
                f"platform-delivered reply; recording it without a room write"
            )
            return platform_text

        await self._finalize_stream_silent(
            credential, message.chat_id, state
        )
        return ""

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
        # F28 voice turn: a reply tool's streamed text argument rides
        # AGENT_REPLY_DELTA (the expressive reply stream). The first
        # trigger-owned reply tool (speak OR narra_reply) to produce
        # output claims the live stream and feeds TTS — models routinely
        # answer voice turns via narra_reply despite the spoken-register
        # instructions (2026-08-13 call: 12/14 turns), and gating on
        # speak alone left those turns waiting for finalize. narra_send
        # stays out: it delivers itself via room_send, so bridging its
        # deltas would double-deliver.
        if state.voice_bridge is not None and mt == MessageType.AGENT_REPLY_DELTA:
            tool = getattr(event, "tool_name", "") or ""
            # The claim key is the LEAF name: deltas carry the qualified
            # spelling while PROGRESS may carry either, and a spelling
            # mismatch would let a completed PROGRESS block that same
            # tool's later deltas (pipeline review Minor #6).
            leaf = tool.rsplit("__", 1)[-1]
            if _is_voice_reply_tool(tool) and (
                not state.voice_stream_tool or state.voice_stream_tool == leaf
            ):
                state.voice_stream_tool = leaf
                await state.voice_bridge.on_reply_delta(
                    call_id=getattr(event, "call_id", "") or "",
                    delta=getattr(event, "delta", "") or "",
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

        # A reply the platform wrote AND already delivered (step_3's 1:1 DM
        # no-reply fallback sends through ChannelSenderRegistry). Recognised
        # here because this streaming path is the DEFAULT one and never
        # reaches `resolve_agent_response` — it returns the text captured
        # mid-stream instead, so without this the turn finalises as silent
        # and the inbox records "" for a message the person did receive.
        # Captured into its OWN field: finalize fresh-sends
        # `narra_reply_text` to the room, so reusing it would deliver the
        # same message a second time.
        platform_text = arguments.get(PLATFORM_REPLY_TEXT_KEY)
        if isinstance(platform_text, str) and platform_text.strip():
            state.platform_reply_text = platform_text
            logger.info(
                f"[matrix:{credential.agent_id}] streaming captured a "
                f"platform-delivered reply (len={len(platform_text)}, "
                f"room={room_id}); no room write from this trigger"
            )
            return

        text = arguments.get("text")
        if not (isinstance(text, str) and text.strip()):
            return

        is_speak = tool_name == "speak" or tool_name.endswith("__speak")
        is_narra_reply = (
            tool_name == "narra_reply" or tool_name.endswith("__narra_reply")
        )

        if state.voice_bridge is not None and _is_voice_reply_tool(tool_name):
            # Every completed reply text enters the bridge — the claim
            # gates DELTA streams only. The VOICE prompt teaches a
            # preannounce-then-answer two-call pattern; when the model
            # splits it across tools the answer must be spoken, not
            # dropped. The identical-text dual-call shape is neutralized
            # by the bridge's exact-equality segment dedup, and same-call
            # correction rides the raw-prefix judge as before.
            if not state.voice_stream_tool:
                # Leaf name — same normalization as the delta claim.
                state.voice_stream_tool = tool_name.rsplit("__", 1)[-1]
            state.voice_bridge.on_segment_text(
                call_id=str(details.get("call_id") or ""), text=text
            )
            # ALSO keep the legacy capture: finalize returns early on any
            # non-empty spoken text, so this cannot double-deliver — but
            # it is the only delivery path left when the spoken form
            # sanitizes to nothing (emoji-only ack, URL-only reply).
            state.narra_reply_text = (
                f"{state.narra_reply_text} {text}".strip()
                if is_speak and state.narra_reply_text
                else text
            )
            logger.info(
                f"[matrix:{credential.agent_id}] reply text via "
                f"{tool_name.rsplit('__', 1)[-1]} bridged for voice "
                f"(len={len(text)}, room={room_id})"
            )
            return

        if is_speak:
            # Text turn: no bridge consumes speak, so without this capture
            # the call would be a dead tool — ok:true returned, nothing
            # delivered, a fully silent round. Deliver via the legacy
            # finalize path instead; segments concatenate (speak is a
            # multi-call tool).
            state.narra_reply_text = (
                f"{state.narra_reply_text} {text}".strip()
                if state.narra_reply_text
                else text
            )
            logger.info(
                f"[matrix:{credential.agent_id}] speak on a text turn "
                f"captured for plain delivery (room={room_id})"
            )
            return

        if is_narra_reply:
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
            # Reached only when nothing was said by ANYONE this turn — the
            # caller returns before this when the platform's own fallback
            # already delivered a reply. Saying "no reply" in that case
            # would send a debugger looking for a silent turn that was in
            # fact answered.
            logger.info(
                f"[matrix:{credential.agent_id}] silent stream — no reply "
                f"from the agent, none written by the platform, no error; "
                f"staying quiet (room={room_id})"
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

        ``narra_reply`` (NOT the generic ``notify_owner``,
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
        # Ordered fold mirroring the STREAMING capture semantics exactly
        # (review #22): narra_reply REPLACES the accumulator (last-writer,
        # like state.narra_reply_text = text), speak APPENDS a segment
        # (review #16 — no bridge runs on the atomic path, so joining the
        # segments into the plain delivery is what keeps speak from being
        # a dead ok:true tool when the STREAMING_ENABLED switch is off).
        # Same call sequence must yield the same answer on both paths.
        accumulated = ""
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item = raw.get("item")
            if not isinstance(item, dict) or item.get("type") != "tool_call_item":
                continue
            tool_name = str(item.get("tool_name") or "")
            is_reply = (
                tool_name == "narra_reply" or tool_name.endswith("__narra_reply")
            )
            is_speak = tool_name == "speak" or tool_name.endswith("__speak")
            if not (is_reply or is_speak):
                continue
            args = item.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:  # noqa: BLE001
                    args = {}
            if not isinstance(args, dict):
                continue
            text = args.get("text")
            if not (isinstance(text, str) and text.strip()):
                continue
            if is_reply:
                accumulated = text
            else:
                accumulated = f"{accumulated} {text}".strip() if accumulated else text
        if accumulated:
            return accumulated
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
                await self.disable_credential(
                    credential,
                    reason="missing matrix credentials (homeserver / user id / access token); re-run the bind flow",
                )
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
            cursor = await self._resume_cursor(mgr, credential)
            is_first_sync = cursor is None
            self._set_session_baseline(agent_id, resumed=cursor is not None)

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
                        join_resp = await client.join(room_id)
                        if isinstance(join_resp, JoinError):
                            # nio reports server-side refusals (M_FORBIDDEN,
                            # rate limits) as a returned JoinError, not an
                            # exception. Same handling as a raised failure:
                            # the invite comes back on the next sync.
                            logger.warning(
                                f"[matrix:{agent_id}] auto-join refused for "
                                f"{room_id}: {join_resp.status_code} "
                                f"{join_resp.message}"
                            )
                            continue
                        # A re-invited room's old roster missed every
                        # membership change while we were out; re-verify
                        # it on the next classification.
                        self._drop_roster_authority(room_id)
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
                    # roster + display name caches are populated BEFORE
                    # any message from the same batch gets classified.
                    # This is NOT guaranteed to be the whole roster (a
                    # freshly joined room's first sync may carry only some
                    # member events); _get_member_count verifies the shape
                    # via joined_members before routing trusts it.
                    state_events = getattr(
                        getattr(room_info, "state", None), "events", []
                    ) or []
                    for event in state_events:
                        if isinstance(event, RoomMemberEvent):
                            self._apply_member_event(room_id, event)
                        elif self._is_room_creation_event(event):
                            self._apply_room_creation_event(room_id, event)

                    # Now walk the timeline. Member events here are LIVE
                    # membership changes (join/leave during this batch);
                    # apply them BEFORE the message events that follow,
                    # so classification sees the right room shape even
                    # when someone joined and immediately spoke.
                    for event in room_info.timeline.events:
                        if isinstance(event, RoomMemberEvent):
                            self._apply_member_event(room_id, event)
                            continue
                        if self._is_room_creation_event(event):
                            self._apply_room_creation_event(room_id, event)
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

    async def _resume_cursor(
        self,
        mgr: Optional[NarramessengerCredentialManager],
        credential: NarramessengerCredential,
    ) -> Optional[str]:
        """The /sync cursor to resume from: the one PERSISTED by the last sync.

        ``credential`` is a snapshot (possibly days old for a long-lived
        subscriber); the live cursor is written to the DB on every sync, so
        read it back at the start of each connect. Falls back to the
        credential's own field only when the read fails or the row is gone.
        """
        agent_id = credential.agent_id
        token = credential.matrix_since_token
        if mgr is not None:
            try:
                latest = await mgr.get(agent_id)
                if latest is not None:
                    token = latest.matrix_since_token
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[matrix:{agent_id}] could not re-read since_token from "
                    f"the DB ({type(e).__name__}: {e}); falling back to the "
                    f"credential snapshot's cursor (a replay is possible and "
                    f"is absorbed by the DB dedup layer)"
                )
        return token or None

    def _set_session_baseline(self, agent_id: str, *, resumed: bool) -> None:
        """Historic filtering is only right for a CURSOR-LESS sync.

        A first sync after bind / a lost token returns recent room history
        that must not be answered: drop events older than this session's
        connect time (minus the store's buffer). A sync resumed from a
        persisted cursor returns events AFTER the last committed batch —
        unprocessed backlog or an at-least-once overlap — so it is never
        historic-dropped (rule #16); the DB dedup layer absorbs the overlap.
        The baseline is per agent so one agent's reconnect never moves
        another's filter. Every Matrix agent has an override here before its
        first sync, so the channel-wide baseline (the base's default
        ``_on_transport_connected`` still bumps it on each reconnect; Matrix
        does not override that hook) is never consulted for this channel.
        """
        if self._dedup_store is None:
            return
        self._dedup_store.set_agent_baseline(
            agent_id, 0 if resumed else int(time.time() * 1000)
        )

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

        # Room shape from what the sync loop recorded so far (a hint — the
        # roster may be partial): a known creation-state kind, else the
        # roster without the service sender. Unknown is treated as GROUP so
        # we don't accidentally auto-reply to a room we can't verify — the
        # authoritative check happens again in _process_message via
        # _resolve_room_shape, which does the one-time joined_members and
        # creation-state lookups for any room not yet verified.
        chat_type = (
            ChatType.PRIVATE
            if self._hinted_private(room_id, raw.get("_our_user_id") or "")
            else ChatType.GROUP
        )

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
            # ── F28 RTC voice turn detection (two-level, handoff §3.4) ──
            # Hybrid sends final STT as a normal m.text plus the
            # ai.netmind.rtc.voice_input v1 metadata on the same event.
            # Level 1: the strict v1 block parses -> full voice turn with
            # the four binding IDs. Level 2: strict parse failed but the
            # metadata object still carries a non-blank backend-controlled
            # voice_instructions string -> DEGRADED voice turn: voice
            # behavior applies, binding IDs stay empty and never feed
            # correlation, per-call serialization keys, or security
            # decisions. Only when NEITHER level fires does the event stay
            # a plain text message (contract: bad metadata must never
            # break the normal reply path). Metadata is a presentation
            # hint, NOT authorization — classify/authorize gates run
            # unchanged.
            #
            # 1:1 rooms ONLY: the metadata has no source binding (format
            # is validated, provenance is not — and the handoff forbids a
            # sync backend round trip for the fast-mode decision), so a
            # group member could otherwise inject voice_instructions into
            # the reply register. On a DEGRADED turn the metadata was
            # never trustworthy to begin with, so this PRIVATE gate
            # carries the WHOLE injection argument for BOTH levels — do
            # not weaken it on the theory that level 1 "validated" the
            # block. F13 RTC calls are Agent-Human 1:1 by product shape.
            # The gate is NOT the PRIVATE stamp alone: a declared
            # ``room_type: dm`` (sender not validated) makes a room PRIVATE
            # whatever its size, so voice also needs exactly two
            # participants on the known roster, service sender excluded.
            # A cold roster cache (unknown member count) is conservatively
            # refused HERE; _process_message recovers it after the
            # authoritative lookup when the room is dm AND the verified
            # effective count is 2 — see _late_voice_upgrade. The argument
            # chain closes there.
            detected = (
                _detect_voice_turn(
                    body, getattr(raw.get("_nio_event"), "source", None)
                )
                if self._hinted_voice_one_to_one(
                    room_id, raw.get("_our_user_id") or ""
                )
                else None
            )
            if detected is not None:
                transcript, rtc_voice = detected
                if not transcript.strip():
                    # Envelope-only body: nothing to answer. Drop the turn
                    # rather than running the agent on empty input.
                    logger.info(
                        f"[matrix:{raw.get('_agent_id', '?')}] voice turn "
                        f"with empty transcript dropped (event={event_id})"
                    )
                    return None
                body = transcript
                raw = dict(raw)
                raw["rtc_voice"] = rtc_voice
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

        from narranexus.sdk.web import host_settings

        max_bytes = host_settings().max_upload_bytes

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
