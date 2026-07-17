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

import re
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from backend.auth import resolve_current_user_id
from xyz_agent_context.settings import settings
from xyz_agent_context.utils.deployment_mode import (
    is_cloud_mode,
    is_power_login_enabled,
)
from xyz_agent_context.services.power_account import is_power_account
from xyz_agent_context.services.netmind_billing_client import (
    BillingAuthError,
    BillingBusinessError,
    BillingForbiddenError,
    BillingNotFoundError,
    BillingUpstreamError,
    NetmindBillingClient,
)

router = APIRouter()

_NETMIND_TOKEN_HEADER = "X-Netmind-Token"


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
        raise HTTPException(
            status_code=401,
            detail=f"Missing NetMind token ({_NETMIND_TOKEN_HEADER} header)",
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
    await _require_power_account(request)
    token = _require_netmind_token(request)
    try:
        data = await _client().get_subscription(token)
    except BillingAuthError:
        # Bad / expired loginToken -> 401 so the frontend re-auths with NetMind.
        raise HTTPException(status_code=401, detail="NetMind token invalid or expired")
    except (BillingUpstreamError, BillingBusinessError) as exc:
        # Business 4xx on a read endpoint = upstream contract violation -> 502.
        logger.error(f"[billing] get_subscription upstream failure: {exc}")
        raise HTTPException(status_code=502, detail="Billing service unavailable")
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
        raise HTTPException(status_code=401, detail="NetMind token invalid or expired")
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
        raise HTTPException(status_code=401, detail="NetMind token invalid or expired")
    except (BillingUpstreamError, BillingBusinessError) as exc:
        logger.error(f"[billing] get_records upstream failure: {exc}")
        raise HTTPException(status_code=502, detail="Billing service unavailable")
    records = body.get("data") if isinstance(body, dict) else None
    return {
        "success": True,
        "data": records if isinstance(records, list) else [],
        "has_next": bool(body.get("has_next")) if isinstance(body, dict) else False,
    }


async def _write_action(request: Request, action: Literal["subscribe", "cancel", "reactivate"]):
    """Shared harness for the subscription write routes (subscribe / cancel /
    reactivate): Power-account gate + NetMind token, then dispatch to the client
    method, mapping the three error kinds consistently.

    BillingBusinessError -> 400 (surface the user-safe message, e.g. "Already
    subscribed"); BillingAuthError -> 401; BillingUpstreamError -> 502.
    """
    await _require_power_account(request)
    token = _require_netmind_token(request)
    method = getattr(_client(), action)
    try:
        data = await method(token)
    except BillingAuthError:
        raise HTTPException(status_code=401, detail="NetMind token invalid or expired")
    except BillingBusinessError as exc:
        # e.g. "Already subscribed to Pro." / "No active Pro subscription."
        raise HTTPException(status_code=400, detail=exc.message)
    except BillingUpstreamError as exc:
        logger.error(f"[billing] {action} upstream failure: {exc}")
        raise HTTPException(status_code=502, detail="Billing service unavailable")
    return {"success": True, "data": data}


@router.post("/subscribe")
async def subscribe(request: Request):
    """Start a Pro subscription — returns Stripe {session_id, checkout_url}."""
    result = await _write_action(request, "subscribe")
    _validate_checkout_url((result.get("data") or {}).get("checkout_url"))
    return result


@router.post("/cancel")
async def cancel(request: Request):
    """Cancel = turn off auto-renew; stays Pro until period end."""
    return await _write_action(request, "cancel")


@router.post("/reactivate")
async def reactivate(request: Request):
    """Re-enable auto-renew on a cancelled-but-in-period subscription."""
    return await _write_action(request, "reactivate")


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
    positive amount (<= ceiling) is accepted."""

    amount: float = Field(gt=0, le=_MAX_RECHARGE_AMOUNT)
    currency: str = "USD"


@router.post("/recharge")
async def recharge(req: RechargeRequest, request: Request):
    """Create a hosted Stripe Checkout for an account top-up.

    Returns Stripe ``{recharge_id, session_id, checkout_url, status}``; the
    frontend opens ``checkout_url`` then polls GET /recharge/{session_id}.
    """
    await _require_power_account(request)
    token = _require_netmind_token(request)
    try:
        body = await _client().recharge(token, req.amount, req.currency)
    except BillingAuthError:
        raise HTTPException(status_code=401, detail="NetMind token invalid or expired")
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
        raise HTTPException(status_code=401, detail="NetMind token invalid or expired")
    except BillingForbiddenError:
        raise HTTPException(status_code=403, detail="This recharge is not yours")
    except BillingNotFoundError:
        raise HTTPException(status_code=404, detail="Recharge session not found")
    except (BillingUpstreamError, BillingBusinessError) as exc:
        logger.error(f"[billing] recharge_status upstream failure: {exc}")
        raise HTTPException(status_code=502, detail="Billing service unavailable")
    inner = body.get("data") if isinstance(body, dict) else None
    return {"success": True, "data": inner if isinstance(inner, dict) else {}}
