"""
@file_name: netmind_key_client.py
@author: NetMind.AI
@date: 2026-07-02
@description: NetMind Key-management API client (generate/list inference keys).

Module F (Phase 5) needs to mint a NetMind inference API key on the user's
behalf so "use this subscription" can wire it into the agent/helper slots
without the user pasting anything.

This is a SEPARATE surface from netmind_billing_client:
- Different host: platform-api.netmind.ai (prod) / mind-web.protago-dev.com (dev).
- Different auth header: ``token: Bearer <jwt>`` (NOT ``loginToken``). Same JWT
  though — verified on dev 2026-07-02 (the login JWT works on both domains,
  only the header name differs).
- Form-encoded body (``application/x-www-form-urlencoded``).
- Envelope quirk: errors come back as HTTP 200 with ``{"success": false,
  "errorcode": "..."}`` — status code alone is NOT reliable, the body must be
  parsed. ``NOT_LOGGEDIN`` = bad/absent token.

addApiToken does not return the key string, so we create-then-list: create a
named key, then queryApitokenList and return the freshest match by name.
Injectable ``transport`` for unit tests (no network), same as the sibling
clients. Never logs the JWT or the generated apitoken.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, NamedTuple, Optional

import httpx
from loguru import logger

DEFAULT_BASE_URL_ENV = "NETMIND_KEY_API_BASE"
DEFAULT_TIMEOUT_ENV = "NETMIND_KEY_API_TIMEOUT_SECONDS"
_FALLBACK_TIMEOUT_SECONDS = 20.0
_KEY_NAME_PREFIX = "NarraNexus"


class MintedKey(NamedTuple):
    """A freshly-minted inference key + its NetMind row id (for revoke)."""

    apitoken: str
    token_id: object  # NetMind numeric id; opaque to us, used only to delete


class KeyAuthError(Exception):
    """The JWT was rejected by the key API (caller -> 401)."""


class KeyUpstreamError(Exception):
    """Key API unreachable / malformed / non-auth failure (caller -> 502)."""


class NetmindKeyClient:
    """Thin async client around NetMind's /inference/* key-management API."""

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

    async def create_key(self, jwt: str, currency: str = "USD") -> MintedKey:
        """Mint an inference key and return it + its row id.

        addApiToken returns no key string, so we create-then-list. We use a
        UNIQUE per-call name (``NarraNexus-<uuid8>``) and query by that name, so
        the match is unambiguous even if the account already has keys named
        "NarraNexus" — never picks a pre-existing/other key. The server-side
        name filter also means the freshest match can't be pushed off a page.
        """
        name = f"{_KEY_NAME_PREFIX}-{uuid.uuid4().hex[:8]}"
        await self._post("/inference/addApiToken", jwt, {"name": name, "currency": currency})
        listing = await self._post(
            "/inference/queryApitokenList", jwt, {"name": name, "page": "1", "size": "50"}
        )
        rows = listing.get("data") if isinstance(listing, dict) else None
        if not isinstance(rows, list) or not rows:
            raise KeyUpstreamError("minted key not found in token list")
        matches = [r for r in rows if isinstance(r, dict) and r.get("name") == name]
        if not matches:
            raise KeyUpstreamError("minted key not found by unique name")
        newest = max(matches, key=lambda r: r.get("createTime") or 0)
        row_map = newest.get("map") if isinstance(newest.get("map"), dict) else {}
        token = newest.get("apitoken") or row_map.get("api_token")
        if not isinstance(token, str) or not token:
            raise KeyUpstreamError("token list row missing apitoken")
        return MintedKey(apitoken=token, token_id=newest.get("id"))

    async def delete_key(self, jwt: str, token_id: object) -> None:
        """Best-effort revoke of a minted key (orphan cleanup on failure).

        Never raises — cleanup failure must not mask the original error that
        triggered it. Logs a warning so orphans are still discoverable.
        """
        if token_id is None:
            return
        try:
            await self._post(
                "/inference/deleteApiToken", jwt, {"apiTokenId": str(token_id)}
            )
        except Exception as e:  # noqa: BLE001 — best-effort cleanup
            logger.warning(f"[netmind_key] best-effort delete of orphan key failed: {e}")

    async def _post(self, path: str, jwt: str, form: dict) -> Any:
        """POST a form-encoded key-API call, decoding the 200+envelope contract.

        Never logs jwt/apitoken.
        """
        if not self.base_url and self._transport is None:
            raise KeyUpstreamError(f"{DEFAULT_BASE_URL_ENV} is not configured")
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self.timeout_seconds
            ) as http:
                response = await http.post(
                    f"{self.base_url}{path}",
                    headers={"token": f"Bearer {jwt}"},
                    data=form,  # form-encoded
                )
        except httpx.HTTPError as exc:
            raise KeyUpstreamError(f"NetMind key API unreachable: {exc}") from exc

        if response.status_code >= 500:
            raise KeyUpstreamError(f"NetMind key API returned {response.status_code}")

        try:
            body = response.json()
        except ValueError as exc:
            raise KeyUpstreamError("NetMind key API returned non-JSON") from exc

        # Envelope: success/failed flags carry the real verdict (HTTP is often 200).
        if isinstance(body, dict) and body.get("success") is False:
            errorcode = str(body.get("errorcode") or "")
            if errorcode == "NOT_LOGGEDIN":
                raise KeyAuthError("NetMind key API rejected the token")
            # Non-auth business/failure — surface a short, non-sensitive marker.
            raise KeyUpstreamError(f"NetMind key API failure ({errorcode or 'unknown'})")
        if response.status_code >= 400:
            raise KeyUpstreamError(f"NetMind key API returned {response.status_code}")
        return body
