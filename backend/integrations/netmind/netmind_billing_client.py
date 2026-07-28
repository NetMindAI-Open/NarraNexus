"""
@file_name: netmind_billing_client.py
@author: NetMind.AI
@date: 2026-07-02
@description: NetMind billing/subscription API proxy client.

Thin async client the NarraNexus backend uses to call NetMind's
billing+subscription API on behalf of a logged-in user. The user's NetMind
`loginToken` (JWT) is held by the frontend and forwarded per-request — we
NEVER store it. This client only wraps the HTTP call + error mapping so the
routes stay thin.

Mirrors ``netmind_auth_client.NetmindAuthClient`` deliberately:
- injectable ``transport`` so unit tests use ``httpx.MockTransport`` (no net).
- two-valued errors: BillingAuthError (bad/expired token -> caller 401) vs
  BillingUpstreamError (NetMind unreachable / broke contract -> caller 502).

Auth header quirks (verified against dev 2026-07-02 live probe):
- Subscription endpoints (``/v1/power-subscription/*``) authenticate with the
  custom header ``loginToken: Bearer <jwt>`` and return their JSON flat at the
  top level (NOT wrapped in {success, data}).
- Missing/invalid credentials return 401 on power-subscription; the sibling
  finance service returns 403 instead — both mean "auth failed", so BOTH are
  mapped to BillingAuthError.
- ``/v1/power-subscription/plan`` is public (no token).

Scope note (Phase 1): only ``get_plans`` + ``get_subscription`` are needed for
the account/status panel. Balance (user-fee-info), subscribe/cancel, and
recharge land in later phases on the same client.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

import httpx
from loguru import logger

# A JWT / opaque-token shape (a.b.c of url-safe chars). Used to scrub upstream
# business-error messages so a token echoed under an allowed key never reaches
# the client / logs.
_TOKENISH = re.compile(r"[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")

# An id/token-shaped run: 8+ chars of the token alphabet containing at least one
# DIGIT. Opaque ids/keys/session ids/card numbers have digits; natural-language
# words (even "subscription", "auto-renew") do not — so this drops an
# id embedded mid-sentence ("...for session cs_test_a1b2c3d4...") without eating
# legitimate business copy.
_IDLIKE = re.compile(r"(?=[A-Za-z0-9_-]*[0-9])[A-Za-z0-9_-]{8,}")


def _safe_business_message(msg: str) -> str:
    """Return msg only if it looks like a human-readable error, else "".

    Guards against a misbehaving upstream putting a token/id/PII blob under an
    allowed key (message/detail/error): a JWT-shaped substring, an id/token-shaped
    run (8+ chars with a digit) even inside a sentence, or a long whitespace-free
    string (likely an id/token, not a sentence), is dropped.
    """
    if not msg:
        return ""
    if _TOKENISH.search(msg):
        return ""
    if _IDLIKE.search(msg):
        return ""
    if " " not in msg.strip() and len(msg) > 40:
        return ""
    return msg

DEFAULT_BASE_URL_ENV = "BILLING_API_BASE"
DEFAULT_TIMEOUT_ENV = "BILLING_API_TIMEOUT_SECONDS"
_FALLBACK_TIMEOUT_SECONDS = 10.0

class BillingAuthError(Exception):
    """The NetMind loginToken is invalid / expired / rejected (caller -> 401)."""


class BillingUpstreamError(Exception):
    """NetMind billing API unreachable or returned an unusable response (caller -> 502)."""


class BillingBusinessError(Exception):
    """A non-auth 4xx business rejection from NetMind (caller -> 400).

    Carries a short, user-safe message extracted from the upstream body
    (e.g. "Already subscribed to Pro." / "No active Pro subscription.").
    """

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class BillingForbiddenError(Exception):
    """Authenticated but not permitted for THIS resource (caller -> 403).

    Distinct from BillingAuthError (bad token): the token is valid but the
    resource isn't the caller's (e.g. recharge by-session for a session owned
    by another user). Only raised when a caller opts in via
    ``distinguish_forbidden`` — otherwise 403 collapses into BillingAuthError,
    since on most endpoints 403 just means "token rejected".
    """


class BillingNotFoundError(Exception):
    """Resource does not exist (caller -> 404).

    Only raised when a caller opts in via ``distinguish_not_found`` (e.g.
    recharge by-session for an unknown session id); otherwise a 404 falls
    through to BillingBusinessError like any other non-auth 4xx.
    """


class NetmindBillingClient:
    """Thin async client around NetMind's billing+subscription API."""

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

    async def get_plans(self) -> Any:
        """Public plan catalog (Free / Pro). No token required.

        Returns the parsed JSON body (``{"plans": [...]}``).
        """
        return await self._request("GET", "/v1/power-subscription/plan")

    async def get_subscription(self, login_token: str) -> Any:
        """Current plan + subscription status for the token's user.

        Returns the flat JSON body: ``{plan_id, name, quota_limits, features,
        monthly_grant_usd, prices, subscription: null | {...}}``.
        """
        return await self._request(
            "GET",
            "/v1/power-subscription/me",
            login_token=login_token,
        )

    async def get_fee_info(self, login_token: str) -> Any:
        """User balance + eligibility (finance domain).

        Returns ``{success, user_id, eligible, checks, metrics}``. Note the
        finance service signals a rejected token with 403 (vs 401 on
        power-subscription) — both are mapped to BillingAuthError.
        """
        return await self._request(
            "GET", "/v1/finance/user-fee-info", login_token=login_token
        )

    async def get_records(
        self,
        login_token: str,
        direction: Optional[str] = None,
        page_size: int = 20,
    ) -> Any:
        """Financial records / transactions (finance domain).

        Returns ``{success, data: [...], page, page_size, has_next}``. Optional
        ``direction`` filter: ``expense`` (consumption) / ``income``
        (recharge/refund); default returns all for the current month.
        """
        params: dict[str, str] = {"page_size": str(page_size)}
        if direction:
            params["direction"] = direction
        return await self._request(
            "GET", "/v1/finance/records", login_token=login_token, params=params
        )

    async def subscribe(self, login_token: str) -> Any:
        """Start a Pro subscription. Returns ``{session_id, checkout_url}``.

        Raises BillingBusinessError on 400 (e.g. "Already subscribed to Pro.").
        """
        return await self._request(
            "POST", "/v1/power-subscription/subscribe", login_token=login_token
        )

    async def cancel(self, login_token: str) -> Any:
        """Cancel = turn off auto-renew (stays Pro until period end).

        Returns ``{status: "auto_renew_off"}``. Raises BillingBusinessError on
        400 (e.g. "No active Pro subscription.").
        """
        return await self._request(
            "POST", "/v1/power-subscription/cancel", login_token=login_token
        )

    async def reactivate(self, login_token: str) -> Any:
        """Re-enable auto-renew on a cancelled-but-in-period subscription.

        NOTE: endpoint existence confirmed on dev (401 unauth); exact semantics
        (resume auto-renew vs re-subscribe) still pending NetMind confirmation.
        """
        return await self._request(
            "POST", "/v1/power-subscription/reactivate", login_token=login_token
        )

    async def recharge(
        self,
        login_token: str,
        amount: float,
        currency: str = "USD",
    ) -> Any:
        """Create a Stripe HOSTED Checkout for an account top-up (finance 4.2).

        Uses the hosted-checkout endpoint (returns a redirectable
        ``checkout_url``), NOT the embedded-SDK endpoint (which returns a
        ``client_secret``) — hosted matches our openExternal flow (same as
        subscribe). We omit ``success_url``/``cancel_url`` so NetMind uses its
        own result page and we poll by-session regardless; they are intentionally
        NOT forwarded from client input (an unvalidated redirect target into a
        payment session is attack surface with no current use — re-add with an
        allowlist when a concrete web-redirect UX needs it).

        Returns the wrapped body ``{success, data: {recharge_id, session_id,
        checkout_url, status}}``.
        """
        body: dict[str, Any] = {"amount": amount, "currency": currency or "USD"}
        return await self._request(
            "POST",
            "/v1/finance/recharge/stripe/checkout",
            login_token=login_token,
            json_body=body,
        )

    async def recharge_status(self, login_token: str, session_id: str) -> Any:
        """Poll a recharge by its Stripe session id (finance 4.3 by-session).

        Returns the wrapped body ``{success, data: {recharge_id, session_id,
        status, ...}}`` where ``status`` is ``pending``/``succeeded``/``failed``.
        403 (session not owned by caller) -> BillingForbiddenError; 404 (unknown
        session) -> BillingNotFoundError, so the route can pass those through
        instead of collapsing them to 401/400.
        """
        return await self._request(
            "GET",
            f"/v1/finance/recharge/by-session/{session_id}",
            login_token=login_token,
            distinguish_forbidden=True,
            distinguish_not_found=True,
        )

    async def _request(
        self,
        method: str,
        path: str,
        login_token: Optional[str] = None,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
        distinguish_forbidden: bool = False,
        distinguish_not_found: bool = False,
    ) -> Any:
        """Issue one billing call, mapping transport/auth failures to the
        two-valued error contract. Never logs the loginToken."""
        if not self.base_url and self._transport is None:
            raise BillingUpstreamError(f"{DEFAULT_BASE_URL_ENV} is not configured")

        headers: dict[str, str] = {}
        if login_token:
            # NetMind convention: custom header named `loginToken`, Bearer prefix.
            headers["loginToken"] = f"Bearer {login_token}"

        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self.timeout_seconds
            ) as http:
                response = await http.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    json=json_body,
                    params=params,
                )
        except httpx.HTTPError as exc:
            raise BillingUpstreamError(
                f"NetMind billing API unreachable: {exc}"
            ) from exc

        if response.status_code == 401 or (
            response.status_code == 403 and not distinguish_forbidden
        ):
            # 401 always = bad token. 403 collapses into auth-failure too,
            # EXCEPT where the caller opts in to distinguish "not your resource"
            # (recharge by-session) from "token rejected".
            raise BillingAuthError("NetMind rejected the loginToken")
        if response.status_code == 403:
            raise BillingForbiddenError("NetMind: not permitted for this resource")
        if response.status_code == 404 and distinguish_not_found:
            raise BillingNotFoundError("NetMind: resource not found")
        if response.status_code >= 500:
            raise BillingUpstreamError(
                f"NetMind billing API returned {response.status_code}"
            )
        if response.status_code >= 400:
            # 4xx that isn't an auth failure = a business rejection (e.g.
            # "Already subscribed to Pro." / "No active Pro subscription.").
            # Extract ONLY a short user-safe message — never dump the whole
            # upstream body (it may echo the token or payment/PII fields, and
            # this string flows into server logs). Common message keys tried in
            # order; falls back to a generic string.
            msg = ""
            try:
                body = response.json()
                if isinstance(body, dict):
                    for key in ("message", "detail", "error"):
                        val = body.get(key)
                        if isinstance(val, str) and val:
                            msg = val[:200]
                            break
                        if isinstance(val, dict) and isinstance(val.get("message"), str):
                            msg = val["message"][:200]
                            break
            except ValueError:
                pass
            raise BillingBusinessError(
                _safe_business_message(msg)
                or f"Billing request rejected ({response.status_code})",
                response.status_code,
            )

        try:
            return response.json()
        except ValueError as exc:
            raise BillingUpstreamError(
                "NetMind billing API returned non-JSON"
            ) from exc
