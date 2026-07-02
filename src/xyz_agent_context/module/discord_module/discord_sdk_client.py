"""
@file_name: discord_sdk_client.py
@date: 2026-06-16
@description: Thin async wrapper around the Discord REST API.

Encapsulates the runtime REST calls Discord channel code needs:
- get_bot_user (validate token + discover bot identity)
- send_message / create_reply (POST a message, splitting at the 2000-char cap)
- get_channel_messages (build conversation history context)
- get_user (resolve a display name)
- download_url (fetch an attachment from Discord's CDN)

Why REST-over-aiohttp instead of discord.py here: discord.py is a
Gateway-first library — its high-level helpers assume a live WebSocket
session. The send path, bind-time auth check, and context-history fetch
all run WITHOUT a gateway connection (``send_to_agent`` from another
module, a bind REST route, the per-turn context builder). Opening a full
gateway just to POST one message is wasteful, so those paths use the
REST API directly. discord.py is used ONLY by ``DiscordTrigger`` for the
inbound Gateway stream. This mirrors how the Telegram module rolls its
own httpx client rather than dragging in python-telegram-bot.

This is the ONLY Discord-channel file that talks to the REST API.
"""
from __future__ import annotations

from typing import Any, Optional

import aiohttp
from loguru import logger

from ._discord_text_sanitizer import split_discord_message

# Discord REST base. v10 is the current stable API version.
_API_BASE = "https://discord.com/api/v10"


class DiscordSDKError(RuntimeError):
    """Raised when the Discord REST API surfaces an error.

    Carries a stable ``code`` string so callers can branch without
    parsing messages:
      - ``unauthorized`` — token invalid / revoked (HTTP 401)
      - ``forbidden``    — bot lacks permission / not in channel (HTTP 403)
      - ``not_found``    — channel / user does not exist (HTTP 404)
      - ``rate_limited`` — HTTP 429
      - ``http_<status>``— any other non-2xx
      - ``oversized``    — download exceeded the byte cap
      - ``client_error:<Type>`` / ``client_exception:<Type>`` — transport
    """

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


# Discord error codes that mean "this token is permanently dead". A missed
# code is benign (the trigger just keeps retrying); an over-broad match would
# disable a healthy credential on a transient blip, so keep this narrow.
PERMANENT_AUTH_CODES = frozenset({"unauthorized"})


def _status_to_code(status: int) -> str:
    if status == 401:
        return "unauthorized"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    return f"http_{status}"


