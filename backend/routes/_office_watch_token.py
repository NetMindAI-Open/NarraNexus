"""
@file_name: _office_watch_token.py
@author: NetMind.AI
@date: 2026-07-13
@description: HMAC-signed view tokens for the public office-watch proxy route.

Same rationale as _artifact_token.py: an <iframe src> navigation (and the
watch page's own EventSource/fetch sub-requests) cannot attach an X-User-Id /
Authorization header, so the session-authed proxy 401s. Instead the authed
`view-token` endpoint mints a short-TTL token carrying the caller's user_id +
the watch port; the public route (`/api/public/office-watch-proxy/{token}/...`)
validates it and forwards. The token IS the auth and sits in the URL path so
the page's relative sub-requests keep the prefix.

Reuses the artifact token's signing secret + codec so there is one signing
posture across the app.

Token payload = { "user_id": str, "port": int, "exp": int }
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Final

from loguru import logger

# Reuse the artifact token's secret resolution + base64url codec + error types
# so office-watch and artifacts share one signing posture.
from backend.routes._artifact_token import (
    TokenExpired,
    TokenInvalid,
    _b64url_decode,
    _b64url_encode,
    _secret,
)

# A live preview tab can sit open a long time; the SSE connection re-uses the
# token on reconnect. 2h matches the artifact token and is comfortably longer
# than officecli watch's own idle-stop, so the token rarely outlives the
# server it points at.
DEFAULT_TTL_SECONDS: Final[int] = 2 * 60 * 60


@dataclass(frozen=True)
class OfficeWatchClaims:
    user_id: str
    port: int
    exp: int


def mint(*, user_id: str, port: int, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Build an HMAC-signed view token for the public office-watch proxy."""
    payload = {"user_id": user_id, "port": int(port), "exp": int(time.time()) + int(ttl_seconds)}
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    digest = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(digest)}"


def verify(token: str) -> OfficeWatchClaims:
    """Decode + verify a token. Raises TokenExpired (410) / TokenInvalid (401)."""
    try:
        payload_b64, digest_b64 = token.split(".", 1)
    except ValueError:
        raise TokenInvalid("token missing '.' separator") from None
    try:
        payload_bytes = _b64url_decode(payload_b64)
        provided_digest = _b64url_decode(digest_b64)
    except Exception as e:  # noqa: BLE001
        raise TokenInvalid(f"token base64 decode failed: {e}") from None

    expected = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, provided_digest):
        logger.warning("office-watch view token signature mismatch")
        raise TokenInvalid("signature mismatch")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise TokenInvalid(f"payload not JSON: {e}") from None

    missing = {"user_id", "port", "exp"} - set(payload)
    if missing:
        raise TokenInvalid(f"payload missing fields: {sorted(missing)}")
    if not isinstance(payload["exp"], int) or not isinstance(payload["port"], int):
        raise TokenInvalid("exp/port not int")
    if payload["exp"] < int(time.time()):
        raise TokenExpired("token expired")

    return OfficeWatchClaims(user_id=payload["user_id"], port=payload["port"], exp=payload["exp"])
