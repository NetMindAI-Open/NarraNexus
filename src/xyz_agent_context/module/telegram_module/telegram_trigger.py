"""
@file_name: telegram_trigger.py
@date: 2026-05-09
@description: Telegram channel trigger built on ``ChannelTriggerBase``.

Uses long-poll (``getUpdates`` with timeout=30) — no webhook, no public IP.
Each active credential gets one long-poll loop. Events flow through the
shared dedup → debounce → worker pipeline owned by the base class.

Telegram-specific concerns this class handles:
  - **Privacy mode default ON (we KEEP it that way)**: bot in groups
    only sees @-mentions and ``/commands``. This is the **right**
    default — it gives us the @-mention-only group behaviour Slack is
    still trying to retrofit (Phase 5). ``_NO_BOT_INSTRUCTION``
    explicitly tells users NOT to disable it. Trigger does no extra
    filtering for groups beyond what Telegram already does at source.
  - **getUpdates / setWebhook exclusivity**: 409 Conflict if a webhook
    is set. ``deleteWebhook`` runs once at bind time; if a 409 still
    happens at runtime, we call it again and retry.
  - **chat_id is signed int64**: positive=user DM, negative=group,
    very-large-negative=supergroup/channel. Stored as string everywhere.
  - **forum topic threads**: supergroup forums have ``message_thread_id``;
    we map to ``ParsedMessage.thread_id`` so reply-in-thread works.
  - **No conversation history API**: ``get_conversation_history`` in
    the context builder returns ``[]`` — agent relies on ChatModule.

The base class owns: dedup, worker pool, credential watcher, audit log,
inbox writer, reconnect backoff. This class overrides the abstract
surface (connect/parse/echo/sender/builder/load) only, plus
``extract_output`` to scrape ``tg_cli`` tool-call args (NOT
``output_text`` — Phase 3 Slack regression).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
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

from ._telegram_credential_manager import TelegramCredential, TelegramCredentialManager
from .telegram_context_builder import TelegramContextBuilder
from .telegram_sdk_client import TelegramSDKClient, TelegramSDKError


class TelegramTrigger(ChannelTriggerBase):
    """Telegram channel trigger.

    One long-poll generator per credential; the base feeds events into
    the shared queue → workers.
    """

    # ── ChannelTriggerBase contract ───────────────────────────────────────
    channel_name = "telegram"
    brand_display = "Telegram"
    working_source = WorkingSource.TELEGRAM
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

    DEDUP_TTL_SECONDS = 600
    HISTORY_BUFFER_MS = 5 * 60 * 1000

    # Telegram users burst-message often; same as Slack.
    DEBOUNCE_WINDOW_MS = 1500

    # Long-poll tuning
    POLL_TIMEOUT_SECONDS = 30
    POLL_IDLE_SLEEP_SECONDS = 0.5

    # "typing..." indicator pump cadence. Telegram's sendChatAction
    # decays after 5s server-side, so we re-fire every 4s for a margin.
    TYPING_REFRESH_SECONDS = 4.0

    def __init__(self, max_workers: int = 3):
        super().__init__(
            base_workers=max_workers,
            # Telegram has no platform-side conversation history API, but
            # ChannelInboxWriter persists every turn to bus_messages
            # under channel_id=f"telegram_{chat_id}". The context builder
            # reads from there — same load_conversation_history=True
            # contract as Slack/Lark, just with a local data source.
            # Without this, agents see zero prior context and treat
            # follow-up messages like "再试一下" as fresh requests.
            history_config=ChannelHistoryConfig(
                load_conversation_history=True,
                history_limit=20,
                history_max_chars=20000,
            ),
        )
        # Per-credential long-poll offset (last consumed update_id + 1)
        self._poll_offsets: dict[str, int] = {}
        # Per-credential SDK client kept so stop() can close cleanly
        self._sdk_clients: dict[str, TelegramSDKClient] = {}

    # ────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ────────────────────────────────────────────────────────────────────

    async def start(self, db) -> None:
        await super().start(db)
        logger.info(
            f"TelegramTrigger started: {len(self._workers)} workers, "
            f"watching channel_telegram_credentials for active rows"
        )

    async def stop(self) -> None:
        for key, client in list(self._sdk_clients.items()):
            try:
                await asyncio.wait_for(client.close(), timeout=3.0)
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                logger.warning(f"[telegram:{key}] client close during stop: {e}")
        self._sdk_clients.clear()
        await super().stop()

    # ────────────────────────────────────────────────────────────────────
    # Abstract method implementations
    # ────────────────────────────────────────────────────────────────────

    async def load_active_credentials(self) -> list[TelegramCredential]:
        if not self._db:
            return []
        mgr = TelegramCredentialManager(self._db)
        return await mgr.list_active()

    def _subscriber_key(self, credential: TelegramCredential) -> str:  # type: ignore[override]
        return credential.agent_id

    def is_permanent_auth_failure(self, exc: BaseException) -> bool:  # type: ignore[override]
        # Telegram surfaces ``description="Unauthorized"`` (HTTP 401) when
        # the token is revoked, never minted, or the bot account was
        # deleted by the owner via @BotFather. Treat that as terminal —
        # the watcher would otherwise reconnect every 120s forever.
        if isinstance(exc, TelegramSDKError):
            code = exc.code or ""
            return code == "Unauthorized" or code.startswith("Unauthorized")
        return False

    async def disable_credential(self, credential: TelegramCredential) -> None:  # type: ignore[override]
        if not self._db:
            return
        mgr = TelegramCredentialManager(self._db)
        await mgr.set_enabled(credential.agent_id, False)

    @asynccontextmanager
    async def processing_indicator(  # type: ignore[override]
        self, credential: TelegramCredential, message: ParsedMessage
    ) -> AsyncIterator[None]:
        """Drive a "typing..." indicator on Telegram while the agent thinks.

        ``sendChatAction(action="typing")`` clears after ~5s, so a
        background task re-fires every ``TYPING_REFRESH_SECONDS`` until
        the agent run finishes. The pump is best-effort — Telegram
        failures (network, rate-limit, chat-gone) are swallowed so the
        agent run itself never aborts because the cosmetic indicator
        broke.

        Uses the cached long-poll SDK client when available
        (``self._sdk_clients[key]``) to share the aiohttp session; falls
        back to a short-lived client otherwise so the indicator still
        fires in tests / corner cases where the watcher hasn't populated
        the cache.
        """
        key = self._subscriber_key(credential)
        client = self._sdk_clients.get(key)
        own_client = client is None
        if own_client:
            client = TelegramSDKClient(credential.bot_token)
        chat_id = message.chat_id
        stop_event = asyncio.Event()

        async def _pump() -> None:
            # First action fires immediately so the user sees "typing..."
            # within the latency budget rather than 4s after acceptance.
            while not stop_event.is_set():
                try:
                    await client.send_chat_action(chat_id, action="typing")
                except Exception as e:  # noqa: BLE001 — cosmetic, never abort
                    logger.debug(
                        f"[telegram:{credential.agent_id}] typing indicator "
                        f"sendChatAction failed (non-fatal): "
                        f"{type(e).__name__}: {e}"
                    )
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=self.TYPING_REFRESH_SECONDS
                    )
                    return  # stop_event signalled — exit cleanly
                except asyncio.TimeoutError:
                    continue  # refresh interval elapsed — fire again

        pump_task = asyncio.create_task(_pump())
        try:
            yield
        finally:
            stop_event.set()
            try:
                await asyncio.wait_for(pump_task, timeout=1.0)
            except asyncio.TimeoutError:
                pump_task.cancel()
            except Exception:  # noqa: BLE001
                pass
            if own_client:
                try:
                    await asyncio.wait_for(client.close(), timeout=1.0)
                except Exception:  # noqa: BLE001
                    pass

    async def connect(
        self, credential: TelegramCredential
    ) -> AsyncIterator[dict]:
        """Long-poll loop. Yields raw Telegram update dicts.

        On 409 Conflict (webhook still set), call ``deleteWebhook`` once
        and retry. Other ``TelegramSDKError`` raises so the base class
        backs off and reconnects.
        """
        client = TelegramSDKClient(credential.bot_token)
        key = self._subscriber_key(credential)
        self._sdk_clients[key] = client
        offset = self._poll_offsets.get(key, 0)

        logger.info(
            f"[telegram:{credential.agent_id}] long-poll started, "
            f"bot=@{credential.bot_username}"
        )

        try:
            while self.running:
                try:
                    updates = await client.get_updates(
                        offset=offset,
                        timeout=self.POLL_TIMEOUT_SECONDS,
                        allowed_updates=["message"],
                    )
                except TelegramSDKError as e:
                    if "Conflict" in e.code or "terminated by setWebhook" in e.code:
                        logger.warning(
                            f"[telegram:{credential.agent_id}] getUpdates 409 — "
                            f"webhook still set; calling deleteWebhook"
                        )
                        try:
                            await client.delete_webhook()
                        except TelegramSDKError:
                            pass
                        await asyncio.sleep(1.0)
                        continue
                    raise

                for update in updates:
                    if not self.running:
                        break
                    update_id = update.get("update_id")
                    yield update
                    # Advance offset ONLY after a clean yield. If the consumer
                    # raises (e.g. crash, shutdown mid-batch, parse_event
                    # error), the offset stays put and Telegram replays this
                    # update on the next getUpdates. The dedup store catches
                    # double-delivery on the happy path.
                    if update_id is not None:
                        offset = int(update_id) + 1
                        self._poll_offsets[key] = offset

                if not updates:
                    # No-op idle wake-up so self.running is checked
                    # periodically even when the bot is quiet.
                    await asyncio.sleep(self.POLL_IDLE_SLEEP_SECONDS)
        finally:
            try:
                await asyncio.wait_for(client.close(), timeout=3.0)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
            self._sdk_clients.pop(key, None)

    def parse_event(self, raw: dict) -> Optional[ParsedMessage]:
        """Telegram update → ParsedMessage. None means "skip".

        Phase 1a: media support added — ``document``, ``photo``, ``voice``,
        ``audio``, ``video`` payloads are converted to platform-neutral
        refs in ``raw["attachment_refs"]`` and ``fetch_attachments``
        downloads them. ``content`` carries the message text or
        ``caption`` (when only media + caption arrived). Returns ``None``
        only when neither text nor a supported attachment exists
        (stickers, locations, contact cards — still out of scope).
        """
        msg = raw.get("message")
        if not msg:
            # We only allowed "message" updates; ignore everything else.
            return None

        # Telegram puts plain text in ``text``; media-only messages put
        # the framing line (if any) in ``caption``. Treat them
        # interchangeably for ``ParsedMessage.content``.
        text = msg.get("text", "") or msg.get("caption", "") or ""

        # Build attachment refs in priority order. Telegram messages
        # carry exactly one media kind, so the ``elif`` cascade is fine.
        refs: list[dict[str, Any]] = []
        content_type = MessageContentType.TEXT
        if "document" in msg:
            doc = msg["document"]
            refs.append({
                "kind": "document",
                "platform_ref": doc.get("file_id", ""),
                "original_name": doc.get("file_name") or doc.get("file_id", "file"),
                "mime_hint": doc.get("mime_type", "") or "",
                "size_hint": int(doc.get("file_size", 0) or 0),
            })
            content_type = MessageContentType.FILE
        elif isinstance(msg.get("photo"), list) and msg["photo"]:
            # photo is a list of PhotoSize — pick the LAST entry (largest).
            # Each PhotoSize has file_id / file_unique_id / file_size.
            largest = msg["photo"][-1] or {}
            unique = largest.get("file_unique_id") or largest.get("file_id", "photo")
            refs.append({
                "kind": "photo",
                "platform_ref": largest.get("file_id", ""),
                "original_name": f"{unique}.jpg",
                "mime_hint": "image/jpeg",
                "size_hint": int(largest.get("file_size", 0) or 0),
            })
            content_type = MessageContentType.IMAGE
        elif "voice" in msg:
            v = msg["voice"]
            unique = v.get("file_unique_id") or v.get("file_id", "voice")
            refs.append({
                "kind": "voice",
                "platform_ref": v.get("file_id", ""),
                "original_name": f"{unique}.ogg",
                "mime_hint": v.get("mime_type", "") or "audio/ogg",
                "size_hint": int(v.get("file_size", 0) or 0),
            })
            content_type = MessageContentType.AUDIO
        elif "audio" in msg:
            a = msg["audio"]
            unique = a.get("file_unique_id") or a.get("file_id", "audio")
            # Telegram audio has optional file_name (only present for
            # uploaded MP3s with metadata) — fall back to unique id + ext.
            ext = ".mp3"
            refs.append({
                "kind": "audio",
                "platform_ref": a.get("file_id", ""),
                "original_name": a.get("file_name") or f"{unique}{ext}",
                "mime_hint": a.get("mime_type", "") or "audio/mpeg",
                "size_hint": int(a.get("file_size", 0) or 0),
            })
            content_type = MessageContentType.AUDIO
        elif "video" in msg:
            v = msg["video"]
            unique = v.get("file_unique_id") or v.get("file_id", "video")
            refs.append({
                "kind": "video",
                "platform_ref": v.get("file_id", ""),
                "original_name": v.get("file_name") or f"{unique}.mp4",
                "mime_hint": v.get("mime_type", "") or "video/mp4",
                "size_hint": int(v.get("file_size", 0) or 0),
            })
            content_type = MessageContentType.VIDEO

        # Drop only when there's nothing actionable at all.
        # Sticker / location / contact / poll / dice fall through to here.
        if not text and not refs:
            return None

        from_user = msg.get("from", {})
        sender_id = str(from_user.get("id", "") or "")
        if not sender_id:
            return None

        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", "") or "")
        if not chat_id:
            return None

        chat_type_raw = chat.get("type", "private")
        chat_type = (
            ChatType.GROUP if chat_type_raw in ("group", "supergroup", "channel")
            else ChatType.PRIVATE
        )

        first = from_user.get("first_name", "") or ""
        last = from_user.get("last_name", "") or ""
        sender_name = " ".join(p for p in (first, last) if p) or sender_id

        # Mentions: parse "entities" of type "mention" / "text_mention".
        # For media messages with a caption, entities live under
        # ``caption_entities`` instead — merge both sources.
        mentions: list[str] = []
        for ent in (msg.get("entities") or []) + (msg.get("caption_entities") or []):
            if not isinstance(ent, dict):
                continue
            etype = ent.get("type", "")
            offset = ent.get("offset", 0)
            length = ent.get("length", 0)
            if etype == "mention":
                # Plain @username — extract from text
                token = text[offset : offset + length].lstrip("@")
                if token:
                    mentions.append(token)
            elif etype == "text_mention":
                # Inline mention with user object
                user = ent.get("user", {}) or {}
                uid = user.get("id")
                if uid is not None:
                    mentions.append(str(uid))

        thread_id = msg.get("message_thread_id")
        thread_id_str: Optional[str] = str(thread_id) if thread_id is not None else None

        reply_msg = msg.get("reply_to_message") or {}
        reply_to_id = reply_msg.get("message_id")
        reply_to_str: Optional[str] = str(reply_to_id) if reply_to_id is not None else None

        # Stash refs in ``raw`` so fetch_attachments can read them later
        # without polluting the canonical ParsedMessage schema. We make
        # a shallow copy first (``raw = dict(raw)``) so the caller's dict
        # is never mutated — matches the immutability contract used by
        # SlackTrigger and LarkTrigger's parse_event implementations.
        if refs:
            raw = dict(raw)
            raw["attachment_refs"] = refs

        return ParsedMessage(
            message_id=str(msg["message_id"]),
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content=text,
            content_type=content_type,
            chat_type=chat_type,
            timestamp_ms=int(msg.get("date", 0)) * 1000,
            reply_to_message_id=reply_to_str,
            thread_id=thread_id_str,
            mentions=mentions,
            raw=raw,
        )

    async def fetch_attachments(  # type: ignore[override]
        self, message: ParsedMessage, credential: TelegramCredential
    ) -> list[Attachment]:
        """Download every attachment ref via Telegram's two-step bot API.

        Never-raises. Per-ref failures (network, oversized, missing
        file_path) are audited and skipped; remaining successful
        downloads still flow to the agent.
        """
        refs = (message.raw or {}).get("attachment_refs") or []
        if not refs:
            return []

        # Pull the cached long-poll SDK client. Watcher populates this
        # in connect(); if it's missing (very early in start(), tests
        # that exercise fetch_attachments directly), fall back to a
        # short-lived client so the hook stays usable in isolation.
        key = self._subscriber_key(credential)
        client = self._sdk_clients.get(key)
        own_client = client is None
        if own_client:
            client = TelegramSDKClient(credential.bot_token)

        from backend.config import settings as backend_settings
        max_bytes = backend_settings.max_upload_bytes

        out: list[Attachment] = []
        try:
            for ref in refs:
                platform_ref = ref.get("platform_ref") or ""
                if not platform_ref:
                    continue
                size_hint = int(ref.get("size_hint", 0) or 0)
                original_name = ref.get("original_name") or platform_ref
                mime_hint = ref.get("mime_hint", "") or ""

                # Backend cap pre-check. Telegram's 20 MB platform cap is
                # enforced by the SDK; this catches the (rarer) case where
                # backend cap is tighter than 20 MB.
                if size_hint and size_hint > max_bytes:
                    logger.info(
                        f"[telegram:{credential.agent_id}] refusing oversized "
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

                try:
                    raw_bytes, _file_path = await client.download_file(
                        platform_ref, size_hint=size_hint or None
                    )
                except TelegramSDKError as e:
                    # ``oversized`` from the SDK is a separate audit event so
                    # ops can distinguish "Telegram 20MB platform cap" from
                    # "general fetch failure".
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
                                "size_hint": size_hint,
                                "reason": "telegram_bot_20mb_cap",
                            },
                        )
                    else:
                        logger.warning(
                            f"[telegram:{credential.agent_id}] download_file "
                            f"failed for {platform_ref!r}: {e.code}: {e}"
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
                                "error": f"{e.code}:{e}",
                            },
                        )
                    continue

                # Post-download size sanity check (in case size_hint was 0
                # or lying).
                if len(raw_bytes) > max_bytes:
                    await self._audit(
                        EVENT_INGRESS_DROPPED_OVERSIZED,
                        message_id=message.message_id,
                        agent_id=credential.agent_id,
                        app_id=getattr(credential, "app_id", ""),
                        chat_id=message.chat_id,
                        sender_id=message.sender_id,
                        details={
                            "platform_ref": platform_ref,
                            "actual_size": len(raw_bytes),
                            "max_upload_bytes": max_bytes,
                            "reason": "post_download_oversize",
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
                        f"[telegram:{credential.agent_id}] persist failed for "
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
                            "error": f"persist:{type(e).__name__}:{e}",
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
        finally:
            if own_client:
                try:
                    await client.close()
                except Exception:  # noqa: BLE001
                    pass

        return out

    async def is_echo(
        self, message: ParsedMessage, credential: TelegramCredential
    ) -> bool:
        """True when the message was sent by our own bot user."""
        if not credential.bot_user_id:
            return False
        return message.sender_id == credential.bot_user_id

    async def resolve_sender_name(
        self, sender_id: str, credential: TelegramCredential
    ) -> str:
        """Telegram messages already carry first/last name in the payload —
        ``parse_event`` extracts it into ``sender_name``. This resolver is
        the fallback used by the base class' inbox writer when the parsed
        name is empty (rare). We just return ``sender_id`` rather than
        burning an API call (Telegram has no general user-info-by-id API
        without a chat context)."""
        return sender_id

    def create_context_builder(
        self,
        message: ParsedMessage,
        credential: TelegramCredential,
        agent_id: str,
    ) -> ChannelContextBuilderBase:
        return TelegramContextBuilder(
            message=message,
            credential=credential,
            agent_id=agent_id,
            # Pass the trigger's DB handle so the builder can read history
            # from bus_messages. ``self._db`` is set by the base class in
            # ``start()`` before the first message is processed.
            db_client=self._db,
        )

    # ────────────────────────────────────────────────────────────────────
    # Inbound preprocessing — late owner resolution
    # ────────────────────────────────────────────────────────────────────

    async def _process_message(
        self, credential: TelegramCredential, message: ParsedMessage
    ) -> None:
        """Override to do late owner resolution before invoking AgentRuntime.

        WHY this lives here (not in bind):
            Telegram's ``getChat`` API does NOT accept @username for
            regular user accounts (only supergroups/channels/bots). So
            at bind time we cannot translate the user-supplied
            ``owner_username`` to a numeric ``user_id``. The first
            inbound DM is when the mapping becomes available — the
            event payload carries both numeric ``from.id`` and current
            ``from.username``.

        SECURITY MODEL:
            ``owner_username`` is the LOCK set at bind time. We only
            populate ``owner_user_id`` when the inbound sender's
            ``from.username`` matches the stored ``owner_username``
            (case-insensitive). This is NOT "first DM wins" — a
            stranger DM'ing the bot first won't claim ownership
            because their ``from.username`` won't match. Telegram
            @username ownership is globally unique, so matching the
            handle on first contact functionally proves "you control
            this handle on Telegram."

        Idempotent: only fires when ``owner_username`` is set AND
        ``owner_user_id`` is still empty. Once resolved it stays put
        across restarts (persisted in DB).
        """
        if (
            credential.owner_username
            and not credential.owner_user_id
            and isinstance(message.raw, dict)
        ):
            await self._maybe_resolve_owner(credential, message)
        return await super()._process_message(credential, message)

    async def _maybe_resolve_owner(
        self, credential: TelegramCredential, message: ParsedMessage
    ) -> None:
        """Match inbound message's ``from.username`` against the stored
        ``owner_username``. On match, write ``owner_user_id`` +
        ``owner_name`` to DB and update the in-memory credential so
        the agent run THIS turn sees the resolved owner (build_extra_data
        re-fetches credential via TelegramCredentialManager.get)."""
        raw_msg = message.raw.get("message") or {}
        from_user = raw_msg.get("from") or {}
        sender_username = (from_user.get("username") or "").strip().lstrip("@")
        if not sender_username:
            return
        if sender_username.lower() != credential.owner_username.lower():
            # Different sender — do NOT claim. owner_username is the lock.
            return

        first = (from_user.get("first_name") or "").strip()
        last = (from_user.get("last_name") or "").strip()
        owner_name = " ".join(p for p in (first, last) if p) or sender_username

        if not self._db:
            return

        mgr = TelegramCredentialManager(self._db)
        await mgr.update_owner(
            credential.agent_id,
            owner_user_id=message.sender_id,
            owner_name=owner_name,
        )
        # In-memory mutation so the rest of THIS turn sees the resolved
        # owner. Build_extra_data downstream re-fetches from DB but the
        # update_owner() write above ensures consistency.
        credential.owner_user_id = message.sender_id
        credential.owner_name = owner_name

    # ────────────────────────────────────────────────────────────────────
    # Reply-side overrides
    # ────────────────────────────────────────────────────────────────────

    def extract_output(
        self, result, message: ParsedMessage, credential: TelegramCredential
    ) -> str:
        """Pull reply text from the AgentRuntime result for inbox display.

        Phase 3 Slack regression — DO NOT use ``result.output_text``;
        it contains the agent's reasoning ("My thought process: ...")
        and would leak chain-of-thought into the inbox. Instead scrape
        the ``tg_cli`` tool-call args for ``method=sendMessage``.
        """
        replies: list[str] = []
        for raw in getattr(result, "raw_items", []) or []:
            if not isinstance(raw, dict):
                continue
            item = raw.get("item", {})
            if item.get("type") != "tool_call_item":
                continue
            sent_text = self._extract_tg_reply(item)
            if sent_text:
                replies.append(sent_text)

        if replies:
            output_text = "\n".join(replies)
        else:
            # Agent produced reasoning but never called tg_cli to send.
            output_text = CHANNEL_SILENT_SENTINEL

        bot_label = credential.bot_username or credential.agent_id
        logger.info(
            f"TelegramTrigger [{bot_label}] agent responded: {output_text[:200]}"
        )
        return output_text

    async def send_channel_reply(
        self, credential: TelegramCredential, message: ParsedMessage, text: str
    ) -> None:
        """Error-fallback send: a plain-text message to the originating chat.
        Reuses the per-subscriber SDK client (shared aiohttp session) when
        present, else constructs a short-lived one."""
        key = self._subscriber_key(credential)
        client = self._sdk_clients.get(key) or TelegramSDKClient(credential.bot_token)
        await client.send_message(message.chat_id, text)

    @staticmethod
    def _extract_tg_reply(item: dict) -> str:
        """Extract sent text from a ``tg_cli`` tool call item.

        ``tg_cli`` signature: ``(agent_id, method, args)`` where ``args``
        is a dict. For inbox display we only care about
        ``method == "sendMessage"`` and ``args["text"]``.
        Other Telegram methods (sendChatAction, deleteMessage,
        editMessageText, etc.) are not user-visible reply text.
        """
        tool_name = item.get("tool_name", "")
        if "tg_cli" not in tool_name:
            return ""

        raw_args = item.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except (ValueError, TypeError):
                return ""
        if not isinstance(raw_args, dict):
            return ""

        if raw_args.get("method") != "sendMessage":
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
        """User-friendly error string for the inbox + Telegram reply."""
        return (
            f"Sorry, I hit an error processing your message: "
            f"{getattr(error, 'message', '') or 'unknown'}"
        )
