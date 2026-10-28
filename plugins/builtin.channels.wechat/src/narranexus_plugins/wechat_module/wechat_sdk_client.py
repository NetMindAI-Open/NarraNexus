"""
@file_name: wechat_sdk_client.py
@author:
@date: 2026-06-24
@description: Low-level client for the WeChat iLink ("ClawBot") gateway —
personal-account WeChat over a long-poll PULL protocol.

iLink is PULL-only (unlike Telegram's webhook): the trigger holds a long-poll
``getupdates`` open (the server hangs ~35s), turns each inbound text into a
message, and replies via ``sendmessage``. Bind is a QR-scan flow:
``get_bot_qrcode`` → poll ``get_qrcode_status`` until the owner scans → the
gateway returns a ``bot_token`` (+ optional per-account ``baseurl``).

This module wraps the raw HTTP so the trigger / MCP-send-tool / bind-route share
one source of truth for the gateway's quirks (verified live 2026-06-23 against
the gateway, mirrored from the reference implementation):
  - Auth headers: ``AuthorizationType: ilink_bot_token`` + a per-request
    ``X-WECHAT-UIN`` (base64 of a random uint32) + ``Authorization: Bearer``.
  - App-level failures come back as a non-zero ``errcode`` on an HTTP 200 —
    callers MUST check ``errcode`` (raise_for_status can't see them). Verified
    live 2026-07-16: getupdates responses carry NO ``ret`` field at all
    (healthy: ``{msgs, sync_buf, get_updates_buf}``; error: ``{errcode, errmsg}``).
  - The response Content-Type is ``application/octet-stream`` but the body is
    JSON — ``r.json()`` still parses it.
  - ``get_qrcode_status`` is itself a long-poll (holds until the scan state
    changes); a ReadTimeout means "still waiting", not an error.

No NarraNexus deps — httpx only — so it is unit-testable in isolation.
"""
from __future__ import annotations

import asyncio
import base64
import os
import random
import uuid
from typing import Any, Optional

import httpx
from loguru import logger


class WeChatSDKError(RuntimeError):
    """An iLink app-level failure — HTTP 200 with a non-zero ``errcode`` in the
    JSON body (the getupdates/sendmessage error field; responses carry no
    ``ret``).

    Carries the numeric error ``code`` and the call ``source`` so the trigger
    can branch without parsing strings:
      - ``"updates"`` — a getupdates error = session expired / bad token, a
        PERMANENT auth failure that must disable the credential.
      - ``"send"`` — a per-message send failure (stale context_token) that must
        NOT take the whole account down.
      - ``"stall"`` — a wedged long-poll; TRANSIENT, so the base reconnects
        rather than disabling.
    Subclasses ``RuntimeError`` so existing ``str(exc)`` callers keep working.
    """

    def __init__(self, code: int, source: str, message: str = ""):
        super().__init__(message or f"iLink {source} failed (code={code})")
        self.code = code
        self.source = source

# Default iLink host; a bind may return a per-account base_url that overrides it.
ILINK_HOST = os.environ.get("WECHAT_ILINK_HOST", "https://ilinkai.weixin.qq.com")
CHANNEL_VERSION = os.environ.get("WECHAT_ILINK_VERSION", "1.0.2")

# The server holds getupdates ~35s; give the read generous headroom over that.
POLL_READ_TIMEOUT = 50.0
# Split long replies — WeChat rejects very long single messages.
MSG_CHUNK = 2000


def ilink_headers(bot_token: str = "") -> dict:
    """Build the per-request iLink auth headers.

    X-WECHAT-UIN is regenerated every request (base64 of a random uint32).
    The QR-fetch call is unauthenticated; everything after bind carries the
    Bearer token.
    """
    uin = base64.b64encode(str(random.randint(0, 2**32 - 1)).encode()).decode()
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": uin,
    }
    if bot_token:
        headers["Authorization"] = f"Bearer {bot_token}"
    return headers


def extract_text(msg: dict) -> str:
    """Concatenate text_item.text across an inbound message's item_list."""
    parts = []
    for item in msg.get("item_list") or []:
        text = (item.get("text_item") or {}).get("text")
        if text:
            parts.append(text)
    return "".join(parts)


# NOTE (2026-07-03): a sanitize_bmp() emoji strip briefly lived here — the
# "non-BMP chars kill delivery" theory was a coincidental correlation. The
# real drop rule was the missing per-message client_id (see send_message);
# with it present, astral-plane emoji deliver and render fine (probe P11).


# ---------------------------------------------------------------------------
# Bind flow (QR scan) — used by the route, NOT the long-poll worker.
# ---------------------------------------------------------------------------

async def fetch_qrcode(base_url: str = "", *, bot_type: str = "3") -> dict:
    """Begin a bind: get a login QR. Returns ``{qrcode, qr_url}``. No auth.

    ``qr_url`` (the API's ``qrcode_img_content``) is a WeChat URL that renders a
    scannable QR — NOT a base64 PNG.
    """
    base = base_url or ILINK_HOST
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{base}/ilink/bot/get_bot_qrcode",
            params={"bot_type": bot_type},
            headers=ilink_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
    return {"qrcode": data.get("qrcode", ""), "qr_url": data.get("qrcode_img_content", "")}