class DiscordSDKClient:
    """Async Discord REST client. One instance per credential / call site."""

    def __init__(self, bot_token: str):
        if not bot_token:
            raise ValueError("bot_token must be provided")
        self._bot_token = bot_token
        self._auth_headers = {
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",
            # Discord requires a descriptive User-Agent on REST calls.
            "User-Agent": "NarraNexus (https://agent.narra.nexus, 1.0)",
        }

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        timeout_seconds: float = 30.0,
    ) -> Any:
        """Issue one REST call. Raises ``DiscordSDKError`` on non-2xx.

        ``trust_env=True`` honours HTTPS_PROXY / NO_PROXY the same way the
        Slack download path does — matters for CN devs reaching
        ``discord.com`` through a local relay.
        """
        url = f"{_API_BASE}{path}"
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        try:
            async with aiohttp.ClientSession(
                timeout=timeout, trust_env=True
            ) as session:
                async with session.request(
                    method,
                    url,
                    headers=self._auth_headers,
                    json=json_body,
                    params=params,
                ) as resp:
                    if 200 <= resp.status < 300:
                        if resp.status == 204:
                            return None
                        return await resp.json()
                    code = _status_to_code(resp.status)
                    detail = ""
                    try:
                        body = await resp.json()
                        detail = body.get("message", "") if isinstance(body, dict) else ""
                    except Exception:  # noqa: BLE001 — error body not JSON
                        detail = ""
                    raise DiscordSDKError(
                        code, f"{method} {path} failed: HTTP {resp.status} {detail}".strip()
                    )
        except aiohttp.ClientError as e:
            raise DiscordSDKError(
                f"client_error:{type(e).__name__}", f"network error: {e}"
            ) from e

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_bot_user(self) -> dict[str, Any]:
        """GET /users/@me — validate token + return bot identity.

        Returns the user object (``id``, ``username``, ``global_name`` …).
        Raises ``DiscordSDKError("unauthorized")`` if the token is bad.
        """
        return await self._request("GET", "/users/@me")

    async def send_message(self, channel_id: str, text: str) -> dict[str, Any]:
        """POST one or more messages to a channel.

        ``text`` longer than 2000 chars is split into multiple messages
        (Discord rejects the whole body otherwise). Returns the LAST
        message object posted (carries ``id`` for threading / logging).
        """
        last: dict[str, Any] = {}
        for chunk in split_discord_message(text):
            # Skip whitespace-only chunks — Discord renders them as a
            # blank message (and rejects a truly-empty body). This is the
            # last-line choke point so no send path can post a blank.
            if not chunk.strip():
                continue
            last = await self._request(
                "POST",
                f"/channels/{channel_id}/messages",
                json_body={"content": chunk},
            )
        return last

    async def create_reply(
        self, channel_id: str, message_id: str, text: str
    ) -> dict[str, Any]:
        """POST a reply that references ``message_id`` (Discord inline reply).

        Only the FIRST chunk carries the ``message_reference`` so the
        reply arrow points at the original message; continuation chunks
        post as plain follow-ups. ``fail_if_not_exists=False`` degrades a
        reply-to-deleted-message into a normal message instead of erroring.
        """
        chunks = [c for c in split_discord_message(text) if c.strip()]
        last: dict[str, Any] = {}
        for i, chunk in enumerate(chunks):
            body: dict[str, Any] = {"content": chunk}
            if i == 0:
                body["message_reference"] = {
                    "message_id": message_id,
                    "channel_id": channel_id,
                    "fail_if_not_exists": False,
                }
            last = await self._request(
                "POST", f"/channels/{channel_id}/messages", json_body=body
            )
        return last

    async def get_channel_messages(
        self, channel_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """GET /channels/{id}/messages — recent messages, newest-first.

        Caller usually reverses for chronological order. Returns ``[]`` on
        any error (history is best-effort context, never fatal).
        """
        try:
            data = await self._request(
                "GET",
                f"/channels/{channel_id}/messages",
                params={"limit": max(1, min(limit, 100))},
            )
            return list(data) if isinstance(data, list) else []
        except DiscordSDKError as e:
            logger.warning(
                f"[discord] get_channel_messages failed: channel={channel_id} {e.code}"
            )
            return []

    async def get_user(self, user_id: str) -> dict[str, Any]:
        """GET /users/{id}. Returns the user object or {} on miss."""
        try:
            return await self._request("GET", f"/users/{user_id}")
        except DiscordSDKError as e:
            logger.warning(f"[discord] get_user failed: user={user_id} {e.code}")
            return {}

    async def create_dm_channel(self, user_id: str) -> str:
        """POST /users/@me/channels — open (or fetch) a 1:1 DM channel.

        Discord requires a DM channel to exist before a bot can message a
        user it hasn't received a message from. This is idempotent —
        repeated calls return the same DM channel. Returns the DM channel
        id. Raises ``DiscordSDKError`` on failure (e.g. the user shares no
        server with the bot, or their privacy settings block DMs).
        """
        data = await self._request(
            "POST", "/users/@me/channels", json_body={"recipient_id": str(user_id)}
        )
        return str(data.get("id", "")) if isinstance(data, dict) else ""

    async def list_guilds(self) -> list[dict[str, Any]]:
        """GET /users/@me/guilds — guilds (servers) the bot is a member of.

        Returns ``[]`` on error. Each entry has at least ``id`` and ``name``.
        """
        try:
            data = await self._request("GET", "/users/@me/guilds")
            return list(data) if isinstance(data, list) else []
        except DiscordSDKError as e:
            logger.warning(f"[discord] list_guilds failed: {e.code}")
            return []

    async def list_guild_channels(self, guild_id: str) -> list[dict[str, Any]]:
        """GET /guilds/{id}/channels — all channels in a guild.

        Returns ``[]`` on error. Caller filters by ``type`` (0 = text,
        5 = announcement) for channels the bot can post to.
        """
        try:
            data = await self._request("GET", f"/guilds/{guild_id}/channels")
            return list(data) if isinstance(data, list) else []
        except DiscordSDKError as e:
            logger.warning(f"[discord] list_guild_channels failed: guild={guild_id} {e.code}")
            return []

    async def download_url(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_seconds: float = 60.0,
    ) -> bytes:
        """Stream-download a Discord CDN attachment with a byte cap.

        Discord ``cdn.discordapp.com`` attachment URLs are public (they
        carry a signed ``ex``/``is``/``hm`` query) — no Authorization
        header is sent. The cap is enforced per 64 KB chunk so a hostile
        / mis-sized file can't OOM the worker. Raises ``DiscordSDKError``
        on HTTP error, network failure, or oversize.
        """
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        try:
            async with aiohttp.ClientSession(
                timeout=timeout, trust_env=True
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise DiscordSDKError(
                            f"http_{resp.status}",
                            f"download failed: HTTP {resp.status}",
                        )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise DiscordSDKError(
                                "oversized",
                                f"file exceeds max_bytes={max_bytes} during stream",
                            )
                        chunks.append(chunk)
                    return b"".join(chunks)
        except aiohttp.ClientError as e:
            raise DiscordSDKError(
                f"client_error:{type(e).__name__}", f"download network error: {e}"
            ) from e
        except DiscordSDKError:
            raise
        except Exception as e:  # noqa: BLE001 — defensive
            logger.exception(f"[discord] download_url unexpected error: {url[:80]}")
            raise DiscordSDKError(
                f"client_exception:{type(e).__name__}", f"download unexpected: {e}"
            ) from e
