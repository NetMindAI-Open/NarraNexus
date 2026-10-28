"""
@file_name: billing.py
@author: NetMind.AI
@date: 2026-07-02
@description: Backend proxy for NetMind billing/subscription API.

The user's NetMind loginToken lives in the frontend (configStore.netmindToken);
it is forwarded per-request via the ``X-Netmind-Token`` header and proxied to
NetMind's billing API. We never store or log the token — this layer only adds
the HTTP envelope, cloud gating, and error mapping (D-1: backend proxy).

Gated on the "power" axis, NOT the deployment/security axis. The public
catalog (/plans) gates on ``is_power_login_enabled()`` (cloud OR a local
deployment that opted into NetMind login). Every user-scoped endpoint gates on
``is_power_account(user_id)`` — the resolved user must be a NetMind
("individual") account. A pure-local username user therefore gets a clean 404,
while a Power user on a local install gets the full billing panel. We
deliberately do NOT gate on ``is_cloud_mode()``: that is the JWT security
regime, orthogonal to whether Power billing applies (see
``utils.deployment_mode`` "two orthogonal axes").

Phase 1 scope: GET /plans (public), GET /subscription (loginToken). Balance,
subscribe/cancel, and recharge land in later phases on the same proxy.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field, model_validator

from backend.auth import resolve_current_user_id
from narranexus.platform.analytics import track
from narranexus.platform.analytics.events import (
    EVENT_CHECKOUT_CREATED,
    EVENT_SUBSCRIPTION_ACTIVATED,
    PROP_MONTHS,
    PROP_PAYMENT_METHOD,
    PROP_SESSION_ID,
)
from narranexus.platform.settings import settings
from narranexus.platform.utils.url_safety import is_obviously_non_public_host
from narranexus.platform.utils.deployment_mode import (
    is_cloud_mode,
    is_power_login_enabled,
)
from backend.integrations.netmind.power_account import is_power_account
from backend.integrations.netmind.netmind_billing_client import (
    BillingAuthError,
    BillingBusinessError,
    BillingForbiddenError,
    BillingNotFoundError,
    BillingUpstreamError,
    NetmindBillingClient,
)
from backend.auth_errors import NETMIND_TOKEN_INVALID, AuthError

router = APIRouter()

_NETMIND_TOKEN_HEADER = "X-Netmind-Token"


def _normalise_billing_timestamp(value: object) -> str | None:
    """Convert a controlled upstream timestamp to DB-neutral UTC DATETIME."""
    try:
        if isinstance(value, (int, float)):
            seconds = float(value)
            if seconds > 10_000_000_000:
                seconds /= 1000
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        elif isinstance(value, str) and value.strip():
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            parsed = parsed.astimezone(timezone.utc)
        else:
            return None
    except (OverflowError, TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def _subscription_activation_fact(
    user_id: str,
    subscription: dict,
) -> tuple[str, str] | None:
    """Return a stable event ID and authoritative activation timestamp.

    A bare ACTIVE snapshot is insufficient: without a subscription/cycle key
    it cannot distinguish the first purchase from a later re-subscription, and
    without an upstream timestamp it would mislabel polling time as payment
    time. Such snapshots deliberately produce no fact.
    """
    subscription_id = subscription.get("subscription_id") or subscription.get("id")
    period_start = subscription.get("current_period_start") or subscription.get(
        "period_start"
    )
    identity = subscription_id or period_start
    raw_occurred_at = (
        subscription.get("activated_at")
        or subscription.get("start_time")
        or subscription.get("created_at")
        or period_start
    )
    occurred_at = _normalise_billing_timestamp(raw_occurred_at)
    if not identity or not occurred_at:
        return None
    digest = hashlib.sha256(f"{user_id}:{identity}".encode("utf-8")).hexdigest()
    return f"subscription_activated:{digest}", occurred_at


def _client() -> NetmindBillingClient:
    """Build a billing client from settings. Cheap; no shared mutable state."""
    return NetmindBillingClient(
        base_url=settings.billing_api_base,
        timeout_seconds=settings.billing_api_timeout_seconds,
    )


def _require_power_login_enabled() -> None:
    """404 where Power login is unavailable — used by the public /plans catalog,
    which has no user identity to check ``is_power_account`` against."""
    if not is_power_login_enabled():
        raise HTTPException(status_code=404, detail="Not available in local mode")


async def _require_power_account(request: Request) -> str:
    """Resolve the caller and require billing be available to them.

    Raises 401 if unauthenticated (no identity on the request). Otherwise
    reachable when EITHER:
      - this is the multi-tenant cloud server (``is_cloud_mode()``) — preserves
        the pre-existing cloud behavior exactly (every authenticated user could
        reach billing; a non-NetMind user still 401s later for lack of the
        X-Netmind-Token, so nothing new leaks), OR
      - the resolved user is a NetMind ("Power") account (``is_power_account``)
        — the new local dual-mode path.
    A pure-local username user on a local install gets a clean 404.

    The cloud short-circuit is deliberate: gating cloud purely on
    ``user_type == "individual"`` would newly 404 any non-individual cloud row
    (staff / legacy), a behavior regression flagged in review. Keeping
    ``is_cloud_mode()`` here restores the old cloud semantics while still adding
    the per-user local path.
    """
    uid = await resolve_current_user_id(request)
    if is_cloud_mode() or await is_power_account(uid):
        return uid
    raise HTTPException(status_code=404, detail="Not available for this account")


def _require_netmind_token(request: Request) -> str:
    """Extract the user's NetMind loginToken from the request header.

    The frontend holds it in configStore.netmindToken and sends it on every
    billing call. Missing -> 401 (the user must (re-)authenticate with NetMind).
    """
    token = request.headers.get(_NETMIND_TOKEN_HEADER, "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise AuthError(
            NETMIND_TOKEN_INVALID,
            f"Missing NetMind token ({_NETMIND_TOKEN_HEADER} header)",
        )
    return token


@router.get("/plans")
async def get_plans(request: Request):
    """Public plan catalog (Free / Pro). No NetMind token needed; available
    wherever Power login is enabled."""
    _require_power_login_enabled()
    try:
        data = await _client().get_plans()
    except (BillingUpstreamError, BillingBusinessError) as exc:
        # A business 4xx on a read endpoint is an upstream contract violation,
        # not a user-actionable error -> 502 (not 500). Catching
        # BillingBusinessError here is required since _request() raises it for
        # ALL non-auth 4xx, including on this read path.
        logger.error(f"[billing] get_plans upstream failure: {exc}")
        raise HTTPException(status_code=502, detail="Billing service unavailable")
    return {"success": True, "data": data}


@router.get("/subscription")
async def get_subscription(request: Request):
    """Current plan + subscription status for the logged-in user.

    Identity is established locally (auth_middleware -> resolve_current_user_id);
    the NetMind loginToken is forwarded to identify the user on NetMind's side.
    """
    # Require a Power account (rejects unauthenticated -> 401, local user -> 404).
    user_id = await _require_power_account(request)
    token = _require_netmind_token(request)
    try:
        data = await _client().get_subscription(token)
    except BillingAuthError:
        # Bad / expired loginToken -> 401 so the frontend re-auths with NetMind.
        raise AuthError(NETMIND_TOKEN_INVALID, "NetMind token invalid or expired")
    except (BillingUpstreamError, BillingBusinessError) as exc:
        # Business 4xx on a read endpoint = upstream contract violation -> 502.
        logger.error(f"[billing] get_subscription upstream failure: {exc}")
        raise HTTPException(status_code=502, detail="Billing service unavailable")
    subscription = data.get("subscription") if isinstance(data, dict) else None
    if isinstance(subscription, dict) and subscription.get("status") == "ACTIVE":
        activation = _subscription_activation_fact(user_id, subscription)
        if activation is not None:
            event_id, occurred_at = activation
            await track(
                user_id=user_id,
                event=EVENT_SUBSCRIPTION_ACTIVATED,
                event_id=event_id,
                occurred_at=occurred_at,
            )
    return {"success": True, "data": data}


def _validate_checkout_url(url: object) -> None:
    """Reject a checkout_url the upstream returned unless it is https on the
    Stripe payment domain. Defends against a compromised/MITM'd billing
    upstream handing the frontend an attacker URL that openExternal would then
    open on the user's machine (Tauri shell-open). Backend-side so a modified
    frontend can't bypass it.
    """
    host = ""
    scheme = ""
    if isinstance(url, str):
        parsed = urlparse(url)
        scheme = parsed.scheme
        host = (parsed.hostname or "").lower()
    ok = scheme == "https" and (host == "stripe.com" or host.endswith(".stripe.com"))
    if not ok:
        logger.error(f"[billing] subscribe returned non-allowlisted checkout host: {host!r}")
        raise HTTPException(
            status_code=502, detail="Billing service returned an invalid checkout URL"
        )


# Where the payer comes back to. `/app/settings` + `tab=account` is the Account
# & Subscription panel (SettingsPage reads `tab`; the panel consumes `status` and
# `flow`, then strips them so a refresh doesn't re-announce the payment).
_RETURN_PATH = "/app/settings"


def _return_urls(flow: Literal["subscription", "topup"]) -> dict[str, str]:
    """Post-payment redirect targets, or ``{}`` when this deployment has none.

    Stripe sends the payer to the URLs stored on the Checkout Session, and
    NetMind — not us — creates that session, so handing these two fields
    upstream is the only lever we have. Without them the payer lands on
    NetMind's own result page, on a domain they have never seen (the 2026-07-30
    P0: "paid on NarraNexus, ended up on a stranger's site").

    The origin comes ONLY from deploy config (``settings.public_base_url``),
    never from request headers. A payment session's redirect target must not be
    influenceable by anything a caller can set, and the deploy value is already
    trusted — which also makes an allowlist redundant rather than a second line
    of defence. Consequence: an install without ``PUBLIC_BASE_URL`` (self-hosted,
    and the desktop app, whose front-end origin is ``tauri://localhost`` and
    therefore unreachable for Stripe anyway) keeps exactly today's behavior.

    Anything the upstream would choke on yields ``{}`` instead of a broken
    checkout. This is the load-bearing rule, and it is built on measurements
    (dev, 2026-07-30) rather than assumption, because guessing wrong here costs
    the user the ability to pay at all — never worth a cosmetic redirect:

    - A malformed URL makes the upstream answer 500 "Failed to create Stripe
      checkout session".
    - A loopback / private host is rejected by the upstream's EDGE with an HTML
      403 — for any scheme, `https://localhost` and `http://192.168.x.x` alike.
      Our client maps a 403 to BillingAuthError, which this route reports as
      401 "NetMind token invalid or expired", so sending one would break payment
      AND blame the user's login for it. Hence the host screen below; it also
      means a `bash run.sh` install cannot receive the redirect no matter what
      we send — that block is upstream, not ours.
    """
    # Parsing AND validation live in one try. Both steps raise ValueError on a
    # malformed deploy value, and an uncaught one would 500 both payment endpoints
    # — the exact outcome this function exists to prevent:
    #   - `urlparse` itself raises on a netloc that NFKC-normalizes to something
    #     else (a FULL-WIDTH colon, `：` — the likeliest slip when this value is
    #     typed into an EC2 .env by hand) and on unbalanced IPv6 brackets.
    #   - `.port` is a property that parses, so it raises on `:99999` / `:abc`
    #     where `.scheme`/`.hostname` happily do not.
    # Same shape as `url_artifact._origin_tuple`, which already guards `.port`
    # for this very setting (its own `urlparse` call is unguarded — tracked).
    try:
        parsed = urlparse((settings.public_base_url or "").strip())
        # https only. NOT because the upstream refuses plain http — it accepts it
        # (probed: http://example.com → 200). Stripe's live-mode behavior with a
        # plain-http return URL is what we have not verified, and the cost of being
        # wrong is asymmetric: refusing http merely denies an http self-hoster the
        # redirect, while sending something Stripe rejects breaks their checkout.
        if parsed.scheme != "https" or not parsed.hostname:
            return {}
        # Private / loopback / single-label hosts: see the 403 note above.
        if is_obviously_non_public_host(parsed.hostname):
            return {}
        _ = parsed.port  # read for VALIDATION only — the value is unused
    except ValueError:
        return {}
    # A netloc that survives parsing can still be unusable: an ASCII space or a
    # zero-width character passes both urlparse and the host screen, then reaches
    # Stripe as a malformed URL and earns a 500 — the same paste/IME family as
    # above, just failing upstream instead of here. An operator on an
    # internationalised domain must configure its punycode form, which is ASCII.
    if not parsed.netloc.isascii() or any(c.isspace() for c in parsed.netloc):
        return {}
    # Take host+port from netloc, minus the userinfo. netloc is the only form that
    # keeps IPv6 brackets intact (`.hostname` strips them, and re-adding a port
    # would then produce `https://2001:db8::1:8443` — a malformed URL). The
    # userinfo MUST go: a `user:pass@` base URL would otherwise leak basic-auth
    # credentials to NetMind and into a stored Stripe session.
    origin = f"https://{parsed.netloc.rpartition('@')[2]}"

    def _url(status: str) -> str:
        query = urlencode({"tab": "account", "status": status, "flow": flow})
        return f"{origin}{_RETURN_PATH}?{query}"

    return {"success_url": _url("success"), "cancel_url": _url("cancelled")}


@router.get("/fee-info")
async def get_fee_info(request: Request):
    """User balance + eligibility (module B). Requires the NetMind loginToken.

    Field-level note: NetMind's user-fee-info has no per-period consumption and
    `free_credit` conflates subscription grant + recharge (gap G1) — the panel
    shows the degraded view. The endpoint auth itself is now live (was 403).
    """
    await _require_power_account(request)
    token = _require_netmind_token(request)
    try:
        data = await _client().get_fee_info(token)
    except BillingAuthError:
        raise AuthError(NETMIND_TOKEN_INVALID, "NetMind token invalid or expired")
    except (BillingUpstreamError, BillingBusinessError) as exc:
        logger.error(f"[billing] get_fee_info upstream failure: {exc}")
        raise HTTPException(status_code=502, detail="Billing service unavailable")
    return {"success": True, "data": data}


@router.get("/records")
async def get_records(request: Request, direction: str | None = None):
    """Financial records / transactions (module B — consumption + recharge
    history). Resolves gap G1: NetMind now exposes per-record ledger, so the
    balance panel can show real activity, not just a mixed balance snapshot.

    ``direction``: expense (consumption) / income (recharge/refund); default all.
    """
    await _require_power_account(request)
    token = _require_netmind_token(request)
    try:
        body = await _client().get_records(token, direction=direction)
    except BillingAuthError:
        raise AuthError(NETMIND_TOKEN_INVALID, "NetMind token invalid or expired")
    except (BillingUpstreamError, BillingBusinessError) as exc:
        logger.error(f"[billing] get_records upstream failure: {exc}")
        raise HTTPException(status_code=502, detail="Billing service unavailable")
    records = body.get("data") if isinstance(body, dict) else None
    return {
        "success": True,
        "data": records if isinstance(records, list) else [],
        "has_next": bool(body.get("has_next")) if isinstance(body, dict) else False,
    }


async def _write_action(
    request: Request,
    action: Literal["subscribe", "cancel", "reactivate"],
    extra: dict[str, Any] | None = None,
):
    """Shared harness for the subscription write routes (subscribe / cancel /
    reactivate): Power-account gate + NetMind token, then dispatch to the client
    method, mapping the three error kinds consistently.

    ``extra`` carries per-action keyword arguments, and now has two kinds of
    payload: ``channel`` (all three actions) and subscribe's return URLs (only
    subscribe). Only the second is excluded from cancel/reactivate — they open
    no Stripe checkout, so they have nowhere to redirect to. Keeping this a
    parameter rather than resolving it inside the harness is what keeps that
    exclusion true, and there is a test named for it.

    BillingBusinessError -> 400 (surface the user-safe message, e.g. "Already
    subscribed"); BillingAuthError -> 401; BillingUpstreamError -> 502.
    """
    await _require_power_account(request)
    token = _require_netmind_token(request)
    method = getattr(_client(), action)
    try:
        data = await method(token, **(extra or {}))
    except BillingAuthError:
        raise AuthError(NETMIND_TOKEN_INVALID, "NetMind token invalid or expired")
    except BillingBusinessError as exc:
        # e.g. "Already subscribed to Pro." / "No active Pro subscription."
        raise HTTPException(status_code=400, detail=exc.message)
    except BillingUpstreamError as exc:
        logger.error(f"[billing] {action} upstream failure: {exc}")
        raise HTTPException(status_code=502, detail="Billing service unavailable")
    return {"success": True, "data": data}


class SubscribeRequest(BaseModel):
    """Body for POST /subscribe — which of the two Pro products to start.

    ``stripe`` is a real card subscription that renews itself. ``alipay`` /
    ``wechat`` cannot pay a Stripe subscription at all, so they are a ONE-TIME
    purchase of ``months`` months that simply ends when the period does.

    ``months`` therefore belongs to the one-time mode only, and passing it with
    a card is REJECTED rather than ignored: someone who sends "card, 6 months"
    believes they are buying six months on a card, and quietly charging them
    for one is the worst of the three available outcomes. ``model_fields_set``
    (rather than a sentinel default) is what separates "explicitly asked for
    1 month" from "never mentioned months".
    """

    payment_method: Literal["stripe", "alipay", "wechat"] = "stripe"
    months: int = Field(1, ge=1, le=12)

    @model_validator(mode="after")
    def _months_only_for_one_time(self) -> "SubscribeRequest":
        if self.payment_method == "stripe" and "months" in self.model_fields_set:
            raise ValueError("months does not apply to a card subscription")
        return self


@router.post("/subscribe")
async def subscribe(request: Request, req: SubscribeRequest | None = None):
    """Start a Pro subscription — returns Stripe {session_id, checkout_url}.

    The body is optional: a caller that just wants the card subscription can
    keep posting nothing at all.
    """
    req = req or SubscribeRequest()
    extra: dict[str, Any] = {
        "payment_method": req.payment_method,
        "channel": settings.billing_channel,
        **_return_urls("subscription"),
    }
    # Only the one-time products have a month count; see SubscribeRequest.
    if req.payment_method != "stripe":
        extra["months"] = req.months
    result = await _write_action(request, "subscribe", extra)
    _validate_checkout_url((result.get("data") or {}).get("checkout_url"))
    user_id = await resolve_current_user_id(request)
    session_id = (result.get("data") or {}).get("session_id")
    await track(
        user_id=user_id,
        event=EVENT_CHECKOUT_CREATED,
        event_id=f"checkout_created:{session_id}" if session_id else None,
        properties={
            PROP_SESSION_ID: session_id,
            # Which product was bought. `months` only for the one-time rails —
            # a card subscription has no month count, and sending 1 would read
            # as "someone bought one month on a card", a thing that cannot
            # happen.
            PROP_PAYMENT_METHOD: req.payment_method,
            **({} if req.payment_method == "stripe" else {PROP_MONTHS: req.months}),
        },
    )
    return result


@router.get("/fx-rate")
async def fx_rate(request: Request, amount: float | None = Query(None, gt=0)):
    """Quote what a CNY charge would actually cost, before the user commits.

    WeChat Pay settles in CNY on this account while everything being bought is
    denominated in USD, so "$10" and "¥73" have to appear together — an
    unexplained number next to the QR code is where a payment gets abandoned.

    ``currency`` is pinned to CNY rather than read from the query: CNY is the
    only non-USD currency this account charges in, and an open parameter would
    just be a way to ask the upstream questions we have no screen for.

    ``amount`` is optional — omitted asks for the bare rate, present also
    returns the converted total. The reply carries the WeChat minimum too, so
    the caller can stop an under-minimum payment before creating a checkout the
    upstream would only 400.
    """
    await _require_power_account(request)
    token = _require_netmind_token(request)
    try:
        body = await _client().fx_rate(token, "CNY", amount=amount)
    except BillingAuthError:
        raise AuthError(NETMIND_TOKEN_INVALID, "NetMind token invalid or expired")
    except (BillingUpstreamError, BillingBusinessError) as exc:
        # Business 4xx on a read endpoint = upstream contract violation -> 502,
        # same as /plans and /subscription.
        logger.error(f"[billing] fx_rate upstream failure: {exc}")
        raise HTTPException(status_code=502, detail="Billing service unavailable")
    # Upstream returns the quote flat; unwrap if it ever arrives enveloped so
    # the frontend only ever reads one shape.
    inner = body.get("data") if isinstance(body, dict) else None
    if isinstance(inner, dict):
        return {"success": True, "data": inner}
    return {"success": True, "data": body if isinstance(body, dict) else {}}


@router.post("/cancel")
async def cancel(request: Request):
    """Cancel = turn off auto-renew; stays Pro until period end.

    Carries `channel` but NOT the return URLs — this opens no Stripe checkout,
    which is the whole reason `extra` is a parameter rather than something the
    harness resolves. See the client for why the channel is sent.
    """
    return await _write_action(request, "cancel", {"channel": settings.billing_channel})


@router.post("/reactivate")
async def reactivate(request: Request):
    """Re-enable auto-renew on a cancelled-but-in-period subscription.

    Card subscriptions only — on a one-time purchase this genuinely flips
    auto_renew on something that cannot renew (measured 2026-08-19). The panel
    never offers it there.
    """
    return await _write_action(
        request, "reactivate", {"channel": settings.billing_channel}
    )


# --- Phase 4: recharge / top-up (module E) ---------------------------------

# Preset tiers live in the frontend; the API accepts any positive amount. We
# only guard amount > 0 here (a 0/negative amount is a client bug, not a
# business rejection worth a round-trip to NetMind).
_MAX_RECHARGE_AMOUNT = 100_000  # sanity ceiling; NetMind is the real authority

# Stripe Checkout Session ids are `cs_test_...` / `cs_live_...`. The `session_id`
# path param is spliced into the OUTBOUND upstream URL, so it must be a strict
# opaque token — never a path fragment. Without this, a `..` segment (which
# Starlette's string converter does NOT reject) is normalized by httpx and the
# request lands on a DIFFERENT NetMind endpoint (still with the caller's token).
_STRIPE_SESSION_ID_RE = re.compile(r"^cs_[A-Za-z0-9_]+$")


class RechargeRequest(BaseModel):
    """Body for POST /recharge. Preset tiers are a frontend convenience; any
    positive amount (<= ceiling) is accepted.

    ``amount`` is ALWAYS the USD figure — the credit the user ends up with.
    A WeChat payer is charged an equivalent in CNY that upstream computes at
    its own live rate; see GET /fx-rate for showing them that number first.

    There is deliberately no ``currency`` field. It is a function of
    ``payment_method`` (upstream 400s when the two disagree), so it is derived
    in the client where the upstream contract is modelled. A body that still
    carries one from an older frontend is ignored, not rejected — during a
    rolling deploy that is the difference between "the field does nothing" and
    "nobody can pay".
    """

    amount: float = Field(gt=0, le=_MAX_RECHARGE_AMOUNT)
    payment_method: Literal["default", "alipay", "wechat"] = "default"


@router.post("/recharge")
async def recharge(req: RechargeRequest, request: Request):
    """Create a hosted Stripe Checkout for an account top-up.

    Returns Stripe ``{recharge_id, session_id, checkout_url, status}``; the
    frontend opens ``checkout_url`` then polls GET /recharge/{session_id}.
    """
    await _require_power_account(request)
    token = _require_netmind_token(request)
    try:
        body = await _client().recharge(
            token,
            req.amount,
            payment_method=req.payment_method,
            channel=settings.billing_channel,
            **_return_urls("topup"),
        )
    except BillingAuthError:
        raise AuthError(NETMIND_TOKEN_INVALID, "NetMind token invalid or expired")
    except BillingBusinessError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
    except BillingUpstreamError as exc:
        logger.error(f"[billing] recharge upstream failure: {exc}")
        raise HTTPException(status_code=502, detail="Billing service unavailable")
    inner = body.get("data") if isinstance(body, dict) else None
    inner = inner if isinstance(inner, dict) else {}
    # Same MITM guard as subscribe: never hand the frontend a non-Stripe URL.
    _validate_checkout_url(inner.get("checkout_url"))
    return {"success": True, "data": inner}


@router.get("/recharge/{session_id}")
async def recharge_status(session_id: str, request: Request):
    """Poll a recharge by Stripe session id. Returns ``{status}`` =
    pending/succeeded/failed. 403 (not the caller's session) and 404 (unknown
    session) are passed through, not collapsed to 401/400."""
    await _require_power_account(request)
    token = _require_netmind_token(request)
    # Strict allowlist BEFORE the id is spliced into the outbound upstream path
    # — blocks `..`/`?`/`#`/`/` smuggling that would retarget the NetMind call.
    if not _STRIPE_SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=404, detail="Recharge session not found")
    try:
        body = await _client().recharge_status(token, session_id)
    except BillingAuthError:
        raise AuthError(NETMIND_TOKEN_INVALID, "NetMind token invalid or expired")
    except BillingForbiddenError:
        raise HTTPException(status_code=403, detail="This recharge is not yours")
    except BillingNotFoundError:
        raise HTTPException(status_code=404, detail="Recharge session not found")
    except (BillingUpstreamError, BillingBusinessError) as exc:
        logger.error(f"[billing] recharge_status upstream failure: {exc}")
        raise HTTPException(status_code=502, detail="Billing service unavailable")
    inner = body.get("data") if isinstance(body, dict) else None
    return {"success": True, "data": inner if isinstance(inner, dict) else {}}