async def poll_qrcode_status(qrcode: str, base_url: str = "", timeout: float = 30.0) -> dict:
    """Check a pending bind. This endpoint LONG-POLLS — it holds the connection
    until the scan state changes or ``timeout`` elapses.

    Pre-scan status is ``"wait"``; success returns
    ``{status:"confirmed", bot_token, baseurl}``. On a ReadTimeout (long-poll
    expired with no change) we return ``{status:"wait"}`` so callers re-invoke.
    """
    base = base_url or ILINK_HOST
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15)) as client:
            resp = await client.get(
                f"{base}/ilink/bot/get_qrcode_status",
                params={"qrcode": qrcode},
                headers=ilink_headers(),
            )
            resp.raise_for_status()
            return resp.json()
    except (httpx.ReadTimeout, httpx.TimeoutException):
        return {"status": "wait"}


# ---------------------------------------------------------------------------
# Runtime client — one per bound account; long-poll in, sendmessage out.
# ---------------------------------------------------------------------------

class WeChatSDKClient:
    """Thin iLink HTTP client bound to one account's ``bot_token`` + base URL.

    Holds an httpx client whose default read timeout covers the long-poll
    ``get_updates``; ``send_message`` overrides the timeout per request.
    """

    def __init__(self, bot_token: str, base_url: str = "") -> None:
        self._bot_token = bot_token
        self._base = base_url or ILINK_HOST
        self._client: Optional[httpx.AsyncClient] = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(POLL_READ_TIMEOUT, connect=15)
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_updates(self, cursor: str) -> dict:
        """Long-poll one batch. Returns the raw payload
        ``{msgs: [...], sync_buf, get_updates_buf (next cursor)}``.

        Raises ``WeChatSDKError(source="updates")`` on an app-level failure — a
        non-zero ``errcode`` (e.g. ``{"errcode":-14,"errmsg":"session
        timeout"}``), the way iLink reports an expired session / bad token. The
        trigger classifies that as a permanent auth failure
        (``is_permanent_auth_failure``) and the base disables the credential
        instead of reconnecting against a dead session forever.
        """
        client = self._ensure_client()
        resp = await client.post(
            f"{self._base}/ilink/bot/getupdates",
            json={
                "get_updates_buf": cursor,
                "base_info": {"channel_version": CHANNEL_VERSION},
            },
            headers=ilink_headers(self._bot_token),
        )
        resp.raise_for_status()
        data = resp.json()
        # getupdates responses carry NO ``ret`` field — verified live
        # (2026-07-16): healthy = {msgs, sync_buf, get_updates_buf}, error =
        # {"errcode":-14,"errmsg":"session timeout"}. The old ``ret`` check
        # therefore NEVER fired (``ret`` always absent → 0), so a dead session
        # slipped through as an empty poll, indistinguishable from an idle
        # account — the "silent death" incident (2026-07-06, dev+prod). Read
        # ``errcode`` (keep ``ret`` as belt-and-suspenders); non-zero → raise so
        # the trigger disables the credential instead of polling a corpse.
        code = data.get("errcode", 0) or data.get("ret", 0)
        if code != 0:
            raise WeChatSDKError(code, "updates", data.get("errmsg", ""))
        return data

    async def send_message(self, to_user_id: str, context_token: str, text: str) -> bool:
        """Send a text reply, chunked. Returns True iff every chunk delivered.

        One retry per chunk: the inbound was already consumed (cursor advanced),
        so a transient send failure would otherwise read as "read, no reply".
        A non-zero ``errcode`` on HTTP 200 = app-level send failure (stale context_token /
        session expiry) → treated as a failed send. A chunk that fails both
        attempts aborts the whole send: continuing would deliver a truncated /
        out-of-order reply while still returning ``ok=False``.
        """
        client = self._ensure_client()
        ok = True
        for start in range(0, len(text), MSG_CHUNK):
            chunk = text[start:start + MSG_CHUNK]
            body = {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": to_user_id,
                    # Server-side dedup key. MUST be unique per message: with it
                    # missing, every send shares the same empty key, so the
                    # first message of a login session delivers and every later
                    # one is silently dropped as a duplicate (HTTP 200, empty
                    # body — no error surface). 2026-07-03 incident, reproduced
                    # across two QR sessions. Stable across the retry below on
                    # purpose: a retry IS the same message.
                    "client_id": uuid.uuid4().hex,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": context_token,
                    "item_list": [{"type": 1, "text_item": {"text": chunk}}],
                },
                "base_info": {"channel_version": CHANNEL_VERSION},
            }
            for attempt in range(2):
                try:
                    resp = await client.post(
                        f"{self._base}/ilink/bot/sendmessage",
                        json=body,
                        headers=ilink_headers(self._bot_token),
                        timeout=20,
                    )
                    resp.raise_for_status()
                    rj = resp.json() or {}
                    code = rj.get("errcode", 0) or rj.get("ret", 0)
                    if code != 0:
                        raise WeChatSDKError(code, "send", rj.get("errmsg", ""))
                    break
                except Exception as e:  # noqa: BLE001
                    # Audit trail, not noise-hiding: chunk sends used to fail
                    # with zero trace (lesson #3) — every attempt is logged.
                    logger.warning(
                        f"[wechat send] chunk at {start} attempt {attempt + 1}/2 "
                        f"failed: {type(e).__name__}: {e}"
                    )
                    if attempt == 0:
                        await asyncio.sleep(1.0)
                        continue
                    ok = False
            if not ok:
                # Don't send later chunks past a failed one — that would deliver
                # a gap (truncated / out-of-order) under an already-False result.
                break
        return ok


async def send_text_once(bot_token: str, base_url: str, to_user_id: str,
                         context_token: str, text: str) -> bool:
    """One-shot send for the MCP tool path (no long-lived client)."""
    client = WeChatSDKClient(bot_token, base_url)
    try:
        return await client.send_message(to_user_id, context_token, text)
    finally:
        await client.aclose()
