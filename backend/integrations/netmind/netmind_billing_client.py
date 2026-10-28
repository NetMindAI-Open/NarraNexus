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


def _redirect_fields(
    success_url: Optional[str], cancel_url: Optional[str]
) -> dict[str, Any]:
    """Only the post-payment redirect fields the caller actually resolved.

    Absent rather than null: upstream types both as ``Optional[str]`` and does
    accept explicit nulls (probed dev+prod 2026-07-30), but "omitted" is the
    body shape that has shipped for months, so an unconfigured deployment keeps
    byte-identical behavior.
    """
    fields: dict[str, Any] = {}
    if success_url:
        fields["success_url"] = success_url
    if cancel_url:
        fields["cancel_url"] = cancel_url
    return fields


# Which currency each payment method is settled in on the nexus Stripe account.
# Upstream VALIDATES this pairing and 400s on a mismatch, so it is derived here
# rather than accepted as a parameter: a caller able to pass both could only
# ever use that freedom to break its own payment. WeChat Pay is enabled for CNY
# only on this account; card and Alipay settle in USD.
#
# Note what does NOT change with currency: `amount` is always the USD figure
# (the credit the user receives / the subscription's value). A WeChat payer is
# charged an equivalent in CNY that upstream computes at its own live rate and
# reports back as `charge_amount` + `fx_rate`; we never compute it ourselves,
# because a rate we made up would disagree with the one they actually charge.
PAYMENT_METHOD_CURRENCY: dict[str, str] = {
    "default": "USD",   # card (+ Alipay on the same hosted page)
    "alipay": "USD",
    "wechat": "CNY",
}


