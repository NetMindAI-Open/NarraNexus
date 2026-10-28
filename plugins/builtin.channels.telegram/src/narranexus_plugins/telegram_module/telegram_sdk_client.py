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
_FILE_BASE = "https://api.telegram.org/file/bot"

# Telegram's bot API caps downloadable file size at 20 MiB. This is a
# PLATFORM limit, separate from ``backend.config.settings.max_upload_bytes``
# — even if the user configured a higher backend cap, getFile will refuse
# any file larger than this with ``file is too big``. Document the constant
# here so the pre-check error message is self-explanatory.
TELEGRAM_BOT_DOWNLOAD_CAP_BYTES = 20 * 1024 * 1024


class TelegramSDKError(RuntimeError):
    """Raised when the Bot API returns ``{"ok": false}`` or HTTP failure.

    Carries the upstream ``description`` (mapped to ``code``) so callers
    can branch without parsing strings, plus the HTTP status / Telegram
    ``error_code`` (``status``, None for transport failures) so the poller
    can tell a revoked token (401) from a competing poller (409) from a
    Telegram outage (5xx). ``str()`` names status and description — the
    line the base trigger logs must be diagnosable on its own (dev logs
    once showed 115 bare ``getUpdates failed`` lines across three agents).
    """

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        status: Optional[int] = None,
        description: str = "",
    ):
        self.code = code
        self.status = status
        self.description = description or code
        detail = self.description
        if status is not None:
            detail = f"HTTP {status}: {detail}"
        super().__init__(f"{message} ({detail})" if message else detail)

    @classmethod
    def from_envelope(cls, envelope: dict[str, Any], message: str) -> "TelegramSDKError":
        """Build from an ``api_call`` failure envelope (``error`` + ``error_code``)."""
        code = str(envelope.get("error") or "unknown")
        raw_status = envelope.get("error_code")
        status = int(raw_status) if isinstance(raw_status, int) else None
        detail = str(envelope.get("error_detail") or "")
        return cls(code, message, status=status, description=f"{code}: {detail}" if detail else code)


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
    def _base_url(self) -> str:
        """Bot API base URL with token embedded — INTERNAL ONLY.

        Leading underscore is load-bearing: this string contains the
        bot's auth credential in its path (``https://api.telegram.org/bot{TOKEN}``).
        Printing it anywhere (logger, error message, exception repr) leaks
        the credential. The download_file path constructs URLs separately
        using ``_FILE_BASE`` so that path stays safe even when callers
        get ad-hoc with this property.
        """
        return f"{_API_BASE}{self._bot_token}"

    def _redact(self, text: str) -> str:
        """Strip the bot token from any text that came out of aiohttp.

        Both request URLs (``_API_BASE`` / ``_FILE_BASE``) carry the token
        in their path, and aiohttp exceptions such as ``InvalidURL`` quote
        the URL in ``str(e)``. Everything built from an exception message
        goes through here BEFORE it becomes an envelope field or a
        ``TelegramSDKError`` description, so the token cannot reach
        ``error_detail`` (agent-visible tg_cli JSON), ``str(exc)`` or the
        traceback a caller's ``logger.exception`` renders.
        """
        return text.replace(self._bot_token, "<token>") if self._bot_token else text

    def _failure(self, method: str, *, max_len: Optional[int] = None, **fields: Any) -> dict[str, Any]:
        """The ``{"ok": false, ...}`` envelope with every string field redacted.

        One constructor for all failure paths so "envelope strings never
        carry the bot token" holds structurally — a proxy's HTML error page
        echoing the request URL (``error_detail``) is as covered as an
        aiohttp exception message. Truncation is owned HERE too: callers
        pass the raw text plus ``max_len`` and this method redacts first,
        then cuts, then flattens newlines — so a token straddling the cut
        can never leave its first half behind, by construction rather than
        by caller discipline.
        """
        out: dict[str, Any] = {"ok": False, "method": method}
        for key, value in fields.items():
            if isinstance(value, str):
                value = self._redact(value)
                if max_len is not None:
                    value = value[:max_len]
                value = value.replace("\n", " ")
            out[key] = value
        return out

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # ``trust_env=True`` makes aiohttp honour the standard
            # ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``NO_PROXY`` env vars
            # (and ``~/.netrc`` for HTTP auth). Without this flag aiohttp
            # IGNORES those vars by default — a long-standing gotcha that
            # breaks every CN developer trying to reach ``api.telegram.org``
            # through a local Clash / V2Ray HTTP proxy. Setting it here
            # makes proxy support fully opt-in via the environment, with
            # zero code change required on the caller side.
            self._session = aiohttp.ClientSession(
                timeout=self._timeout, trust_env=True
            )
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
        are surfaced as ``{"ok": false, "error": "...", "error_code"?: int,
        "method": ...}`` rather than raising — agents read the envelope per
        the per-method skill docs. ``error`` stays the short, stable code
        callers branch on (Telegram's ``description``, ``http_<status>``
        for a non-JSON body, ``client_error:<ExceptionName>`` for a
        transport failure); ``error_code`` is Telegram's own (it equals
        the HTTP status; absent for transport failures); ``error_detail``
        carries the diagnostic text that used to be lost — a snippet of
        the non-JSON body, or the transport exception's message.
        """
        url = f"{self._base_url}/{method}"
        try:
            session = await self._ensure_session()
            async with session.post(url, json=args) as resp:
                try:
                    data = await resp.json()
                except (aiohttp.ContentTypeError, ValueError):
                    # Proxies echo the request URL (token in its path) in
                    # their error pages: hand _failure the RAW body, it
                    # redacts before cutting to 160.
                    return self._failure(
                        method,
                        max_len=160,
                        error=f"http_{resp.status}",
                        error_code=resp.status,
                        error_detail=await resp.text(),
                    )
                if not data.get("ok"):
                    raw_code = data.get("error_code")
                    return self._failure(
                        method,
                        error=data.get("description", f"http_{resp.status}"),
                        error_code=raw_code if isinstance(raw_code, int) else resp.status,
                    )
                return data
        except aiohttp.ClientError as e:
            return self._failure(method, error=f"client_error:{type(e).__name__}", error_detail=str(e))
        except Exception as e:  # pragma: no cover — defensive
            # Never an aiohttp error (all caught above), so the traceback
            # cannot quote the request URL; the message is redacted anyway.
            logger.exception(f"[telegram] api_call({method}) unexpected error: {self._redact(str(e))}")
            return self._failure(method, error=f"client_exception:{type(e).__name__}")

    # ------------------------------------------------------------------
    # Hot-path wrappers (raise on failure for caller-side ergonomics)
    # ------------------------------------------------------------------

    async def get_me(self) -> dict[str, Any]:
        """Validate token + return bot identity (id, username, first_name)."""
        resp = await self.api_call("getMe", {})
        if not resp.get("ok"):
            raise TelegramSDKError.from_envelope(resp, "getMe failed")
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
            raise TelegramSDKError.from_envelope(resp, "sendMessage failed")
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
            raise TelegramSDKError.from_envelope(resp, "getUpdates failed")
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

    async def set_message_reaction(
        self, chat_id: str | int, message_id: str | int, emoji: str
    ) -> bool:
        """setMessageReaction — set the bot's reaction on a message.

        Backs the agent-facing ``react_to_user_message`` tool. ``emoji`` must be
        one of Telegram's allowed reaction emojis (👍 ❤️ 🔥 👀 🎉 💯 😱 …);
        anything else gets a 400 which we suppress. Best-effort — returns False
        rather than raising so a failed reaction never breaks the agent turn.
        """
        try:
            mid = int(message_id)
        except (TypeError, ValueError):
            return False
        resp = await self.api_call(
            "setMessageReaction",
            {
                "chat_id": chat_id,
                "message_id": mid,
                "reaction": [{"type": "emoji", "emoji": emoji}],
            },
        )
        return bool(resp.get("ok"))

    async def get_chat(self, chat_id_or_handle: str | int) -> dict[str, Any]:
        """getChat — also resolves @username → numeric user_id (for the
        owner-trust signal at bind time)."""
        resp = await self.api_call("getChat", {"chat_id": chat_id_or_handle})
        if not resp.get("ok"):
            raise TelegramSDKError.from_envelope(resp, "getChat failed")
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

    # ------------------------------------------------------------------
    # File download (Phase 1a — attachment ingestion)
    # ------------------------------------------------------------------

    async def download_file(
        self, file_id: str, *, size_hint: Optional[int] = None
    ) -> tuple[bytes, str]:
        """Two-step Telegram bot download.

        Step 1 — ``getFile(file_id)`` returns metadata including
        ``file_path``. Step 2 — HTTP GET against
        ``https://api.telegram.org/file/bot{TOKEN}/{file_path}`` returns
        the raw bytes.

        ``size_hint`` (the ``file_size`` from the incoming Update) gates a
        pre-check against ``TELEGRAM_BOT_DOWNLOAD_CAP_BYTES``. Without the
        gate, oversized files only fail at the getFile step with a generic
        ``file is too big`` upstream error — slower and harder to surface
        to the user.

        Returns ``(raw_bytes, file_path)``.

        Raises ``TelegramSDKError`` on any failure (oversized refusal,
        getFile non-ok, HTTP non-2xx). Callers catch this in
        ``TelegramTrigger.fetch_attachments`` and audit via
        ``EVENT_ATTACHMENT_FETCH_FAILED`` / ``EVENT_INGRESS_DROPPED_OVERSIZED``.
        """
        if size_hint and size_hint > TELEGRAM_BOT_DOWNLOAD_CAP_BYTES:
            raise TelegramSDKError(
                "oversized",
                f"file exceeds Telegram bot download cap "
                f"({size_hint} > {TELEGRAM_BOT_DOWNLOAD_CAP_BYTES})",
            )

        info = await self.api_call("getFile", {"file_id": file_id})
        if not info.get("ok"):
            raise TelegramSDKError.from_envelope(info, "getFile failed")
        file_path = (info.get("result") or {}).get("file_path", "")
        if not file_path:
            raise TelegramSDKError(
                "no_file_path", "Telegram returned no file_path"
            )

        # Build the URL using _FILE_BASE — NOT self._base_url which points at
        # the JSON API. The token must be embedded in the URL path here;
        # no Authorization header is used. We deliberately do NOT log this
        # URL — it leaks the bot token. The caller logs file_id only.
        url = f"{_FILE_BASE}{self._bot_token}/{file_path}"
        session = await self._ensure_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise TelegramSDKError(
                        f"http_{resp.status}",
                        f"binary fetch failed with status {resp.status}",
                    )
                data = await resp.read()
        except aiohttp.ClientError as e:
            raise TelegramSDKError(
                f"client_error:{type(e).__name__}",
                "binary fetch network error",
                description=f"client_error:{type(e).__name__}: {self._redact(str(e))}",
            ) from None
        return data, file_path
