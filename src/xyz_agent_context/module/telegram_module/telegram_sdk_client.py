"""
@file_name: telegram_sdk_client.py
@date: 2026-05-09
@description: Async Telegram Bot API client (raw aiohttp — no heavy SDK).

Telegram has no official Python SDK we want to depend on (python-telegram-bot
is heavy + opinionated; aiogram has its own loop assumptions). Bot API is a
plain HTTPS-JSON surface, so we wrap aiohttp directly. Each method PUTs to
``https://api.telegram.org/bot{token}/{method}`` with a JSON body.

Mirrors ``slack_sdk_client.py`` shape: thin per-method wrappers for the hot
methods we use directly (get_me, send_message, get_updates, get_chat,
delete_webhook) plus a generic ``api_call`` dispatcher that backs the
``tg_cli`` MCP tool.
"""

from __future__ import annotations

from typing import Any, Optional

import aiohttp
from loguru import logger


_API_BASE = "https://api.telegram.org/bot"


class TelegramSDKError(RuntimeError):
    """Raised when the Bot API returns ``{"ok": false}`` or HTTP failure.

    Carries the upstream ``description`` (mapped to ``code``) so callers
    can branch without parsing strings.
    """

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


class TelegramSDKClient:
    """Async Telegram Bot API client. One instance per credential / call site."""

    def __init__(self, bot_token: str, *, timeout_seconds: float = 35.0):
        if not bot_token:
            raise ValueError("bot_token must be provided")
        self._bot_token = bot_token
        # Long-poll uses 30s server-side; client timeout must exceed that.
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def base_url(self) -> str:
        return f"{_API_BASE}{self._bot_token}"

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def __aenter__(self) -> "TelegramSDKClient":
        await self._ensure_session()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Generic dispatcher (backs `tg_cli` MCP tool)
    # ------------------------------------------------------------------

    async def api_call(
        self, method: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Generic Bot API dispatcher.

        Returns Telegram's native envelope ``{"ok": bool, "result"?,
        "description"?}``. Failures (HTTP non-2xx, ok=false, exceptions)
        are surfaced as ``{"ok": false, "error": "...", "method": ...}``
        rather than raising — agents read the envelope per the
        per-method skill docs.
        """
        url = f"{self.base_url}/{method}"
        try:
            session = await self._ensure_session()
            async with session.post(url, json=args) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    return {
                        "ok": False,
                        "error": data.get("description", f"http_{resp.status}"),
                        "method": method,
                    }
                return data
        except aiohttp.ClientError as e:
            return {
                "ok": False,
                "error": f"client_error:{type(e).__name__}",
                "method": method,
            }
        except Exception as e:  # pragma: no cover — defensive
            logger.exception(f"[telegram] api_call({method}) unexpected error")
            return {
                "ok": False,
                "error": f"client_exception:{type(e).__name__}",
                "method": method,
            }

    # ------------------------------------------------------------------
    # Hot-path wrappers (raise on failure for caller-side ergonomics)
    # ------------------------------------------------------------------

    async def get_me(self) -> dict[str, Any]:
        """Validate token + return bot identity (id, username, first_name)."""
        resp = await self.api_call("getMe", {})
        if not resp.get("ok"):
            raise TelegramSDKError(resp.get("error", "unknown"), "getMe failed")
        return resp.get("result", {})

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        reply_to_message_id: Optional[str] = None,
        message_thread_id: Optional[str] = None,
        parse_mode: Optional[str] = None,
    ) -> dict[str, Any]:
        """sendMessage with optional reply / thread / parse_mode.

        ``parse_mode`` defaults to None (plain text) — MarkdownV2 escape
        rules are aggressive (``_*[]()~>#+-=|{}.!\\``) and wrong escaping
        produces 400 errors. Phase 4 stays plain-text.
        """
        args: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id:
            args["reply_to_message_id"] = int(reply_to_message_id)
        if message_thread_id:
            args["message_thread_id"] = int(message_thread_id)
        if parse_mode:
            args["parse_mode"] = parse_mode
        resp = await self.api_call("sendMessage", args)
        if not resp.get("ok"):
            raise TelegramSDKError(resp.get("error", "unknown"), "sendMessage failed")
        return resp.get("result", {})

    async def get_updates(
        self,
        offset: int = 0,
        timeout: int = 30,
        allowed_updates: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """Long-poll for updates. Blocks up to ``timeout`` seconds.

        Returns the ``result`` array (may be empty). Caller advances
        ``offset = updates[-1].update_id + 1`` to ack consumed updates.
        """
        args: dict[str, Any] = {"offset": offset, "timeout": timeout}
        if allowed_updates is not None:
            args["allowed_updates"] = allowed_updates
        resp = await self.api_call("getUpdates", args)
        if not resp.get("ok"):
            raise TelegramSDKError(resp.get("error", "unknown"), "getUpdates failed")
        result = resp.get("result", [])
        return list(result) if isinstance(result, list) else []

    async def delete_webhook(self) -> bool:
        """deleteWebhook — idempotent; required defensively before first
        ``getUpdates`` because Telegram refuses long-poll while a webhook
        is set (``409 Conflict: terminated by setWebhook``)."""
        resp = await self.api_call("deleteWebhook", {})
        return bool(resp.get("ok"))

    async def send_chat_action(
        self, chat_id: str | int, action: str = "typing"
    ) -> bool:
        """sendChatAction — shows a transient activity hint on the
        recipient's client.

        Telegram displays the indicator for ~5 seconds, after which it
        auto-clears unless re-sent. The caller is responsible for
        re-firing every ~4s if the action is supposed to span a longer
        operation. Failures are non-fatal — the indicator is decorative,
        not load-bearing — so we return False rather than raising.

        Valid ``action`` values include ``typing``, ``upload_photo``,
        ``record_video``, ``record_voice``, ``upload_document``,
        ``choose_sticker``, ``find_location``. Anything else gets a 400
        from Telegram, which we suppress.
        """
        resp = await self.api_call(
            "sendChatAction", {"chat_id": chat_id, "action": action}
        )
        return bool(resp.get("ok"))

    async def get_chat(self, chat_id_or_handle: str | int) -> dict[str, Any]:
        """getChat — also resolves @username → numeric user_id (for the
        owner-trust signal at bind time)."""
        resp = await self.api_call("getChat", {"chat_id": chat_id_or_handle})
        if not resp.get("ok"):
            raise TelegramSDKError(resp.get("error", "unknown"), "getChat failed")
        return resp.get("result", {})

    async def get_chat_member(
        self, chat_id: str | int, user_id: str | int
    ) -> dict[str, Any]:
        """getChatMember — used to resolve sender display name from a chat."""
        resp = await self.api_call(
            "getChatMember", {"chat_id": chat_id, "user_id": user_id}
        )
        if not resp.get("ok"):
            return {}
        return resp.get("result", {})
