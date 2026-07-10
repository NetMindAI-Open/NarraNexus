"""
@file_name: discord_trigger.py
@date: 2026-06-16
@description: Discord channel trigger built on ``ChannelTriggerBase``.

Uses the Discord Gateway (a persistent WebSocket) via discord.py. The
library is callback-driven (``@client.event async def on_message``); we
bridge those callbacks into the base class's async-generator ``connect``
contract via an ``asyncio.Queue`` — the exact pattern SlackTrigger uses
for Socket Mode.

Discord-specific concerns this class handles:
  - **Message Content Intent**: enabled on the gateway client. Without
    the matching toggle in the Developer Portal, Discord delivers events
    with an empty ``content`` — that's a user-side setup gap, surfaced in
    the module instructions, not something code can fix.
  - **Reply policy**: in guild (server) channels the bot replies ONLY
    when @-mentioned in the current message; DMs always pass through.
    Non-mention guild messages are dropped at the gateway boundary so the
    bot stays silent in busy servers until addressed.
  - **Bot-loop guard**: messages authored by ANY bot (including our own)
    are dropped at parse time, so two NarraNexus agents in the same
    channel can't ping-pong forever.
  - **Snowflake ids**: channel / user / message ids are uint64 delivered
    as strings throughout.

The base class owns: dedup, worker pool, credential watcher, audit log,
inbox writer, reconnect backoff. We override the abstract surface
(connect/parse/echo/sender/builder/load) plus attachment download.

discord.py is imported lazily/guarded so the module package still imports
in environments where the dependency isn't installed (the trigger just
refuses to start).
"""
from __future__ import annotations

import asyncio
import os
import re
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

from ._discord_credential_manager import DiscordCredential, DiscordCredentialManager
from .discord_context_builder import DiscordContextBuilder
from .discord_sdk_client import (
    PERMANENT_AUTH_CODES,
    DiscordSDKClient,
    DiscordSDKError,
)

try:
    import discord

    _HAS_DISCORD = True
except ImportError:  # pragma: no cover
    discord = None  # type: ignore[assignment]
    _HAS_DISCORD = False

# Sentinel key on a queue item that signals the gateway client task ended.
_CLIENT_EXIT = "__discord_client_exit__"


