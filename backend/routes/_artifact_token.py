"""
@file_name: _artifact_token.py
@author: Bin Liang
@date: 2026-05-14
@description: HMAC-signed view tokens for the public artifact raw-content route.

Why this exists
---------------
Multi-file HTML artifacts must be loaded into an <iframe> via a real `src` URL
(not a `blob:` URL) so the entry document's relative references (./style.css,
./data.json) resolve. But a native iframe `src` load cannot attach an
Authorization header, so cloud-mode JWT auth can't gate it.

Instead: the JWT-authed `view-token` endpoint mints a short-TTL HMAC token; the
public raw route (`/api/public/artifacts/raw/{token}/{path}`) validates the
token and serves the file. The token IS the auth.

The token sits in the URL *path*, not the query string, so the entry document's
relative sub-resource requests keep the token prefix automatically.

Token format (same scheme as transcription/url_signer.py):
    base64url(payload_json) + "." + base64url(hmac_sha256_digest)
payload_json = { "agent_id": str, "artifact_id": str, "exp": int }
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Final

from loguru import logger

from xyz_agent_context.settings import settings


# Artifact tabs can sit open for a while; sub-resources may load lazily long
# after the iframe first rendered. 2h is generous — an expired token just means
# the user re-opens the tab (the frontend re-mints transparently).
DEFAULT_TTL_SECONDS: Final[int] = 2 * 60 * 60


@dataclass(frozen=True)
class ArtifactClaims:
    """Decoded payload after a successful :func:`verify` call."""
    agent_id: str
    artifact_id: str
    exp: int


class TokenError(Exception):
    """Base for verify-time token errors. Subclasses carry the HTTP status the
    public route should map to."""
    http_status: int = 401


class TokenExpired(TokenError):
    http_status = 410


class TokenInvalid(TokenError):
    http_status = 401


# Deterministic fallback secret for LOCAL mode only. In local mode the backend
# binds to loopback (see backend/main.py::_assert_local_bind_is_loopback), the
# view token is short-TTL, and the OS user is the security boundary — the same
# posture that lets local mode bypass JWT entirely. A stable derived value (vs
# a random per-process one) means tokens minted before a restart still verify
# after it, so an open artifact tab keeps working across `run.sh` restarts.
# Cloud mode never reaches this — it requires an explicit secret.
_LOCAL_FALLBACK_SECRET: Final[bytes] = hashlib.sha256(
    b"narranexus-local-artifact-view-token-v1"
).digest()


def _secret() -> bytes:
    """Resolve the signing secret.

    Reuses the deployment-wide signing secret: `transcription_hmac_secret` if
    set, else `admin_secret_key`. In cloud mode with neither set, raises —
    refusing to derive a secret in production is the right posture. In local
    mode with neither set, derives a stable local secret instead of raising, so
    artifacts render out-of-the-box on a fresh dev/desktop install.
    """
    explicit = (settings.transcription_hmac_secret or "").strip()
    if explicit:
        return explicit.encode("utf-8")

    from xyz_agent_context.utils.deployment_mode import is_cloud_mode

    if is_cloud_mode():
        raise RuntimeError(
            "TRANSCRIPTION_HMAC_SECRET is required in cloud mode but is unset. "
            "Set it explicitly; we refuse to derive a signing secret in production."
        )

    fallback = (settings.admin_secret_key or "").strip()
    if fallback:
        return fallback.encode("utf-8")
    # Local mode, nothing configured: stable derived secret (see note above).
    return _LOCAL_FALLBACK_SECRET


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def mint(*, agent_id: str, artifact_id: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Build an HMAC-signed view token for the public artifact raw route."""
    payload = {
        "agent_id": agent_id,
        "artifact_id": artifact_id,
        "exp": int(time.time()) + int(ttl_seconds),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    digest = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(digest)}"


def verify(token: str) -> ArtifactClaims:
    """Decode + verify a token.

    Raises :class:`TokenExpired` (→ 410) when the signature is valid but past
    its `exp`, :class:`TokenInvalid` (→ 401) for everything else.
    """
    try:
        payload_b64, digest_b64 = token.split(".", 1)
    except ValueError:
        raise TokenInvalid("token missing '.' separator") from None

    try:
        payload_bytes = _b64url_decode(payload_b64)
        provided_digest = _b64url_decode(digest_b64)
    except Exception as e:  # noqa: BLE001 — base64 errors
        raise TokenInvalid(f"token base64 decode failed: {e}") from None

    expected_digest = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_digest, provided_digest):
        logger.warning("artifact view token signature mismatch")
        raise TokenInvalid("signature mismatch")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise TokenInvalid(f"payload not JSON: {e}") from None

    required = {"agent_id", "artifact_id", "exp"}
    missing = required - set(payload)
    if missing:
        raise TokenInvalid(f"payload missing fields: {sorted(missing)}")
    if not isinstance(payload["exp"], int):
        raise TokenInvalid("exp is not an int")
    if payload["exp"] < int(time.time()):
        raise TokenExpired("token expired")

    return ArtifactClaims(
        agent_id=payload["agent_id"],
        artifact_id=payload["artifact_id"],
        exp=payload["exp"],
    )
