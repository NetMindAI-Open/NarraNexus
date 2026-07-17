"""
@file_name: slack_trigger.py
@date: 2026-05-08
@description: Slack channel trigger built on ``ChannelTriggerBase``.

Uses Slack Socket Mode: a persistent WebSocket from the bot to Slack,
no public URL required. Events arrive via a callback listener that we
bridge into the base class's async generator pattern via asyncio.Queue.

Slack-specific concerns this class handles:
  - Bot self-message detection: match ``event.user`` against the bot's
    ``bot_user_id`` from auth.test (NOT against ``event.bot_id`` which
    is the App-level B-prefixed id).
  - Event types (Phase 5 reply policy): ``message`` events only count
    when ``channel_type`` is ``im`` or ``mpim`` (DM / group DM);
    public/private channel messages MUST come through as ``app_mention``.
    Plain ``message.channels`` / ``message.groups`` events are dropped
    at the trigger boundary so the bot stays silent in channels until
    explicitly @-mentioned.
  - Subtype filtering: skip ``message_changed``, ``message_deleted``,
    ``channel_join``, etc. (Phase 3 doesn't react to edits/system events.)
  - Thread continuity: preserve ``thread_ts`` so replies land in-thread.

Known Slack server quirks (no code action — symptoms only):
  - Socket Mode does NOT buffer events while the app's Event
    Subscriptions are disabled or while the socket is disconnected.
    Anything sent during those windows is dropped at Slack's server
    and never replayed on reconnect. ``conversations.history`` will
    still show those messages (it's a Web API, not the event bus), so
    a missing audit row with a present Slack-side message is normal
    and irrecoverable.
  - After a fresh OAuth scope grant — especially ``files:read`` —
    Slack has an undocumented warm-up window where text-only
    ``message.im`` events arrive immediately but file-bearing events
    (``message`` + ``files[]``) silently drop for ~1–5 minutes.
    Symptom: ``ingress_processed`` lands for plain text but zero audit
    appears for the file upload during the window, despite the socket
    being alive and the scope confirmed via ``auth.test``. Resolves
    itself with no code change; the user just resends. Observed during
    Phase 1b smoke testing on 2026-05-21 (Protagolabs workspace,
    Tong Chen_ubc diploma.pdf reproduced the 5-minute delay).

The base class owns: dedup, worker pool, credential watcher, audit log,
inbox writer, reconnect backoff. We override the abstract surface
(connect/parse/echo/sender/builder/load) only.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator, Optional

from loguru import logger

from xyz_agent_context.channel.channel_audit_events import (
    EVENT_ATTACHMENT_FETCH_FAILED,
    EVENT_ATTACHMENT_PERSISTED,
    EVENT_INGRESS_DROPPED_OVERSIZED,
)
from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
    ChannelHistoryConfig,
)
from xyz_agent_context.channel.channel_trigger_base import (
    CHANNEL_SILENT_SENTINEL,
    ChannelTriggerBase,
)
from xyz_agent_context.schema.attachment_schema import Attachment
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)

from ._slack_credential_manager import SlackCredential, SlackCredentialManager
from .slack_context_builder import SlackContextBuilder
from .slack_sdk_client import SlackSDKClient, SlackSDKError

# Slack auth error codes that mean "this token is permanently dead." A
# missed code here is benign (loop just keeps retrying); an over-broad
# match here would disable healthy creds on a transient blip, so keep
# this list narrow.
_SLACK_PERMANENT_AUTH_CODES = frozenset({
    "invalid_auth",
    "token_revoked",
    "token_expired",
    "account_inactive",
    "not_authed",
})

try:
    from slack_sdk.socket_mode.aiohttp import SocketModeClient
    from slack_sdk.socket_mode.response import SocketModeResponse
    _HAS_SLACK_SOCKET = True
except ImportError:  # pragma: no cover
    SocketModeClient = None  # type: ignore[assignment]
    SocketModeResponse = None  # type: ignore[assignment]
    _HAS_SLACK_SOCKET = False


# Subtypes we ignore (edits, deletes, system events, bot replies, etc.)
_IGNORED_SUBTYPES = frozenset({
    "message_changed",
    "message_deleted",
    "bot_message",
    "channel_join",
    "channel_leave",
    "channel_topic",
    "channel_purpose",
    "channel_name",
    "thread_broadcast",  # opt-in; we'd see it twice otherwise
    # NOTE: ``file_share`` is NOT in this set. Modern Slack delivers DM
    # file uploads as a ``message.im`` event WITH ``subtype="file_share"``
    # AND ``files: [...]`` populated. There is no "plain message.im +
    # files[]" path — file_share IS the path. Phase 1b initially included
    # this subtype based on a wrong assumption (legacy duplicate-event
    # behaviour from very old Slack apps). Real CN-dev manual smoke
    # showed text "hi" → ingress_processed fine, but "<text> + PDF" →
    # zero audit rows. Dropping ``file_share`` from the ignore list is
    # what makes attachment ingest actually work end-to-end. Dedup is
    # not at risk: Slack delivers exactly one envelope per upload, and
    # our ``message_id`` (client_msg_id / ts fallback) catches even the
    # edge case where the same file is uploaded twice within the
    # ``ChannelDedupStore`` TTL window.
})

# Channel types from which we accept plain ``message`` events. Anything
# else (public channels, private channels, shared channels) requires an
# ``app_mention`` event to reach us — see Phase 5 reply policy. This
# allow-list is the canonical filter; the manifest also no longer
# subscribes to ``message.channels`` / ``message.groups`` so new bots
# don't even receive those events. The trigger-side filter exists
# defensively for already-bound bots whose manifest can't be
# retroactively changed without manual user action.
_ACCEPTED_MESSAGE_CHANNEL_TYPES = frozenset({"im", "mpim"})


class SlackTrigger(ChannelTriggerBase):
    """Slack channel trigger.

    One Socket Mode WebSocket per credential. Each connection's events
    flow into the shared base-class queue → workers.
    """

    # ── ChannelTriggerBase contract ───────────────────────────────────────
    channel_name = "slack"
    brand_display = "Slack"
    working_source = WorkingSource.SLACK
    react_tool_ref = "react_to_user_message"

    # ── Worker pool ──────────────────────────────────────────────────────
    MIN_WORKERS = 3
    WORKERS_PER_SUBSCRIBER = 2
    MAX_WORKERS = 50
    PROCESS_MESSAGE_TIMEOUT_SECONDS = 1800
    CLEANUP_INTERVAL_SECONDS = 24 * 3600
    HEARTBEAT_INTERVAL_SECONDS = 600
    DEDUP_RETENTION_DAYS = 7
    AUDIT_RETENTION_DAYS = 30

    # Per-event TTL inside the in-memory dedup ring; baseline + DB tier handles older.
    DEDUP_TTL_SECONDS = 600
    HISTORY_BUFFER_MS = 5 * 60 * 1000

    # Slack benefits from debounce — users often send burst follow-up messages.
    DEBOUNCE_WINDOW_MS = 1500

    def __init__(self, max_workers: int = 3):
        super().__init__(
            base_workers=max_workers,
            history_config=ChannelHistoryConfig(
                load_conversation_history=True,
                history_limit=20,
                history_max_chars=3000,
            ),
        )
        # One Socket Mode client per credential, kept so we can disconnect cleanly.
        self._socket_clients: dict[str, Any] = {}
        # users.info cache (per agent → user_id → display name) for 5 min.
        self._sender_cache: dict[tuple[str, str], tuple[str, float]] = {}
        self._sender_cache_ttl = 300.0

    # ────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ────────────────────────────────────────────────────────────────────

    async def start(self, db) -> None:
        if not _HAS_SLACK_SOCKET:
            raise RuntimeError(
                "slack_sdk not installed — cannot run SlackTrigger. "
                "Install with `uv add slack-sdk`."
            )
        await super().start(db)
        logger.info(
            f"SlackTrigger started: {len(self._workers)} workers, "
            f"watching channel_slack_credentials for active rows"
        )

    async def stop(self) -> None:
        # Disconnect any live Socket Mode sessions before the base tears down.
        for key, client in list(self._socket_clients.items()):
            try:
                await asyncio.wait_for(client.disconnect(), timeout=3.0)
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                logger.warning(f"[slack:{key}] disconnect during stop: {e}")
        self._socket_clients.clear()
        await super().stop()

    # ────────────────────────────────────────────────────────────────────
    # Abstract method implementations
    # ────────────────────────────────────────────────────────────────────

    async def load_active_credentials(self) -> list[SlackCredential]:
        if not self._db:
            return []
        mgr = SlackCredentialManager(self._db)
        return await mgr.list_active()

    def _subscriber_key(self, credential: SlackCredential) -> str:  # type: ignore[override]
        return f"{credential.agent_id}:{credential.team_id}"

    def is_permanent_auth_failure(self, exc: BaseException) -> bool:  # type: ignore[override]
        if isinstance(exc, SlackSDKError):
            return (exc.code or "") in _SLACK_PERMANENT_AUTH_CODES
        return False

    async def disable_credential(self, credential: SlackCredential) -> None:  # type: ignore[override]
        if not self._db:
            return
        mgr = SlackCredentialManager(self._db)
        await mgr.set_enabled(credential.agent_id, False)

    async def connect(
        self, credential: SlackCredential
    ) -> AsyncIterator[dict]:
        """Socket Mode → asyncio.Queue → async generator.

        slack_sdk's SocketModeClient is callback-driven. We bridge by
        registering a listener that puts raw events on a queue and
        yielding from that queue. The base class's ``_subscribe_loop``
        consumes us until it raises (then the base handles backoff +
        reconnect).
        """
        assert _HAS_SLACK_SOCKET, "guarded in start()"

        web_client = credential and SlackSDKClient(credential.bot_token).web

        # Honour HTTPS_PROXY / HTTP_PROXY env vars when establishing the
        # Socket Mode wss connection. slack_sdk's SocketModeClient builds
        # its own aiohttp ClientSession internally and does NOT respect
        # the trust_env flag by default — without explicit proxy=, the
        # wss bypass any local Clash / V2Ray / corporate proxy. In
        # restrictive networks (mainland China is the canonical case)
        # the wss-primary.slack.com TCP/TLS handshake often succeeds but
        # subsequent event frames are dropped or reordered by the GFW,
        # producing a "connected but no events" zombie. Symptom in our
        # audit log: socket_mode_connected fires, then repeated
        # "stale. Reconnecting... reason: disconnected for 182+ seconds"
        # without a single ingress_processed event between them.
        proxy_url = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
        )
        socket_client = SocketModeClient(
            app_token=credential.app_token,
            web_client=web_client,
            proxy=proxy_url,
        )
        if proxy_url:
            logger.info(
                f"[slack:{credential.agent_id}] Socket Mode using proxy "
                f"{proxy_url}"
            )

        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def _listener(client, req):
            # Ack first to keep the socket alive even if we fail to enqueue.
            try:
                await client.send_socket_mode_response(
                    SocketModeResponse(envelope_id=req.envelope_id)
                )
            except Exception:  # pragma: no cover — defensive
                logger.exception(f"[slack:{credential.agent_id}] ack failed")

            if req.type != "events_api":
                return
            event = (req.payload or {}).get("event") or {}
            event_type = event.get("type", "")
            # Accept message + app_mention; skip everything else
            if event_type not in ("message", "app_mention"):
                return
            # Skip bot/edit/system subtypes
            if event.get("subtype") in _IGNORED_SUBTYPES:
                return
            # Phase 5 reply policy: plain ``message`` events only count
            # in DM / group-DM contexts. Public/private channel messages
            # MUST come through as ``app_mention`` — otherwise the bot
            # would try to engage with every message in every joined
            # channel. The manifest no longer subscribes to
            # ``message.channels`` / ``message.groups`` for new bots,
            # but already-bound bots still receive them; this filter
            # drops them at the trigger boundary.
            if event_type == "message":
                channel_type = event.get("channel_type", "")
                if channel_type not in _ACCEPTED_MESSAGE_CHANNEL_TYPES:
                    return
            # Skip our own messages early (cheap pre-filter; is_echo is the
            # canonical check)
            if event.get("user") and event["user"] == credential.bot_user_id:
                return
            await queue.put(event)

        socket_client.socket_mode_request_listeners.append(_listener)
        await socket_client.connect()

        key = self._subscriber_key(credential)
        self._socket_clients[key] = socket_client
        logger.info(
            f"[slack:{credential.agent_id}] socket mode connected, "
            f"team={credential.team_name}"
        )

        try:
            while self.running:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Periodic wake-up — lets the base loop check self.running
                    continue
                yield event
        finally:
            try:
                await asyncio.wait_for(socket_client.disconnect(), timeout=3.0)
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                logger.warning(
                    f"[slack:{credential.agent_id}] disconnect on subscribe-loop exit: {e}"
                )
            self._socket_clients.pop(key, None)

    def parse_event(self, raw: dict) -> Optional[ParsedMessage]:
        """Slack event → ParsedMessage. None means "skip".

        Phase 1b: extracts ``files: [...]`` from message events into
        ``raw["attachment_refs"]``. ``subtype="file_share"`` IS the
        canonical Slack delivery envelope for DM file uploads (one
        ``message.im`` event with the ``files`` array populated AND
        ``subtype="file_share"``). It is explicitly NOT in
        ``_IGNORED_SUBTYPES`` for that reason — see the comment block
        above the constant. The initial Phase 1b assumption that
        ``file_share`` was a legacy / duplicate envelope was disproven
        by real-world manual smoke; ``d017720`` removed it from the
        ignore list.
        """
        event_type = raw.get("type", "")
        if event_type not in ("message", "app_mention"):
            return None
        if raw.get("subtype") in _IGNORED_SUBTYPES:
            return None
        # Phase 5 reply policy (defense-in-depth — same filter ``_listener``
        # already runs, so production events shouldn't reach this branch.
        # Kept here for callers that bypass ``_listener`` — tests, future
        # webhook ingress code, etc.).
        if event_type == "message":
            channel_type = raw.get("channel_type", "")
            if channel_type not in _ACCEPTED_MESSAGE_CHANNEL_TYPES:
                return None
        # Tombstone-y messages without user
        sender_id = raw.get("user", "")
        if not sender_id:
            return None

        ts = raw.get("ts", "")
        # client_msg_id is a stable UUID for user-submitted messages; ts is the
        # canonical "message id" within the channel for everything else.
        message_id = raw.get("client_msg_id") or ts or ""
        if not message_id:
            return None

        try:
            timestamp_ms = int(float(ts) * 1000) if ts else 0
        except (TypeError, ValueError):
            timestamp_ms = 0

        chat_id = raw.get("channel", "")
        thread_ts = raw.get("thread_ts")  # None when not a threaded reply
        text = raw.get("text", "") or ""

        # Phase 1b — extract files[] into normalized refs. Slack messages
        # can carry multiple files in one upload (drag-drop multi-select).
        refs: list[dict[str, Any]] = []
        for f in raw.get("files") or []:
            if not isinstance(f, dict):
                continue
            file_id = f.get("id") or ""
            if not file_id:
                continue
            refs.append({
                "kind": "file",
                "platform_ref": file_id,
                "original_name": f.get("name") or f.get("title") or file_id,
                "mime_hint": f.get("mimetype", "") or "",
                "size_hint": int(f.get("size", 0) or 0),
                # url_private may or may not be present at this stage;
                # fetch_attachments falls back to files.info when missing.
                "url_private": f.get("url_private", "") or "",
            })


        # Derive content_type from the leading ref. If MIXED kinds are
        # attached, FILE is the catch-all so the agent treats it as a
        # generic upload (it still gets multiple Attachment objects in
        # extra_data — they're all readable).
        content_type = MessageContentType.TEXT
        if refs:
            primary_mime = refs[0]["mime_hint"]
            if primary_mime.startswith("image/"):
                content_type = MessageContentType.IMAGE
            elif primary_mime.startswith("audio/"):
                content_type = MessageContentType.AUDIO
            elif primary_mime.startswith("video/"):
                content_type = MessageContentType.VIDEO
            else:
                content_type = MessageContentType.FILE

        # Drop empty events: no text AND no refs means nothing actionable.
        if not text and not refs:
            return None

        # Mentions: <@U12345> tokens in text
        mentions: list[str] = []
        if "<@" in text:
            import re
            mentions = re.findall(r"<@([A-Z0-9]+)>", text)

        # Stash refs on a fresh raw dict so fetch_attachments can read
        # them later without polluting the canonical schema.
        if refs:
            raw = dict(raw)
            raw["attachment_refs"] = refs

        return ParsedMessage(
            message_id=message_id,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name="",  # filled in by resolve_sender_name later
            content=text,
            content_type=content_type,
            # Slack channels behave like groups; even DMs (channel starting with D)
            # are 2-person channels.
            chat_type=ChatType.GROUP if chat_id.startswith(("C", "G")) else ChatType.PRIVATE,
            timestamp_ms=timestamp_ms,
            thread_id=thread_ts if thread_ts else None,
            mentions=mentions,
            raw=raw,
        )

    async def fetch_attachments(  # type: ignore[override]
        self, message: ParsedMessage, credential: SlackCredential
    ) -> list[Attachment]:
        """Download every Slack file ref via Bearer-auth GET and persist.

        Never-raises. Per-ref failures (HTTP, files.info miss, oversize)
        are audited and skipped; remaining refs still flow.
        """
        refs = (message.raw or {}).get("attachment_refs") or []
        if not refs:
            return []

        # Per-credential Socket Mode client also has the SDK client we
        # need (via the same token). We construct a short-lived
        # SlackSDKClient per fetch — Slack's download uses a fresh
        # aiohttp session per call anyway, so there's no resource saving
        # from caching.
        client = SlackSDKClient(credential.bot_token)

        from backend.config import settings as backend_settings
        max_bytes = backend_settings.max_upload_bytes

        out: list[Attachment] = []
        for ref in refs:
            platform_ref = ref.get("platform_ref") or ""
            if not platform_ref:
                continue
            size_hint = int(ref.get("size_hint", 0) or 0)
            original_name = ref.get("original_name") or platform_ref
            mime_hint = ref.get("mime_hint", "") or ""
            url = ref.get("url_private") or ""

            # Pre-check backend cap.
            if size_hint and size_hint > max_bytes:
                logger.info(
                    f"[slack:{credential.agent_id}] refusing oversized "
                    f"attachment {original_name!r}: size_hint={size_hint} "
                    f"> max_upload_bytes={max_bytes}"
                )
                await self._audit(
                    EVENT_INGRESS_DROPPED_OVERSIZED,
                    message_id=message.message_id,
                    agent_id=credential.agent_id,
                    app_id=getattr(credential, "app_id", ""),
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    details={
                        "platform_ref": platform_ref,
                        "size_hint": size_hint,
                        "max_upload_bytes": max_bytes,
                        "reason": "backend_max_upload_bytes",
                    },
                )
                continue

            # If event didn't carry url_private, hydrate via files.info.
            # Slack sometimes ships file events with only the id during
            # high-traffic windows; the canonical metadata comes back
            # complete here.
            if not url:
                try:
                    info = await client.files_info(platform_ref)
                except SlackSDKError as e:
                    logger.warning(
                        f"[slack:{credential.agent_id}] files.info failed "
                        f"for {platform_ref}: {e.code}: {e}"
                    )
                    await self._audit(
                        EVENT_ATTACHMENT_FETCH_FAILED,
                        message_id=message.message_id,
                        agent_id=credential.agent_id,
                        app_id=getattr(credential, "app_id", ""),
                        chat_id=message.chat_id,
                        sender_id=message.sender_id,
                        details={
                            "platform_ref": platform_ref,
                            "stage": "files_info",
                            "error": f"{e.code}:{e}",
                        },
                    )
                    continue
                url = info.get("url_private") or ""
                # Patch any newer mime/size info if the event lacked it.
                if not mime_hint:
                    mime_hint = info.get("mimetype", "") or ""
                if not size_hint:
                    size_hint = int(info.get("size", 0) or 0)
                if not url:
                    logger.warning(
                        f"[slack:{credential.agent_id}] no url_private for {platform_ref}"
                    )
                    await self._audit(
                        EVENT_ATTACHMENT_FETCH_FAILED,
                        message_id=message.message_id,
                        agent_id=credential.agent_id,
                        app_id=getattr(credential, "app_id", ""),
                        chat_id=message.chat_id,
                        sender_id=message.sender_id,
                        details={
                            "platform_ref": platform_ref,
                            "stage": "no_url_private",
                        },
                    )
                    continue

            # Stream-download with per-attachment cap.
            try:
                raw_bytes = await client.download_url(url, max_bytes=max_bytes)
            except SlackSDKError as e:
                # ``oversized`` from the streaming cap is a distinct
                # audit event so ops can tell platform-cap from network.
                if e.code == "oversized":
                    await self._audit(
                        EVENT_INGRESS_DROPPED_OVERSIZED,
                        message_id=message.message_id,
                        agent_id=credential.agent_id,
                        app_id=getattr(credential, "app_id", ""),
                        chat_id=message.chat_id,
                        sender_id=message.sender_id,
                        details={
                            "platform_ref": platform_ref,
                            "reason": "stream_cap_exceeded",
                            "max_upload_bytes": max_bytes,
                        },
                    )
                else:
                    logger.warning(
                        f"[slack:{credential.agent_id}] download_url failed "
                        f"for {platform_ref}: {e.code}: {e}"
                    )
                    await self._audit(
                        EVENT_ATTACHMENT_FETCH_FAILED,
                        message_id=message.message_id,
                        agent_id=credential.agent_id,
                        app_id=getattr(credential, "app_id", ""),
                        chat_id=message.chat_id,
                        sender_id=message.sender_id,
                        details={
                            "platform_ref": platform_ref,
                            "stage": "download",
                            "error": f"{e.code}:{e}",
                        },
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
                logger.warning(
                    f"[slack:{credential.agent_id}] persist failed for "
                    f"{original_name!r}: {type(e).__name__}: {e}"
                )
                await self._audit(
                    EVENT_ATTACHMENT_FETCH_FAILED,
                    message_id=message.message_id,
                    agent_id=credential.agent_id,
                    app_id=getattr(credential, "app_id", ""),
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    details={
                        "platform_ref": platform_ref,
                        "stage": "persist",
                        "error": f"{type(e).__name__}:{e}",
                    },
                )
                continue

            out.append(att)
            await self._audit(
                EVENT_ATTACHMENT_PERSISTED,
                message_id=message.message_id,
                agent_id=credential.agent_id,
                app_id=getattr(credential, "app_id", ""),
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details={
                    "file_id": att.file_id,
                    "mime_type": att.mime_type,
                    "size_bytes": att.size_bytes,
                    "category": att.category.value,
                    "has_transcript": bool(att.transcript),
                },
            )
        return out

    async def is_echo(
        self, message: ParsedMessage, credential: SlackCredential
    ) -> bool:
        """True when the message was sent by our own bot user."""
        if not credential.bot_user_id:
            return False
        return message.sender_id == credential.bot_user_id

    async def resolve_sender_name(
        self, sender_id: str, credential: SlackCredential
    ) -> str:
        """users.info with a small per-agent cache."""
        import time
        cache_key = (credential.agent_id, sender_id)
        cached = self._sender_cache.get(cache_key)
        if cached:
            name, expiry = cached
            if expiry > time.monotonic():
                return name
            self._sender_cache.pop(cache_key, None)

        client = SlackSDKClient(credential.bot_token)
        user = await client.get_user_info(sender_id)
        name = (
            user.get("real_name")
            or user.get("profile", {}).get("display_name")
            or user.get("name")
            or sender_id
        )
        self._sender_cache[cache_key] = (name, time.monotonic() + self._sender_cache_ttl)
        return name

    def create_context_builder(
        self,
        message: ParsedMessage,
        credential: SlackCredential,
        agent_id: str,
    ) -> ChannelContextBuilderBase:
        return SlackContextBuilder(
            message=message,
            credential=credential,
            agent_id=agent_id,
        )

    # ────────────────────────────────────────────────────────────────────
    # Reply path — base's _build_and_run_agent uses extract_output to
    # decide what to send. SlackTrigger's reply lives here.
    # ────────────────────────────────────────────────────────────────────

    def extract_output(
        self, result, message: ParsedMessage, credential: SlackCredential
    ) -> str:
        """Pull reply text from the AgentRuntime result for inbox display.

        Slack agents reply by calling ``slack_cli(method="chat.postMessage",
        args={"channel": ..., "text": ...})`` — they do NOT produce reply
        text directly. ``output_text`` therefore contains agent reasoning
        ("My thought process: ..."), NOT the user-visible reply.

        So we scrape sent text from tool-call items, mirroring Lark's
        approach. If the agent never called slack_cli/chat.postMessage,
        treat it as "stayed silent" (Communication Protocol decided not
        to reply).
        """
        slack_replies: list[str] = []
        for raw in getattr(result, "raw_items", []):
            if not isinstance(raw, dict):
                continue
            item = raw.get("item", {})
            if item.get("type") != "tool_call_item":
                continue
            sent_text = self._extract_slack_reply(item)
            if sent_text:
                slack_replies.append(sent_text)

        if slack_replies:
            output_text = "\n".join(slack_replies)
        else:
            # Agent produced reasoning but never called slack_cli to send.
            # Mark explicitly in the inbox — same convention across channels.
            output_text = CHANNEL_SILENT_SENTINEL

        logger.info(
            f"SlackTrigger [{credential.team_name or credential.agent_id}] "
            f"agent responded: {output_text[:200]}"
        )
        return output_text

    async def send_channel_reply(
        self, credential: SlackCredential, message: ParsedMessage, text: str
    ) -> None:
        """Error-fallback send: post into the originating Slack channel
        (threaded when the inbound message was in a thread)."""
        thread_ts = (message.raw or {}).get("thread_ts") or None
        await SlackSDKClient(credential.bot_token).send_message(
            message.chat_id, text, thread_ts=thread_ts
        )

    @staticmethod
    def _extract_slack_reply(item: dict) -> str:
        """Extract sent text from a ``slack_cli`` tool call item.

        ``slack_cli`` signature: ``(agent_id, method, args)`` where
        ``args`` is a dict. For replies we only care about
        ``method == "chat.postMessage"`` and ``args["text"]``.

        Other Slack methods (chat.update, reactions.add, etc.) are NOT
        user-visible message content — we skip them. ``chat.update``
        edits an existing message; if it ever needs to be surfaced in
        the inbox the right shape is "(edited message)" not the new text.
        """
        tool_name = item.get("tool_name", "")
        if "slack_cli" not in tool_name:
            return ""

        raw_args = item.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except (ValueError, TypeError):
                return ""
        if not isinstance(raw_args, dict):
            return ""

        if raw_args.get("method") != "chat.postMessage":
            return ""

        inner = raw_args.get("args") or {}
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except (ValueError, TypeError):
                return ""
        if not isinstance(inner, dict):
            return ""

        text = inner.get("text", "")
        return text.strip() if isinstance(text, str) else ""

    def format_error_reply(self, error) -> str:
        """User-friendly error string for the inbox + Slack reply."""
        return (
            f"Sorry, I hit an error processing your message: "
            f"{getattr(error, 'message', '') or 'unknown'}"
        )
