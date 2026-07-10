"""
@file_name: wechat_trigger.py
@author:
@date: 2026-06-24
@description: WeChat (iLink) channel trigger built on ``ChannelTriggerBase``.

iLink is PULL-only (long-poll ``getupdates``) — like Telegram's getUpdates, no
webhook / public IP. Each active credential gets one long-poll loop; events flow
through the shared dedup → debounce → worker → AgentRuntime pipeline owned by
the base class. The agent replies by calling the ``wechat_send`` MCP tool;
``extract_output`` scrapes that call for the inbox record.

WeChat-specific concerns handled here:
  - **Cursor, not offset**: iLink returns ``get_updates_buf`` (an opaque cursor)
    instead of a numeric update_id. Advance it after each batch.
  - **App-level failures**: ``get_updates`` raises on ``ret != 0`` (session
    expired / bad token) so the base class backs off + reconnects.
  - **First-DM owner claim**: the owner's wxid is opaque until they DM the
    freshly bound account (bind is owner-initiated from the panel, but the
    wxid isn't known then) — the first inbound DM claims owner (CAS).
  - **DM-only (v1)**: personal-account 1:1. No group handling.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Optional

from loguru import logger

from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from xyz_agent_context.channel.channel_trigger_base import (
    CHANNEL_SILENT_SENTINEL,
    ChannelHistoryConfig,
    ChannelTriggerBase,
)
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)

from ._wechat_credential_manager import WeChatCredential, WeChatCredentialManager
from .wechat_context_builder import WeChatContextBuilder
from .wechat_sdk_client import (
    WeChatSDKClient,
    WeChatSDKError,
    extract_text,
    send_text_once,
)


class WeChatTrigger(ChannelTriggerBase):
    """WeChat (iLink) long-poll trigger."""

    channel_name = "wechat"
    brand_display = "WeChat"
    working_source = WorkingSource.WECHAT

    # Idle wake-up so ``self.running`` is checked even when the bot is quiet.
    POLL_IDLE_SLEEP_SECONDS = 0.5

    def __init__(self, max_workers: int = 3):
        super().__init__(
            base_workers=max_workers,
            # iLink has no server-side history API; ChannelInboxWriter persists
            # every turn to bus_messages under channel_id=f"wechat_{to_user_id}",
            # which WeChatContextBuilder reads. Same contract as Telegram.
            history_config=ChannelHistoryConfig(
                load_conversation_history=True,
                history_limit=20,
                history_max_chars=20000,
            ),
        )
        self._cursors: dict[str, str] = {}  # per-agent getupdates cursor
        self._sdk_clients: dict[str, WeChatSDKClient] = {}

    async def stop(self) -> None:
        for key, client in list(self._sdk_clients.items()):
            try:
                await asyncio.wait_for(client.aclose(), timeout=3.0)
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                logger.warning(f"[wechat:{key}] client close during stop: {e}")
        self._sdk_clients.clear()
        await super().stop()

    # ── Abstract method implementations ──────────────────────────────────

    async def load_active_credentials(self) -> list[WeChatCredential]:
        if not self._db:
            return []
        return await WeChatCredentialManager(self._db).list_active()

    def _subscriber_key(self, credential: WeChatCredential) -> str:  # type: ignore[override]
        return credential.agent_id

    def is_permanent_auth_failure(self, exc: BaseException) -> bool:  # type: ignore[override]
        # A getupdates ``ret != 0`` means the iLink session is expired / the
        # token is bad — reconnecting can never recover it. Treat it as terminal
        # so the base class disables the credential and the loop exits, instead
        # of reconnecting against a dead session every 120s forever (the
        # zombie-reconnect incident class — CLAUDE.md lesson #1). A send-side
        # ``ret != 0`` is per-message (stale context_token) and never reaches
        # the connect loop; transient network errors are not WeChatSDKError and
        # so keep retrying under the default backoff.
        return isinstance(exc, WeChatSDKError) and exc.source == "updates"

    async def disable_credential(self, credential: WeChatCredential) -> None:  # type: ignore[override]
        if not self._db:
            return
        await WeChatCredentialManager(self._db).set_enabled(credential.agent_id, False)

    async def connect(self, credential: WeChatCredential) -> AsyncIterator[dict]:
        """Long-poll loop. Yields raw iLink message dicts.

        ``get_updates`` raises ``RuntimeError`` on an app-level ``ret != 0`` so
        the base class backs off + reconnects (no manual retry here). The cursor
        advances per batch; an in-flight crash replays the batch (the base
        dedup store catches double-delivery on the happy path).
        """
        client = WeChatSDKClient(credential.bot_token, credential.base_url)
        key = self._subscriber_key(credential)
        self._sdk_clients[key] = client
        cursor = self._cursors.get(key, "")
        logger.info(f"[wechat:{credential.agent_id}] long-poll started")
        try:
            while self.running:
                data = await client.get_updates(cursor)
                cursor = data.get("get_updates_buf", cursor)
                self._cursors[key] = cursor
                msgs = data.get("msgs") or []
                for msg in msgs:
                    if not self.running:
                        break
                    yield msg
                if not msgs:
                    await asyncio.sleep(self.POLL_IDLE_SLEEP_SECONDS)
        finally:
            try:
                await asyncio.wait_for(client.aclose(), timeout=3.0)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
            self._sdk_clients.pop(key, None)

    def parse_event(self, raw: dict) -> Optional[ParsedMessage]:
        """iLink message dict → ParsedMessage. None means "skip" (v1: text-only).

        ``raw`` carries ``from_user_id`` + ``context_token``; both are kept in
        ``ParsedMessage.raw`` so the reply path (wechat_send) can address the
        peer. iLink exposes no per-message id, so we key the message on the
        context_token (falling back to the sender).
        """
        text = extract_text(raw)
        from_user = raw.get("from_user_id") or ""
        if not text or not from_user:
            return None
        context_token = raw.get("context_token", "") or ""
        return ParsedMessage(
            message_id=context_token or f"wx_{from_user}",
            chat_id=from_user,
            sender_id=from_user,
            sender_name=from_user,
            content=text,
            content_type=MessageContentType.TEXT,
            chat_type=ChatType.PRIVATE,
            raw=raw,
        )

    async def is_echo(self, message: ParsedMessage, credential: WeChatCredential) -> bool:
        # iLink getupdates returns inbound peer DMs, not our own sends. Guard
        # against an echo only if the bot's own wxid is known.
        if credential.bot_wx_id and message.sender_id == credential.bot_wx_id:
            return True
        return False

    async def resolve_sender_name(self, sender_id: str, credential: WeChatCredential) -> str:
        # iLink has no user-info-by-id API; the wxid is the best label we have.
        return sender_id

    def create_context_builder(
        self, message: ParsedMessage, credential: WeChatCredential, agent_id: str
    ) -> ChannelContextBuilderBase:
        return WeChatContextBuilder(
            message=message,
            credential=credential,
            agent_id=agent_id,
            db_client=self._db,
        )

    # ── Inbound preprocessing — first-DM owner claim ─────────────────────

    async def _process_message(
        self, credential: WeChatCredential, message: ParsedMessage
    ) -> None:
        if not credential.owner_wx_id and message.sender_id and self._db:
            mgr = WeChatCredentialManager(self._db)
            if await mgr.claim_owner(credential.agent_id, message.sender_id):
                # In-memory mutation so the rest of THIS turn sees the owner.
                credential.owner_wx_id = message.sender_id
        return await super()._process_message(credential, message)

    # ── Reply-side override ──────────────────────────────────────────────

    def extract_output(
        self, result, message: ParsedMessage, credential: WeChatCredential
    ) -> str:
        """Pull reply text from the ``wechat_send`` tool-call args for the inbox
        record (NOT result.output_text — that leaks the agent's reasoning)."""
        replies: list[str] = []
        for raw in getattr(result, "raw_items", []) or []:
            if not isinstance(raw, dict):
                continue
            item = raw.get("item", {})
            if item.get("type") != "tool_call_item":
                continue
            sent = self._extract_wechat_reply(item)
            if sent:
                replies.append(sent)
        output = "\n".join(replies) if replies else CHANNEL_SILENT_SENTINEL
        logger.info(f"WeChatTrigger [{credential.agent_id}] agent responded: {output[:200]}")
        return output

    async def send_channel_reply(
        self, credential: WeChatCredential, message: ParsedMessage, text: str
    ) -> None:
        """Error-fallback send: a WeChat DM addressed by the inbound message's
        sender + context_token — the same routing ``wechat_send`` uses."""
        context_token = (message.raw or {}).get("context_token", "") or ""
        await send_text_once(
            credential.bot_token,
            credential.base_url,
            message.sender_id,
            context_token,
            text,
        )

    @staticmethod
    def _extract_wechat_reply(item: dict) -> str:
        if "wechat_send" not in (item.get("tool_name", "") or ""):
            return ""
        raw_args: Any = item.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except (ValueError, TypeError):
                return ""
        if not isinstance(raw_args, dict):
            return ""
        return raw_args.get("text", "") or ""