def _channel_field(channel: Optional[str]) -> dict[str, Any]:
    """``{"channel": …}`` only when the caller resolved one.

    Same "omitted, not null" discipline as ``_redirect_fields``: upstream reads
    an absent channel as the original shared "power" account, which is exactly
    the behaviour a caller that passes nothing should keep getting.
    """
    return {"channel": channel} if channel else {}


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

    async def subscribe(
        self,
        login_token: str,
        *,
        payment_method: str = "stripe",
        months: Optional[int] = None,
        channel: Optional[str] = None,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
    ) -> Any:
        """Start a Pro subscription. Returns ``{session_id, checkout_url}``.

        ``success_url``/``cancel_url`` are where Stripe sends the payer once the
        Checkout Session ends. NetMind creates that session, so these two fields
        are our only lever over it; omitted, the payer lands on NetMind's own
        result page (a different domain — the 2026-07-30 P0 report). The caller
        resolves them from deploy config, NEVER from client input; see
        ``routes.billing._return_urls``.

        Two different products share this endpoint, and ``payment_method``
        picks which:

        - ``stripe`` — a real Stripe subscription on a card. Renews itself, can
          be cancelled/reactivated. ``months`` does not apply and is omitted:
          "a card subscription for 6 months" is not a state upstream has.
        - ``alipay`` / ``wechat`` — Stripe subscriptions cannot be paid with
          either, so this is a ONE-TIME purchase of ``months`` months, granted
          monthly and then simply ending. Nothing renews it; buying again while
          one is live EXTENDS the period. WeChat settles in CNY at upstream's
          live rate (same rate quoted by ``fx_rate``).

        The two modes are mutually exclusive per user and upstream enforces it:
        a card subscriber gets 400 ``already_subscribed_card`` here, and a
        one-time holder gets 400 "Already subscribed to Pro." from the card
        path. Switching modes requires letting the live one expire.

        Raises BillingBusinessError on 400 (e.g. "Already subscribed to Pro.").
        """
        body: dict[str, Any] = {
            "payment_method": payment_method,
            **({"months": months} if months is not None else {}),
            **_channel_field(channel),
            **_redirect_fields(success_url, cancel_url),
        }
        return await self._request(
            "POST",
            "/v1/power-subscription/subscribe",
            login_token=login_token,
            json_body=body or None,
        )

    async def cancel(self, login_token: str, *, channel: Optional[str] = None) -> Any:
        """Cancel = turn off auto-renew (stays Pro until period end).

        Returns ``{status: "auto_renew_off"}``. Raises BillingBusinessError on
        400 (e.g. "No active Pro subscription.").

        ``channel`` is sent for the same reason subscribe sends it, and the
        reasoning is worth stating because the two possible upstream behaviours
        have very different costs. If upstream locates the subscription by
        channel, omitting it means a card subscription created on the nexus
        account CANNOT BE CANCELLED — the user clicks cancel, gets "No active
        Pro subscription.", and is charged again next month. If instead upstream
        routes by the subscription's own account, an extra field is inert. So
        sending it is correct under one hypothesis and harmless under the other,
        which is the only asymmetry that matters here. Measured 2026-08-19: both
        endpoints accept the field (200).

        WARNING, measured the same day: on a ONE-TIME (Alipay/WeChat)
        subscription this endpoint answers 200 and reports success while
        changing nothing that matters — it is a no-op that claims to have
        cancelled something which was never renewing. The UI must not offer it
        there; see NetmindActionZone's pro_onetime branch.
        """
        return await self._request(
            "POST",
            "/v1/power-subscription/cancel",
            login_token=login_token,
            json_body=_channel_field(channel) or None,
        )

    async def reactivate(self, login_token: str, *, channel: Optional[str] = None) -> Any:
        """Re-enable auto-renew on a cancelled-but-in-period subscription.

        ``channel`` — see ``cancel`` for why these two send it.

        WARNING (measured 2026-08-19, dev): called on a ONE-TIME
        (Alipay/WeChat) subscription this does NOT merely no-op — it answers 200
        and genuinely flips ``auto_renew`` to true on a product that cannot
        auto-renew. Nothing then renews it, so the flag is simply a lie the
        panel would have to read past. This is why ``resolveState`` tests
        ``payment_method`` BEFORE ``auto_renew``: with the opposite ordering a
        one-time subscriber whose state had been corrupted this way would be
        shown "Pro active — cancel subscription". The UI never calls this for a
        one-time subscription; the ordering is the belt to that braces.
        """
        return await self._request(
            "POST",
            "/v1/power-subscription/reactivate",
            login_token=login_token,
            json_body=_channel_field(channel) or None,
        )

    async def recharge(
        self,
        login_token: str,
        amount: float,
        *,
        payment_method: str = "default",
        channel: Optional[str] = None,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
    ) -> Any:
        """Create a Stripe HOSTED Checkout for an account top-up (finance 4.2).

        Uses the hosted-checkout endpoint (returns a redirectable
        ``checkout_url``), NOT the embedded-SDK endpoint (which returns a
        ``client_secret``) — hosted matches our openExternal flow (same as
        subscribe).

        ``success_url``/``cancel_url`` decide where Stripe drops the payer
        afterwards. Upstream really does forward them to Stripe (verified on dev
        2026-07-30: an illegal value answers 500 "Failed to create Stripe
        checkout session"), which also means junk here costs the user the
        ability to pay — the caller must resolve them from deploy config and
        omit them when it cannot. They are still never taken from client input:
        an unvalidated redirect target inside a payment session is attack
        surface. See ``routes.billing._return_urls``.

        ``payment_method`` picks the rail (``default`` card / ``alipay`` /
        ``wechat``) and DETERMINES the currency — see
        ``PAYMENT_METHOD_CURRENCY``. The caller does not get to choose a
        currency, because the only thing a disagreeing pair can produce is a
        400. ``channel`` selects the Stripe account and comes from deploy
        config, never from client input.

        Returns the wrapped body ``{success, data: {recharge_id, session_id,
        checkout_url, status}}`` — plus ``charge_currency`` / ``charge_amount``
        / ``fx_rate`` when the charge is in CNY.
        """
        body: dict[str, Any] = {
            "amount": amount,
            # USD is the fallback rather than an exception because it is the
            # conservative answer: it is what both non-WeChat rails use and what
            # this endpoint sent before payment methods existed. The real gate on
            # unknown values is the route's Literal — nothing else calls this.
            "currency": PAYMENT_METHOD_CURRENCY.get(payment_method, "USD"),
            "payment_method": payment_method,
            **_channel_field(channel),
            **_redirect_fields(success_url, cancel_url),
        }
        return await self._request(
            "POST",
            "/v1/finance/recharge/stripe/checkout",
            login_token=login_token,
            json_body=body,
        )

    async def fx_rate(
        self, login_token: str, currency: str, amount: Optional[float] = None
    ) -> Any:
        """Quote the USD -> ``currency`` rate the next charge would actually use.

        Exists so a WeChat payer can be shown "$10 ≈ ¥73" BEFORE they commit:
        they think in the USD credit they are buying, but their bank statement
        will say CNY, and an unexplained number at the QR code is where people
        abandon a payment.

        Upstream states this is the SAME rate the real charge is priced at, so
        we deliberately do not cache it — a cached quote is a second, quietly
        disagreeing source of truth for a number the user is about to be
        charged.

        ``amount`` is optional: omitted asks for the bare rate, present also
        returns ``charge_amount``. It is left out of the query entirely rather
        than sent empty, which would be a different request. The reply also
        carries ``min_amount_usd`` / ``min_charge`` — the floor below which a
        recharge is rejected with a 400, so callers can stop it earlier.
        """
        params: dict[str, Any] = {"currency": currency}
        if amount is not None:
            params["amount"] = amount
        return await self._request(
            "GET",
            "/v1/finance/recharge/fx-rate",
            login_token=login_token,
            params=params,
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