class DiscordTrigger(ChannelTriggerBase):
    """Discord channel trigger. One Gateway WebSocket per credential."""

    # ── ChannelTriggerBase contract ───────────────────────────────────────
    channel_name = "discord"
    brand_display = "Discord"
    working_source = WorkingSource.DISCORD

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

    # Discord users often send burst follow-up messages — debounce merges them.
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
        # One gateway client per credential, kept so we can disconnect cleanly.
        self._clients: dict[str, Any] = {}

    # ────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ────────────────────────────────────────────────────────────────────

    async def start(self, db) -> None:
        if not _HAS_DISCORD:
            raise RuntimeError(
                "discord.py not installed — cannot run DiscordTrigger. "
                "Install with `uv add discord.py`."
            )
        await super().start(db)
        logger.info(
            f"DiscordTrigger started: {len(self._workers)} workers, "
            f"watching channel_discord_credentials for active rows"
        )

    async def stop(self) -> None:
        for key, client in list(self._clients.items()):
            try:
                if not client.is_closed():
                    await asyncio.wait_for(client.close(), timeout=3.0)
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                logger.warning(f"[discord:{key}] close during stop: {e}")
        self._clients.clear()
        await super().stop()

    # ────────────────────────────────────────────────────────────────────
    # Abstract method implementations
    # ────────────────────────────────────────────────────────────────────

    async def load_active_credentials(self) -> list[DiscordCredential]:
        if not self._db:
            return []
        mgr = DiscordCredentialManager(self._db)
        return await mgr.list_active()

    def _subscriber_key(self, credential: DiscordCredential) -> str:  # type: ignore[override]
        # Discord credentials have no app_id; one bot per agent, agent_id unique.
        return credential.agent_id

    def is_permanent_auth_failure(self, exc: BaseException) -> bool:  # type: ignore[override]
        if _HAS_DISCORD and isinstance(exc, discord.LoginFailure):
            return True
        if isinstance(exc, DiscordSDKError):
            return (exc.code or "") in PERMANENT_AUTH_CODES
        return False

    async def disable_credential(self, credential: DiscordCredential) -> None:  # type: ignore[override]
        if not self._db:
            return
        mgr = DiscordCredentialManager(self._db)
        await mgr.set_enabled(credential.agent_id, False)

    async def connect(self, credential: DiscordCredential) -> AsyncIterator[dict]:
        """Gateway WebSocket → asyncio.Queue → async generator.

        discord.py's Client dispatches events to ``@client.event``
        handlers; we register ``on_message`` to enqueue a normalized raw
        dict, run ``client.start`` as a background task, and yield from
        the queue. When the client task ends (auth failure, disconnect),
        its exception is surfaced onto the queue so the base loop can
        decide between permanent-disable and reconnect-with-backoff.
        """
        assert _HAS_DISCORD, "guarded in start()"

        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.dm_messages = True
        intents.message_content = True  # privileged — must match Portal toggle

        # discord.py does NOT read HTTPS_PROXY / HTTP_PROXY from the environment
        # for either the REST login or the Gateway WebSocket — the proxy must be
        # passed explicitly (mirrors SlackTrigger passing proxy= to
        # SocketModeClient). Without this, reaching discord.com from a network
        # behind a forward proxy (mainland China is the canonical case) times
        # out and the Gateway never connects. discord.py's proxy= takes an HTTP
        # proxy URL and applies it to BOTH the REST login and the ws connection.
        proxy_url = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
        )
        client = discord.Client(intents=intents, proxy=proxy_url)
        if proxy_url:
            logger.info(
                f"[discord:{credential.agent_id}] gateway using proxy {proxy_url}"
            )

        queue: asyncio.Queue[dict] = asyncio.Queue()

        @client.event
        async def on_message(message):  # noqa: ANN001 — discord.Message
            try:
                raw = self._message_to_raw(message, client.user)
                await queue.put(raw)
            except Exception:  # pragma: no cover — defensive
                logger.exception(f"[discord:{credential.agent_id}] on_message enqueue failed")

        client_task = asyncio.create_task(client.start(credential.bot_token))

        def _on_client_done(task: asyncio.Task) -> None:
            err: Optional[BaseException] = None
            if not task.cancelled():
                err = task.exception()
            try:
                queue.put_nowait({_CLIENT_EXIT: True, "error": err})
            except Exception:  # pragma: no cover — queue full/closed
                pass

        client_task.add_done_callback(_on_client_done)

        key = self._subscriber_key(credential)
        self._clients[key] = client
        logger.info(f"[discord:{credential.agent_id}] gateway client starting")

        try:
            while self.running:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue
                if item.get(_CLIENT_EXIT):
                    err = item.get("error")
                    if err is not None:
                        # Re-raise so the base loop runs backoff / permanent
                        # detection (is_permanent_auth_failure handles
                        # discord.LoginFailure).
                        raise err
                    return  # clean shutdown
                yield item
        finally:
            try:
                if not client.is_closed():
                    await asyncio.wait_for(client.close(), timeout=3.0)
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                logger.warning(
                    f"[discord:{credential.agent_id}] close on subscribe-loop exit: {e}"
                )
            self._clients.pop(key, None)
            if not client_task.done():
                client_task.cancel()

    @staticmethod
    def _strip_bot_mention(content: str, bot_user_id: Any) -> str:
        """Remove the bot's own @-mention tokens from message content.

        Discord embeds an @-mention as raw markup ``<@123>`` (or the nickname
        form ``<@!123>``). Only the BOT's own mention is removed — other users'
        mentions are left intact so "@bot ping @alice" keeps "@alice". The
        whitespace the removed token leaves behind is collapsed.

        A bare ping ("@bot" with no other text) would strip to empty, which the
        trigger's empty-content guard then drops — so the bot would silently
        ignore a direct ping. To avoid that, an all-stripped result falls back
        to the original content so the message still reaches the agent.
        """
        if not content or bot_user_id in (None, ""):
            return content
        stripped = re.sub(rf"<@!?{re.escape(str(bot_user_id))}>", "", content)
        stripped = re.sub(r"\s{2,}", " ", stripped).strip()
        return stripped if stripped else content

    @staticmethod
    def _message_to_raw(message: Any, bot_user: Any) -> dict:
        """Normalize a discord.Message into a plain dict (testable shape)."""
        guild = getattr(message, "guild", None)
        is_dm = guild is None
        author = message.author
        mentioned_ids = [str(u.id) for u in getattr(message, "mentions", [])]
        mentions_me = bool(bot_user) and any(
            u.id == bot_user.id for u in getattr(message, "mentions", [])
        )

        # Strip the bot's OWN @-mention from the body. In a guild the user
        # addresses the bot as "@bot hi", which Discord delivers as raw markup
        # "<@BOTID> hi". The opaque numeric token is noise the model cannot map
        # to "this is me" and it degraded channel replies (DMs, with no such
        # prefix, were unaffected — the DM-works / channel-blank asymmetry). The
        # reply policy (mentions_me, above) is computed from the structured
        # `mentions` list, so stripping the markup here doesn't affect gating.
        content = message.content or ""
        if bot_user is not None:
            content = DiscordTrigger._strip_bot_mention(content, bot_user.id)

        refs: list[dict[str, Any]] = []
        for a in getattr(message, "attachments", []) or []:
            refs.append(
                {
                    "kind": "file",
                    "platform_ref": str(a.id),
                    "url": a.url,
                    "original_name": a.filename or str(a.id),
                    "mime_hint": getattr(a, "content_type", "") or "",
                    "size_hint": int(getattr(a, "size", 0) or 0),
                }
            )

        reference_id = None
        ref = getattr(message, "reference", None)
        if ref is not None and getattr(ref, "message_id", None):
            reference_id = str(ref.message_id)

        created = getattr(message, "created_at", None)
        created_ms = int(created.timestamp() * 1000) if created else 0

        return {
            "message_id": str(message.id),
            "channel_id": str(message.channel.id),
            "guild_id": str(guild.id) if guild else "",
            "author_id": str(author.id),
            "author_name": getattr(author, "display_name", None) or author.name,
            "author_is_bot": bool(getattr(author, "bot", False)),
            "content": content,
            "is_dm": is_dm,
            "mentions_me": mentions_me,
            "mentioned_ids": mentioned_ids,
            "reference_id": reference_id,
            "created_at_ms": created_ms,
            "attachment_refs": refs,
        }

    def parse_event(self, raw: dict) -> Optional[ParsedMessage]:
        """Discord raw dict → ParsedMessage. None means "skip"."""
        # Bot-loop guard: never engage messages authored by any bot (our own
        # echo is also caught by is_echo, but other bots are dropped here so
        # two agents can't ping-pong).
        if raw.get("author_is_bot"):
            return None

        is_dm = bool(raw.get("is_dm"))
        # Reply policy: guild messages require an @-mention of this bot.
        if not is_dm and not raw.get("mentions_me"):
            return None

        sender_id = raw.get("author_id", "")
        if not sender_id:
            return None

        message_id = raw.get("message_id", "")
        if not message_id:
            return None

        content = raw.get("content", "") or ""
        refs = raw.get("attachment_refs") or []
        if not content and not refs:
            return None

        # Derive content_type from the leading attachment ref.
        content_type = MessageContentType.TEXT
        if refs:
            primary_mime = refs[0].get("mime_hint", "") or ""
            if primary_mime.startswith("image/"):
                content_type = MessageContentType.IMAGE
            elif primary_mime.startswith("audio/"):
                content_type = MessageContentType.AUDIO
            elif primary_mime.startswith("video/"):
                content_type = MessageContentType.VIDEO
            else:
                content_type = MessageContentType.FILE

        return ParsedMessage(
            message_id=message_id,
            chat_id=raw.get("channel_id", ""),
            sender_id=sender_id,
            sender_name=raw.get("author_name", "") or "",
            content=content,
            content_type=content_type,
            chat_type=ChatType.PRIVATE if is_dm else ChatType.GROUP,
            timestamp_ms=int(raw.get("created_at_ms", 0) or 0),
            reply_to_message_id=raw.get("reference_id"),
            mentions=list(raw.get("mentioned_ids") or []),
            raw=raw,
        )

    async def fetch_attachments(  # type: ignore[override]
        self, message: ParsedMessage, credential: DiscordCredential
    ) -> list[Attachment]:
        """Download Discord CDN attachments and persist. Never raises."""
        refs = (message.raw or {}).get("attachment_refs") or []
        if not refs:
            return []

        client = DiscordSDKClient(credential.bot_token)
        from backend.config import settings as backend_settings

        max_bytes = backend_settings.max_upload_bytes

        out: list[Attachment] = []
        for ref in refs:
            url = ref.get("url") or ""
            platform_ref = ref.get("platform_ref") or ""
            if not url:
                continue
            original_name = ref.get("original_name") or platform_ref
            mime_hint = ref.get("mime_hint", "") or ""
            size_hint = int(ref.get("size_hint", 0) or 0)

            if size_hint and size_hint > max_bytes:
                await self._audit(
                    EVENT_INGRESS_DROPPED_OVERSIZED,
                    message_id=message.message_id,
                    agent_id=credential.agent_id,
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
                raw_bytes = await client.download_url(url, max_bytes=max_bytes)
            except DiscordSDKError as e:
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
                    details={"platform_ref": platform_ref, "stage": "download", "error": f"{e.code}:{e}"},
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
                    details={"platform_ref": platform_ref, "stage": "persist", "error": f"{type(e).__name__}:{e}"},
                )
                continue

            out.append(att)
            await self._audit(
                EVENT_ATTACHMENT_PERSISTED,
                message_id=message.message_id,
                agent_id=credential.agent_id,
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
        self, message: ParsedMessage, credential: DiscordCredential
    ) -> bool:
        """True when the message was sent by our own bot user."""
        if credential.bot_user_id:
            return message.sender_id == credential.bot_user_id
        # Fall back to the bot flag carried on the raw event.
        return bool((message.raw or {}).get("author_is_bot"))

    async def resolve_sender_name(
        self, sender_id: str, credential: DiscordCredential
    ) -> str:
        """Resolve a display name. The gateway already carries the author
        name (parse_event fills ``sender_name``), so this REST fallback only
        fires when that was somehow empty."""
        client = DiscordSDKClient(credential.bot_token)
        user = await client.get_user(sender_id)
        return user.get("global_name") or user.get("username", "") or sender_id

    def create_context_builder(
        self,
        message: ParsedMessage,
        credential: DiscordCredential,
        agent_id: str,
    ) -> ChannelContextBuilderBase:
        return DiscordContextBuilder(
            message=message, credential=credential, agent_id=agent_id
        )

    # ────────────────────────────────────────────────────────────────────
    # Reply extraction for inbox display
    # ────────────────────────────────────────────────────────────────────

    def extract_output(
        self, result, message: ParsedMessage, credential: DiscordCredential
    ) -> str:
        """Pull reply text from the AgentRuntime result for inbox display.

        Discord agents reply by calling ``discord_send`` / ``discord_reply``,
        not by producing reply text directly — so scrape sent text from the
        tool-call items. If the agent never sent, mark it "(stayed silent)".
        """
        replies: list[str] = []
        for raw in getattr(result, "raw_items", []):
            if not isinstance(raw, dict):
                continue
            item = raw.get("item", {})
            if item.get("type") != "tool_call_item":
                continue
            sent = self._extract_sent_text(item)
            if sent:
                replies.append(sent)

        output_text = "\n".join(replies) if replies else CHANNEL_SILENT_SENTINEL
        logger.info(
            f"DiscordTrigger [{credential.bot_username or credential.agent_id}] "
            f"agent responded: {output_text[:200]}"
        )
        return output_text

    async def send_channel_reply(
        self, credential: DiscordCredential, message: ParsedMessage, text: str
    ) -> None:
        """Error-fallback send: post into the originating Discord channel."""
        await DiscordSDKClient(credential.bot_token).send_message(
            message.chat_id, text
        )

    @staticmethod
    def _extract_sent_text(item: dict) -> str:
        """Extract sent text from a discord_send / discord_reply / discord_dm call."""
        import json

        tool_name = item.get("tool_name", "")
        if not any(t in tool_name for t in ("discord_send", "discord_reply", "discord_dm")):
            return ""
        raw_args = item.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except (ValueError, TypeError):
                return ""
        if not isinstance(raw_args, dict):
            return ""
        text = raw_args.get("text", "")
        return text.strip() if isinstance(text, str) else ""

    def format_error_reply(self, error) -> str:
        return (
            f"Sorry, I hit an error processing your message: "
            f"{getattr(error, 'message', '') or 'unknown'}"
        )
