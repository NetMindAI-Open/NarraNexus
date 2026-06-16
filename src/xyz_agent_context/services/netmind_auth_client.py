"""
@file_name: netmind_auth_client.py
@author: NetMind.AI
@date: 2026-06-11
@description: NetMind account-system token verification client.

NetMind JWTs cannot be verified offline: the signing secret includes a
per-user `loginToken` that lives in NetMind's database and rotates on
password login. The only reliable way for a third-party service to verify
a token is to call an authenticated NetMind endpoint with it. Following
Arena's integration, we use POST /user/balance and treat a successful
response carrying the user object as proof of validity.

Protocol quirks (verified against Arena's client, netmind.ts):
- The auth header is a custom header literally named `token`, with a
  `Bearer ` prefix. It is NOT the standard Authorization header.
- The response envelope is {"data": {"user": {...}}}; an explicit
  {"success": false} body means the token was rejected.

Error semantics are deliberately two-valued:
- NetmindAuthError      -> the token is bad (caller maps to HTTP 401)
- NetmindUpstreamError  -> NetMind is unreachable / broke contract
                           (caller maps to HTTP 502; NOT the user's fault)

Dev bypass (test environments only): when BOTH the NETMIND_DEV_BYPASS env
switch is on AND the token has the `dev-bypass-` prefix, verification is
skipped and a deterministic synthetic identity is returned. The double
switch mirrors Arena's design so a leaked prefix alone can never bypass
auth in production.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx
from loguru import logger

DEV_BYPASS_ENV = "NETMIND_DEV_BYPASS"
DEV_BYPASS_PREFIX = "dev-bypass-"

DEFAULT_BASE_URL_ENV = "NETMIND_AUTH_API_URL"
DEFAULT_TIMEOUT_ENV = "NETMIND_AUTH_TIMEOUT_SECONDS"
_FALLBACK_TIMEOUT_SECONDS = 5.0


class NetmindAuthError(Exception):
    """The NetMind token is invalid / expired / revoked."""


class NetmindUpstreamError(Exception):
    """NetMind auth service unreachable or returned an unusable response."""


@dataclass
class NetmindUser:
    """Verified identity extracted from NetMind's user object."""

    user_system_code: str
    email: str
    nickname: Optional[str] = None
    avatar_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)


def _dev_bypass_user(token: str) -> NetmindUser:
    email = token[len(DEV_BYPASS_PREFIX):].strip().lower()
    digest = hashlib.sha1(email.encode("utf-8")).hexdigest()[:24]
    return NetmindUser(
        user_system_code=f"devbp_{digest}",
        email=email,
        nickname=email.split("@", 1)[0],
    )


class NetmindAuthClient:
    """Thin async client around NetMind's token-verification call."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get(DEFAULT_BASE_URL_ENV, "")).rstrip("/")
        if timeout_seconds is None:
            timeout_seconds = float(
                os.environ.get(DEFAULT_TIMEOUT_ENV, _FALLBACK_TIMEOUT_SECONDS)
            )
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    async def verify_token(self, token: str) -> NetmindUser:
        """Verify a NetMind loginToken and return the user's identity.

        Raises NetmindAuthError for a rejected token and
        NetmindUpstreamError for transport/contract failures.
        """
        if token.startswith(DEV_BYPASS_PREFIX) and os.environ.get(DEV_BYPASS_ENV) == "1":
            logger.warning("NetMind dev-bypass token accepted (test-only path)")
            return _dev_bypass_user(token)

        if not self.base_url and self._transport is None:
            raise NetmindUpstreamError(
                f"{DEFAULT_BASE_URL_ENV} is not configured"
            )

        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self.timeout_seconds
            ) as http:
                response = await http.post(
                    f"{self.base_url}/user/balance",
                    headers={
                        # NetMind convention: header is named `token`,
                        # not Authorization.
                        "token": f"Bearer {token}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
        except httpx.HTTPError as exc:
            raise NetmindUpstreamError(f"NetMind auth API unreachable: {exc}") from exc

        # Best-effort parse first: NetMind signals a rejected token with
        # {success: false} even on some non-2xx statuses — e.g. a non-NetMind
        # JWT yields a 500 carrying that envelope. A rejected token is the
        # user's problem (401), not an upstream outage (502), so this signal
        # is checked before the status-code fallbacks.
        try:
            body = response.json()
        except ValueError:
            body = None

        if isinstance(body, dict) and body.get("success") is False:
            raise NetmindAuthError("NetMind rejected the token")

        if response.status_code >= 500:
            raise NetmindUpstreamError(
                f"NetMind auth API returned {response.status_code}"
            )
        if response.status_code >= 400:
            raise NetmindAuthError("NetMind rejected the token")

        if not isinstance(body, dict):
            raise NetmindUpstreamError("NetMind auth API returned non-JSON")

        user_obj = (body.get("data") or {}).get("user") or {}
        return self._extract_identity(user_obj)

    @staticmethod
    def _extract_identity(user_obj: Dict[str, Any]) -> NetmindUser:
        email = (user_obj.get("email") or "").strip().lower()
        # Field name confirmed `userSystemCode` against NetMind dev
        # (2026-06-11 live probe); the snake_case fallback is defensive
        # only, in case a future API version renames it.
        user_system_code = (
            user_obj.get("userSystemCode")
            or user_obj.get("user_system_code")
            or ""
        ).strip()

        if not email or not user_system_code:
            # Contract drift (field renamed / missing) must surface as an
            # upstream problem, not as a user auth failure. Never log the
            # user object itself: it carries loginToken and other secrets.
            raise NetmindUpstreamError(
                "NetMind /user/balance response missing identity fields "
                f"(email present: {bool(email)}, "
                f"userSystemCode present: {bool(user_system_code)})"
            )

        sanitized_raw = {
            k: v
            for k, v in user_obj.items()
            if k not in ("loginToken", "nettyToken", "accessToken", "xyzToken")
        }
        return NetmindUser(
            user_system_code=user_system_code,
            email=email,
            nickname=user_obj.get("nickName") or user_obj.get("username"),
            avatar_url=user_obj.get("userHeadImage"),
            raw=sanitized_raw,
        )
