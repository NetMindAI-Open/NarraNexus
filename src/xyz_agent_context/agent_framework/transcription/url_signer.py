"""
@file_name: url_signer.py
@author: Bin Liang
@date: 2026-05-07
@description: HMAC-signed URLs for the public transcription audio route

NetMind's STT worker fetches audio from a publicly-reachable URL — there
is no JWT bypass we can teach NetMind to use. Instead, we mint short-TTL
signed URLs that the public route validates without auth_middleware.

Token format:

    base64url( payload_json ) + "." + base64url( hmac_sha256_digest )

where payload_json contains:

    { "file_id":  str,
      "agent_id": str,
      "user_id":  str,
      "variant":  "original" | "mp3",
      "exp":      int (unix seconds) }

and the digest is HMAC-SHA256 of ``payload_json`` (the canonical bytes,
not the base64url form) under :func:`_secret`.

The signing/verification surface is intentionally tiny. Tokens are
opaque to callers; both sides use ``mint()`` and ``verify()``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Final, Literal, Optional

from loguru import logger

from xyz_agent_context.settings import settings


DEFAULT_TTL_SECONDS: Final[int] = 600  # 10 minutes — see design doc §6.2

Variant = Literal["original", "mp3"]


@dataclass(frozen=True)
class SignedClaims:
    """Decoded payload after a successful :func:`verify` call."""
    file_id: str
    agent_id: str
    user_id: str
    variant: Variant
    exp: int


class TokenError(Exception):
    """Base for verify-time token errors. Subclasses carry the HTTP status
    the public route should map to so the route handler doesn't have to
    branch on the exception type itself."""
    http_status: int = 401


class TokenExpired(TokenError):
    http_status = 410


class TokenInvalid(TokenError):
    http_status = 401


def _secret() -> bytes:
    """Resolve the signing secret.

    Cloud mode: ``settings.transcription_hmac_secret`` MUST be set
    explicitly — we refuse to derive a secret in production.

    Local mode: fall back to ``settings.admin_secret_key`` so dev works
    without configuration. If both are empty in cloud mode the caller
    will hit :class:`RuntimeError` here, which short-circuits the
    NetMind backend in the resolver — the right outcome, since signed
    URLs you can't validate aren't worth minting.
    """
    explicit = (settings.transcription_hmac_secret or "").strip()
    if explicit:
        return explicit.encode("utf-8")

    # Fall back to admin_secret_key. Imports kept local to avoid loading
    # deployment_mode at import time (settings is loaded very early).
    from xyz_agent_context.utils.deployment_mode import is_cloud_mode

    if is_cloud_mode():
        raise RuntimeError(
            "TRANSCRIPTION_HMAC_SECRET is required in cloud mode but is unset. "
            "Set it explicitly; we refuse to derive a signing secret in production."
        )

    fallback = (settings.admin_secret_key or "").strip()
    if not fallback:
        raise RuntimeError(
            "Cannot mint signed transcription URLs — "
            "neither TRANSCRIPTION_HMAC_SECRET nor admin_secret_key is set."
        )
    return fallback.encode("utf-8")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    # Re-pad to a multiple of 4 — base64.urlsafe_b64decode requires it
    # but mint() strips '=' for cleaner URLs.
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def mint(
    *,
    file_id: str,
    agent_id: str,
    user_id: str,
    variant: Variant,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """Build a single-use, HMAC-signed token for the public audio route.

    Raises ``RuntimeError`` only when the signing secret cannot be
    resolved — that's a configuration failure, not a per-request
    failure, and surfacing it loudly is the right behavior. Callers
    in NetMind backend that want never-raise semantics catch this.
    """
    payload = {
        "file_id": file_id,
        "agent_id": agent_id,
        "user_id": user_id,
        "variant": variant,
        "exp": int(time.time()) + int(ttl_seconds),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    digest = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(digest)}"


def verify(token: str) -> SignedClaims:
    """Decode + verify a token.

    On any error raises a :class:`TokenError` subclass — :class:`TokenExpired`
    when the signature is valid but the timestamp is past, :class:`TokenInvalid`
    for everything else (bad format, bad base64, signature mismatch).
    The public route maps these to HTTP 410 / 401 respectively.
    """
    try:
        payload_b64, digest_b64 = token.split(".", 1)
    except ValueError:
        raise TokenInvalid("token missing '.' separator") from None

    try:
        payload_bytes = _b64url_decode(payload_b64)
        provided_digest = _b64url_decode(digest_b64)
    except Exception as e:  # base64 errors
        raise TokenInvalid(f"token base64 decode failed: {e}") from None

    expected_digest = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_digest, provided_digest):
        # Don't log the token itself — even invalid tokens may carry
        # what looks like a valid file_id from a probing client.
        logger.warning("transcription token signature mismatch")
        raise TokenInvalid("signature mismatch")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as e:
        raise TokenInvalid(f"payload not JSON: {e}") from None

    required = {"file_id", "agent_id", "user_id", "variant", "exp"}
    missing = required - set(payload)
    if missing:
        raise TokenInvalid(f"payload missing fields: {sorted(missing)}")
    if payload["variant"] not in ("original", "mp3"):
        raise TokenInvalid(f"unknown variant: {payload['variant']!r}")
    if not isinstance(payload["exp"], int):
        raise TokenInvalid("exp is not an int")

    if payload["exp"] < int(time.time()):
        raise TokenExpired("token expired")

    return SignedClaims(
        file_id=payload["file_id"],
        agent_id=payload["agent_id"],
        user_id=payload["user_id"],
        variant=payload["variant"],
        exp=payload["exp"],
    )


def public_url_for(token: str) -> Optional[str]:
    """Compose the externally-reachable URL for ``token``.

    Returns ``None`` if ``settings.public_base_url`` is unset — caller
    treats that as "this deployment can't host signed URLs, the NetMind
    backend is unavailable here".
    """
    base = (settings.public_base_url or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/api/public/transcription/audio/{token}"
