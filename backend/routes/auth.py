"""
@file_name: auth.py
@author: NetMind.AI
@date: 2025-11-28
@description: REST API routes for authentication and user management

Provides endpoints for:
- POST /api/auth/login - Login with user_id
- POST /api/auth/create-user - Create a new user (requires admin secret key)
- GET /api/auth/agents - Get all agents for a user
- POST /api/auth/agents - Create a new agent
- PUT /api/auth/agents/{agent_id} - Update agent info
- DELETE /api/auth/agents/{agent_id} - Cascade delete agent and all related data
"""

import os
import json
import jwt
from time import monotonic
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from loguru import logger

from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils.logging import (
    set_telemetry_optout,
    telemetry_consent,
)
from narranexus.platform.utils import format_for_api
from narranexus.platform.analytics import track
from narranexus.platform.analytics.events import (
    EVENT_SIGNED_UP, PROP_METHOD,
)
from narranexus.platform.repository import (
    AgentRepository,
    UserRepository,
)
from narranexus.platform.schema import (
    AGENT_TEXT_MAX_LENGTH,
    NON_TRANSACTING_USER_STATUSES,
    agent_field_matches,
    normalize_agent_text,
    LoginRequest,
    LoginResponse,
    NetmindLoginRequest,
    NetmindLoginResponse,
    ActiveRunInfo,
    AgentInfo,
    AgentListResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    UpdateAgentRequest,
    UpdateAgentResponse,
    DeleteAgentResponse,
    CreateUserRequest,
    CreateUserResponse,
    UpdateTimezoneRequest,
    UpdateTimezoneResponse,
    OnboardingProgress,
    OnboardingResponse,
    UpdateOnboardingRequest,
    BoundChannel,
)
from backend.auth import (
    create_token,
    decode_token,
    _is_cloud_mode,
    resolve_current_user_id,
)
from backend.auth_errors import (
    ACCOUNT_SUSPENDED,
    IDENTITY_UNRESOLVED,
    NETMIND_TOKEN_INVALID,
    AuthError,
)
from backend.routes._client_ip import client_ip
from backend.routes._email_policy import is_blocked_email
from backend.routes._rate_limiter import SlidingWindowRateLimiter, env_limit
from narranexus.platform.agent_profile import apply_agent_profile_change
from narranexus.platform.utils.deployment_mode import is_power_login_enabled
from narranexus.platform.utils import is_valid_timezone
from narranexus.platform.utils.run_liveness import run_is_live
from narranexus.platform.settings import settings as app_settings
from narranexus.platform.agent_framework.providers.slot_service import AgentSlotService
from narranexus.platform.channel.binding_tables import bound_channels_query, channel_binding_sources

from pydantic import BaseModel
from narranexus.platform.repository.user_settings_repository import UserSettingsRepository
from typing import Iterable, Optional


router = APIRouter()


# Heartbeat-freshness liveness rule for events rows stuck at
# state='running' — shared with the WS reconnect path so "is this run
# actually alive?" has one answer. See utils/run_liveness.py.
# Without this filter the sidebar avatar pulses "running" forever for an
# agent whose run task died without _finalize.
_run_is_live = run_is_live


async def _schedule_login_rearm(user_id: str) -> None:
    """On login, kick a background edge-recovery: if the user is now provider-
    ready (e.g. they topped up / fixed config while away), builtin.job revives
    their PAUSED_NO_QUOTA jobs (its hook schedules the work; login responds
    immediately)."""
    try:
        from backend.host_events import notify_user_runnability_changed

        await notify_user_runnability_changed(user_id)
    except Exception:  # noqa: BLE001 — never let recovery wiring break login
        pass


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Local-mode login with user_id only (OS user is the trust boundary).

    Cloud mode has no password login anymore — cloud identity lives in
    the NetMind account system; use POST /api/auth/netmind-login.
    """
    if _is_cloud_mode():
        raise HTTPException(
            status_code=404,
            detail="Password login is gone. Use /api/auth/netmind-login.",
        )

    logger.info(f"Login attempt for user: {request.user_id}")

    try:
        db_client = await get_db_client()
        user_repo = UserRepository(db_client)

        user = await user_repo.get_user(request.user_id)

        if not user:
            logger.warning(f"User {request.user_id} not found")
            return LoginResponse(
                success=False,
                error="User not found. Please contact administrator to create an account.",
            )

        await user_repo.update_last_login(request.user_id)
        logger.info(f"User {request.user_id} logged in (local)")
        await _schedule_login_rearm(request.user_id)
        # A local login is never a signup (create-user is); is_new=False routes
        # it through the backfill brake.
        _schedule_guide_agent_provisioning(request.user_id, is_new=False)
        return LoginResponse(
            success=True,
            user_id=request.user_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error during login: {e}")
        return LoginResponse(success=False, error=str(e))


def _get_netmind_auth_client():
    """Factory for the NetMind token-verification client.

    Module-level indirection so tests can monkeypatch it; the client is
    cheap to construct (stateless besides config), so no caching.
    """
    from backend.integrations.netmind.netmind_auth_client import NetmindAuthClient

    return NetmindAuthClient()


# ---------------------------------------------------------------------------
# Self-serve signup. Until 2026-07-28 "Create account" bounced users to
# netmind.ai and back; these two routes let the page own the flow.
#
# Proxied rather than called from the browser for one reason above the others:
# /register/sendCode sends mail on our behalf, so it needs a rate limit, and a
# limit that lives in the page is not a limit. See netmind_register_client for
# the secret-handling rules this path must honour.
# ---------------------------------------------------------------------------

# One code request per email per 60s — the same beat as the UI countdown, so a
# well-behaved client never trips it. This bucket protects ONE mailbox from
# being flooded; on its own it is no protection for the mail channel itself,
# because a caller rotating to a fresh address per request never hits it
# (2026-10-28). The per-IP buckets below close that.
_signup_code_limiter = SlidingWindowRateLimiter(limit=1, window_sec=60.0)
# A far looser ceiling on account creation attempts per email, so a wrong code
# can be retried a few times without opening a brute-force window on it.
_signup_attempt_limiter = SlidingWindowRateLimiter(limit=10, window_sec=600.0)

# Per-client-IP buckets (``client_ip`` — the same edge-appended XFF entry the
# funnel-report buckets use). They cap what ONE source can spend no matter how
# many addresses it rotates through: send-code mail (minute + hour, so a burst
# and a slow drip are both bounded) and signup (two hour buckets: a loose one
# charged for EVERY attempt and never refunded, and a tight one charged only
# for accounts actually created). Evaluated BEFORE the per-email bucket: the IP
# key is trusted and bounded, the email key is caller-chosen, so per-IP in
# front is what caps per-email key allocation (same invariant as
# funnel-report). On signup the never-refunded attempt bucket is what makes
# that cap real — the creation bucket refunds every failed attempt, so on its
# own it would let a caller with a junk code send unlimited requests (and
# unlimited upstream register calls) from one IP.
# Defaults are sized for an office / event NAT where a room of real people
# shares one egress; env-tunable beyond that. These buckets are defence in
# depth, not a closed loop: NetMind's own signup is public, so the login-time
# free-credit gate (_free_credit_withheld) is the backstop for the blocklist.
DEFAULT_SIGNUP_CODE_PER_IP_PER_MINUTE = 10
DEFAULT_SIGNUP_CODE_PER_IP_PER_HOUR = 60
DEFAULT_SIGNUP_PER_IP_PER_HOUR = 20
DEFAULT_SIGNUP_ATTEMPTS_PER_IP_PER_HOUR = 60
SIGNUP_CODE_PER_IP_PER_MINUTE = env_limit(
    "NARRANEXUS_SIGNUP_CODE_PER_IP_PER_MINUTE", DEFAULT_SIGNUP_CODE_PER_IP_PER_MINUTE
)
SIGNUP_CODE_PER_IP_PER_HOUR = env_limit(
    "NARRANEXUS_SIGNUP_CODE_PER_IP_PER_HOUR", DEFAULT_SIGNUP_CODE_PER_IP_PER_HOUR
)
SIGNUP_PER_IP_PER_HOUR = env_limit(
    "NARRANEXUS_SIGNUP_PER_IP_PER_HOUR", DEFAULT_SIGNUP_PER_IP_PER_HOUR
)
SIGNUP_ATTEMPTS_PER_IP_PER_HOUR = env_limit(
    "NARRANEXUS_SIGNUP_ATTEMPTS_PER_IP_PER_HOUR",
    DEFAULT_SIGNUP_ATTEMPTS_PER_IP_PER_HOUR,
)
_signup_code_ip_minute_limiter = SlidingWindowRateLimiter(
    limit=SIGNUP_CODE_PER_IP_PER_MINUTE, window_sec=60.0
)
_signup_code_ip_hour_limiter = SlidingWindowRateLimiter(
    limit=SIGNUP_CODE_PER_IP_PER_HOUR, window_sec=3600.0
)
_signup_ip_limiter = SlidingWindowRateLimiter(
    limit=SIGNUP_PER_IP_PER_HOUR, window_sec=3600.0
)
_signup_attempt_ip_limiter = SlidingWindowRateLimiter(
    limit=SIGNUP_ATTEMPTS_PER_IP_PER_HOUR, window_sec=3600.0
)

# /api/auth/netmind-login is unauthenticated too, and every request costs one
# upstream verify_token call — an invalid token included. A loose per-IP hour
# bucket, charged for every request and never refunded, keeps one source from
# turning us into an amplifier against NetMind's auth API. Loose because a
# real user logs in a handful of times a day and a whole office / event NAT
# shares one egress; env-tunable like the signup buckets.
DEFAULT_NETMIND_LOGIN_PER_IP_PER_HOUR = 120
NETMIND_LOGIN_PER_IP_PER_HOUR = env_limit(
    "NARRANEXUS_NETMIND_LOGIN_PER_IP_PER_HOUR",
    DEFAULT_NETMIND_LOGIN_PER_IP_PER_HOUR,
)
_netmind_login_ip_limiter = SlidingWindowRateLimiter(
    limit=NETMIND_LOGIN_PER_IP_PER_HOUR, window_sec=3600.0
)

# Neutral on purpose: naming the rule ("disposable") tells a prober exactly
# which domains to swap to.
_BLOCKED_EMAIL_DETAIL = (
    "This email address can't be used to sign up. Please use a different one."
)


def _rate_limited(limiter: SlidingWindowRateLimiter, key: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail=detail,
        headers={"Retry-After": str(limiter.retry_after(key))},
    )


def _enforce_ip_buckets(
    limiters: Iterable[SlidingWindowRateLimiter],
    ip: str,
    route: str,
    *,
    funnel: str = "signup-funnel",
) -> None:
    """Raise 429 (with Retry-After) on the first per-IP bucket that refuses.

    Short-circuits: a request the narrower bucket refuses never spends the
    wider one's budget. The reverse does not hold: when the hour bucket
    rejects, the minute bucket was already charged for that request.
    ``funnel`` is the log tag, so a refusal lands in the same grep as the
    rest of that route's funnel lines.
    """
    for limiter in limiters:
        if not limiter.allow(ip):
            logger.info(f"[{funnel}] {route} rate-limited ip={ip}")
            raise _rate_limited(
                limiter, ip, "Too many requests. Please try again later."
            )


def _refuse_blocked_email(email: str, route: str) -> None:
    if is_blocked_email(email):
        logger.info(f"[signup-funnel] {route} blocked-domain email={email}")
        raise HTTPException(status_code=400, detail=_BLOCKED_EMAIL_DETAIL)


class SendSignupCodeRequest(BaseModel):
    email: EmailStr


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    verify_code: str = Field(min_length=1, max_length=6)


def _register_client():
    from backend.integrations.netmind.netmind_register_client import (
        NetmindRegisterClient,
    )

    if not is_power_login_enabled():
        raise HTTPException(status_code=404, detail="Not available in local mode")
    return NetmindRegisterClient()


@router.post("/signup/send-code")
async def send_signup_code(payload: SendSignupCodeRequest, request: Request) -> dict:
    """Email a 6-digit registration code.

    Gate order: blocked domain (spends nothing) -> per-IP minute/hour ->
    per-email -> upstream.
    """
    from backend.integrations.netmind.netmind_register_client import (
        RegistrationError,
        RegistrationUpstreamError,
    )

    client = _register_client()
    email = payload.email.strip().lower()
    _refuse_blocked_email(email, "send-code")
    _enforce_ip_buckets(
        (_signup_code_ip_minute_limiter, _signup_code_ip_hour_limiter),
        client_ip(request),
        "send-code",
    )
    if not _signup_code_limiter.allow(email):
        # Logged, not just refused: seventeen 400s with zero server-side
        # trace is how the 2026-08-01 funnel post-mortem went blind.
        logger.info(f"[signup-funnel] send-code rate-limited email={email}")
        raise _rate_limited(
            _signup_code_limiter,
            email,
            "A code was just sent. Please wait a minute before retrying.",
        )
    try:
        await client.send_code(email)
    except RegistrationError as e:
        # The refusal detail is already logged at the source (_post).
        raise HTTPException(status_code=400, detail=str(e))
    except RegistrationUpstreamError as e:
        logger.error(f"[signup-funnel] send-code upstream failure email={email}: {e}")
        raise HTTPException(
            status_code=502, detail="Sign-up service unavailable, try again"
        )
    return {"success": True}


@router.post("/signup")
async def register_account(payload: SignupRequest, request: Request) -> dict:
    """Create a NetMind account from our own signup form.

    Deliberately does NOT log the user in. The page holds the credentials it
    just submitted and calls the normal login path next, so success here has
    exactly one meaning — the account exists — and no session is minted on a
    route that also accepts unverified input.
    """
    from backend.integrations.netmind.netmind_register_client import (
        RegistrationError,
        RegistrationUpstreamError,
        password_policy_error,
    )

    client = _register_client()
    email = payload.email.strip().lower()
    _refuse_blocked_email(email, "signup")

    # Re-checked server-side: the UI validates the same rules for feedback, but
    # that is a convenience, not a guarantee.
    policy_error = password_policy_error(payload.password)
    if policy_error:
        # The reason names the violated RULE, never the password.
        logger.info(f"[signup-funnel] policy reject email={email}: {policy_error}")
        raise HTTPException(status_code=400, detail=policy_error)

    # Per-IP after the policy check: a rejected weak password spends nothing
    # upstream, so it must not burn a shared network's hourly allowance.
    # Two buckets, attempt bucket FIRST (the check short-circuits, so a request
    # it refuses never reserves a creation hit that nothing would refund):
    # - _signup_attempt_ip_limiter is charged for every attempt and NEVER
    #   refunded — it is the per-IP ceiling on requests (and upstream calls);
    # - _signup_ip_limiter is a shared network's budget for CREATED accounts:
    #   the hit is reserved here (so parallel attempts cannot all slip past one
    #   check) and refunded unless an account was actually created. A wrong
    #   code, an "already registered", an upstream 502 or the per-email 429
    #   below creates nothing; brute force on the code is the per-email
    #   bucket's job.
    ip = client_ip(request)
    _enforce_ip_buckets(
        (_signup_attempt_ip_limiter, _signup_ip_limiter), ip, "signup"
    )
    created = False
    try:
        if not _signup_attempt_limiter.allow(email):
            logger.info(f"[signup-funnel] signup rate-limited email={email}")
            raise _rate_limited(
                _signup_attempt_limiter, email, "Too many attempts. Please try again later."
            )
        try:
            await client.register(email, payload.password, payload.verify_code.strip())
        except RegistrationError as e:
            # The refusal detail is already logged at the source (_post).
            raise HTTPException(status_code=400, detail=str(e))
        except RegistrationUpstreamError as e:
            logger.error(f"[signup-funnel] register upstream failure email={email}: {e}")
            raise HTTPException(
                status_code=502, detail="Sign-up service unavailable, try again"
            )
        created = True
    finally:
        # Also covers cancellation (client disconnect mid-call). Only the
        # creation bucket is refunded; refunding the attempt bucket would
        # remove the per-IP ceiling on failed attempts.
        if not created:
            _signup_ip_limiter.refund(ip)
    return {"success": True}


# The browser talks to NetMind DIRECTLY for the login step (emailLogin /
# OAuth) — when that call fails, the failure exists only in the user's tab
# and the server never learns a login was even attempted. That blind spot is
# exactly where "signed up but can't log in" (2026-08-01, Base
# recvre9LlfwXAP) disappeared into. This ingest lets the page report those
# client-side failures so the funnel is observable end to end.
#
# Log-only by design: no DB writes, no response body to probe, hard caps on
# every field, newlines stripped (log forging).
#
# Rate limiting is this endpoint's ONLY protection (it is auth-exempt by
# nature), and the resource it spends is the GLOBAL log — so the key cannot
# be caller-chosen alone. Three buckets, all must pass, listed in
# EVALUATION ORDER (the order is load-bearing, see the route):
#   * per-IP first: the one bucket whose key is trusted (edge-appended)
#     AND bounded (one attacker = one key) — it caps everything behind
#     it, including how many caller-chosen email keys can ever be
#     ALLOCATED (a new key under limit>0 is always allowed-and-allocated,
#     so this ordering is the email bucket's only key-space cap),
#   * per-email/IP second: the polite per-user cadence,
#   * global last: hard ceiling on total report lines per minute — the
#     backstop for what per-IP can't stop (an attacker rotating source
#     addresses).
# (Contrast send-code's per-email limiter: THERE the spent resource is
# per-email mail, so email is the right key. Here it is not.)
FUNNEL_REPORT_WINDOW_SECONDS = 60.0
# env-tunable like the signup buckets above — same reasoning: a typo in a
# deploy env must not take the backend down, and an operator may need to
# retune these under live abuse without a code change (2026-10-28 audit).
DEFAULT_FUNNEL_REPORT_PER_EMAIL_LIMIT = 10
DEFAULT_FUNNEL_REPORT_PER_IP_LIMIT = 30
# Backstop only — the per-IP bucket is the real limit. 120/min let four IPs
# starve everyone else's reports (2026-10-28 audit).
DEFAULT_FUNNEL_REPORT_GLOBAL_LIMIT = 2000
FUNNEL_REPORT_PER_EMAIL_LIMIT = env_limit(
    "NARRANEXUS_FUNNEL_REPORT_PER_EMAIL_LIMIT", DEFAULT_FUNNEL_REPORT_PER_EMAIL_LIMIT
)
FUNNEL_REPORT_PER_IP_LIMIT = env_limit(
    "NARRANEXUS_FUNNEL_REPORT_PER_IP_LIMIT", DEFAULT_FUNNEL_REPORT_PER_IP_LIMIT
)
FUNNEL_REPORT_GLOBAL_LIMIT = env_limit(
    "NARRANEXUS_FUNNEL_REPORT_GLOBAL_LIMIT", DEFAULT_FUNNEL_REPORT_GLOBAL_LIMIT
)
_funnel_report_limiter = SlidingWindowRateLimiter(
    limit=FUNNEL_REPORT_PER_EMAIL_LIMIT, window_sec=FUNNEL_REPORT_WINDOW_SECONDS
)
_funnel_report_ip_limiter = SlidingWindowRateLimiter(
    limit=FUNNEL_REPORT_PER_IP_LIMIT, window_sec=FUNNEL_REPORT_WINDOW_SECONDS
)
_funnel_report_global_limiter = SlidingWindowRateLimiter(
    limit=FUNNEL_REPORT_GLOBAL_LIMIT, window_sec=FUNNEL_REPORT_WINDOW_SECONDS
)

# Over-limit reports are dropped silently toward the USER (never stack an
# error on the failure they are already looking at) — but not toward OPS:
# a NetMind-wide OAuth outage over the limit must not read as "only 10
# people affected". One aggregate line per minute says how much was cut.
# last_log starts at import time so the very first drop after boot doesn't
# masquerade as a full window's summary.
_funnel_dropped: dict = {"count": 0, "last_log": monotonic()}

_FUNNEL_STAGES = frozenset({
    "netmind_email_login_failed",
    "netmind_oauth_failed",
    # Registration + forgot-password failures (browser->NetMind direct, so the
    # funnel is the only server-side trace). Frontend callers:
    # SignUpDialog.sendCode / useNetmindAuth.sendResetCode|resetPassword. Kept in
    # lockstep with the frontend `AuthFunnelStage` union (frontend/src/lib/api.ts).
    # This allowlist is a caller-controlled-input gate — never free text.
    "signup_send_code_failed",
    "netmind_reset_code_failed",
    "netmind_reset_password_failed",
})


def _note_dropped_report() -> None:
    _funnel_dropped["count"] += 1
    now = monotonic()
    span = now - _funnel_dropped["last_log"]
    if span >= 60.0:
        logger.warning(
            f"[login-funnel] dropped {_funnel_dropped['count']} client "
            f"report(s) over the rate limit in the last {span:.0f}s"
        )
        _funnel_dropped["count"] = 0
        _funnel_dropped["last_log"] = now


class FunnelReportRequest(BaseModel):
    stage: str = Field(min_length=1, max_length=40)
    email: Optional[EmailStr] = None
    detail: str = Field(default="", max_length=300)


@router.post("/funnel-report")
async def report_funnel_event(payload: FunnelReportRequest, request: Request) -> dict:
    """Record a client-side auth-funnel failure (fire-and-forget from the UI)."""
    if not is_power_login_enabled():
        raise HTTPException(status_code=404, detail="Not available in local mode")
    if payload.stage not in _FUNNEL_STAGES:
        raise HTTPException(status_code=400, detail="Unknown stage")
    email = (payload.email or "").strip().lower()
    ip = client_ip(request)
    # The order carries TWO invariants ("and" short-circuits):
    #   1. per-IP FIRST caps caller-chosen key ALLOCATION: a new email key
    #      under limit>0 is always allowed-and-allocated by the limiter
    #      (reject-path no-alloc only covers the limit<=0 degenerate), so
    #      the trusted bucket in front is the email bucket's ONLY key-space
    #      cap — at most 30 caller-chosen keys per IP per window instead of
    #      one per request. Reordering this reintroduces the unbounded
    #      key-growth regression this PR already paid for once.
    #   2. global LAST, so no single client can drain the shared budget
    #      with requests a narrower bucket was going to reject anyway.
    allowed = (
        _funnel_report_ip_limiter.allow(ip)
        and _funnel_report_limiter.allow(email or ip)
        and _funnel_report_global_limiter.allow("global")
    )
    if not allowed:
        # Silently accept toward the user; visibly count toward ops.
        _note_dropped_report()
        return {"success": True}
    detail = payload.detail.replace("\n", " ").replace("\r", " ").strip()
    logger.warning(
        f"[login-funnel] client stage={payload.stage} "
        f"email={email or '-'} detail={detail!r}"
    )
    return {"success": True}


def _free_credit_withheld(user_id: str, email: str) -> bool:
    """Whether this login must NOT receive the free-tier wallet and guide agent.

    The signup routes refuse throwaway domains, but they are not the only way
    in: NetMind's own signup is public (its auth URL ships in /config.js), and
    the free credit is handed out HERE, at login. So the blocklist is applied
    again at the point the credit is spent. The user still logs in and keeps
    their own NetMind card — they just get nothing free. Logs the domain only,
    stricter than the signup funnel (which logs the full address to trace one
    rejected attempt), because this check runs on EVERY login, not only on
    refused signups.
    """
    if not is_blocked_email(email):
        return False
    domain = email.rsplit("@", 1)[1].strip().lower()
    logger.warning(
        f"[login] free credit withheld: blocked email domain={domain} user={user_id}"
    )
    return True


async def _provision_providers(
    user_id: str, netmind_token: str, *, free_tier: bool = True
) -> None:
    """Register the user's provider cards — free tier first, then their own.

    The ORDER is the point; see the call site for the race it closes. Kept as a
    plain coroutine (rather than inlined in the scheduler below) so a test can
    await it and observe that order without racing a fire-and-forget task.

    Each step is independently non-fatal: a wallet-service outage must not stop
    the user's own NetMind card from being registered, and vice versa.
    ``free_tier=False`` (see :func:`_free_credit_withheld`) skips the wallet
    entirely — no wallet-service call at all — and only registers their own.
    """
    from backend.integrations.free_tier import provisioner as free_tier_mod
    from backend.integrations.netmind import netmind_provisioner as netmind

    if free_tier:
        try:
            await free_tier_mod.ensure_free_tier_provider(user_id)
        except Exception as e:  # noqa: BLE001 — retried on the next login
            logger.warning(f"[login] free-tier provisioning failed for {user_id}: {e!r}")
    try:
        await netmind.ensure_netmind_provider(user_id, netmind_token)
    except Exception as e:  # noqa: BLE001 — same
        logger.warning(f"[login] netmind provisioning failed for {user_id}: {e!r}")


def _schedule_provider_provisioning(
    user_id: str, netmind_token: str, *, free_tier: bool = True
) -> None:
    """Run :func:`_provision_providers` off the login path."""
    import asyncio

    try:
        task = asyncio.get_running_loop().create_task(
            _provision_providers(user_id, netmind_token, free_tier=free_tier)
        )
    except RuntimeError:
        return  # no loop (not the request path) — nothing to schedule
    # Incident lesson #2: a bare create_task swallows its exception at GC.
    task.add_done_callback(
        lambda t: t.cancelled() or t.exception() and logger.error(
            f"[login] provisioning task died for {user_id}: {t.exception()!r}"
        )
    )


def _guide_agent_feature_on() -> bool:
    """Whether this deployment auto-provisions the onboarding guide agent.

    Returned to the frontend on login/create-user so the "your first agent is
    already here" coachmark obeys the SAME kill-switch as the provisioning —
    otherwise pulling the server-side switch would leave the UI promising an
    agent that never appears.
    """
    from backend.onboarding.provisioning import is_guide_agent_enabled

    return is_guide_agent_enabled()


def _schedule_guide_agent_provisioning(user_id: str, *, is_new: bool) -> None:
    """Run the onboarding guide-agent provisioning off the login path.

    Fire-and-forget like :func:`_schedule_provider_provisioning`: a login must
    never block on, or fail from, it. `ensure_guide_agent` is idempotent
    (user-level write-once marker) and cheap on the warm path, so calling it
    on every login is fine — that is what lets existing zero-agent users pick
    up their guide on their next login, not just brand-new signups.

    ``is_new`` distinguishes a brand-new signup from a returning login: the
    backfill population (existing zero-agent users) has its own env brake
    (NARRANEXUS_ONBOARDING_GUIDE_BACKFILL) so a prod rollout can start with
    new signups only.
    """
    import asyncio

    from backend.onboarding.provisioning import (
        ensure_guide_agent,
        is_guide_agent_enabled,
    )

    if not is_guide_agent_enabled():
        return  # kill-switch: schedule nothing at all

    async def _run() -> None:
        db_client = await get_db_client()
        await ensure_guide_agent(db_client, user_id, is_new_user=is_new)

    try:
        task = asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        return  # no loop (not the request path) — nothing to schedule
    # Incident lesson #2: a bare create_task swallows its exception at GC.
    task.add_done_callback(
        lambda t: t.cancelled() or t.exception() and logger.error(
            f"[login] guide-agent provisioning died for {user_id}: {t.exception()!r}"
        )
    )


@router.post("/netmind-login", response_model=NetmindLoginResponse)
async def netmind_login(request: NetmindLoginRequest, http_request: Request):
    """Log in with a NetMind account token ("passport for visa" exchange).

    Available wherever Power login is enabled — always on the cloud server,
    and on a local deployment that opted in (NARRANEXUS_ENABLE_POWER_LOGIN).
    The frontend obtains a NetMind loginToken (embedded
    login form / OAuth popup / ?token= URL pass-through from netmind.ai
    or Arena), we verify it once against NetMind's auth API, lazily
    upsert the local user (user_id = NetMind userSystemCode) and issue
    NarraNexus's own JWT. Subsequent requests are validated locally —
    NetMind availability never becomes a per-request dependency.

    Error mapping: invalid/expired NetMind token -> 401; NetMind
    unreachable or contract drift -> 502 (never disguised as a user
    credential failure).
    """
    from backend.integrations.netmind.netmind_auth_client import (
        NetmindAuthError,
        NetmindUpstreamError,
    )

    if not is_power_login_enabled():
        raise HTTPException(status_code=404, detail="Not available in local mode")

    # Before the upstream call: a refused request costs NetMind nothing.
    _enforce_ip_buckets(
        (_netmind_login_ip_limiter,),
        client_ip(http_request),
        "netmind-login",
        funnel="login-funnel",
    )

    try:
        netmind_user = await _get_netmind_auth_client().verify_token(
            request.netmind_token
        )
    except NetmindAuthError as e:
        # The exception message carries upstream status + msg (never the
        # token). Without this line a "signed up but can't log in" report
        # is undiagnosable — the 401 count was all we had on 2026-08-01.
        logger.warning(
            f"[login-funnel] netmind-login rejected source={request.source or '-'}: {e}"
        )
        raise AuthError(NETMIND_TOKEN_INVALID, "Invalid NetMind token")
    except NetmindUpstreamError as exc:
        logger.error(f"netmind-login: upstream failure: {exc}")
        raise HTTPException(
            status_code=502, detail="NetMind auth service unavailable, try again"
        )

    db_client = await get_db_client()
    user_repo = UserRepository(db_client)
    user, is_new = await user_repo.upsert_netmind_user(
        user_system_code=netmind_user.user_system_code,
        email=netmind_user.email,
        display_name=netmind_user.nickname,
    )

    if is_new:
        try:
            await track(
                user_id=user.user_id,
                event=EVENT_SIGNED_UP,
                properties={PROP_METHOD: "netmind"},
                event_id=f"signed_up:{user.user_id}",
            )
        except Exception:  # noqa: BLE001 — analytics must never break login
            pass

    # Account-state gate + role, read CASE-SENSITIVELY.
    #
    # `user` (returned by upsert_netmind_user) is already the row fetched via
    # UserRepository.get_user — a `WHERE BINARY user_id` lookup — so
    # `user.status` is the case-sensitive account state, matching the collation
    # the suspension WRITE uses. A plain get_one here would use MySQL's default
    # case-INSENSITIVE collation and could let a look-alike user_id dodge the
    # gate. `role` is not carried on the User entity, so it is read separately
    # with the same `WHERE BINARY` collation.
    #
    # Fail-OPEN to "active" / "user" on any read hiccup (including a stored
    # status value the strong entity cast cannot coerce): a login must never
    # break because the state read stumbled. The gate exists to stop a specific
    # suspended account, not to become a login availability dependency.
    try:
        account_status = user.status.value
    except Exception:  # noqa: BLE001 — availability over strictness
        account_status = "active"
    try:
        role_rows = await db_client.execute(
            "SELECT role FROM users WHERE BINARY user_id = %s LIMIT 1",
            params=(user.user_id,),
            fetch=True,
        )
        role = (role_rows[0].get("role") if role_rows else None) or "user"
    except Exception:  # noqa: BLE001 — role read must never break login
        role = "user"

    # A suspended account must not be issued a token, and — just as important —
    # must not kick off the fire-and-forget login work below (session re-arm,
    # provider/quota provisioning): those are exactly the background side
    # effects a suspended account should stop consuming. Returning here
    # short-circuits every one of them. The state values are the shared opaque
    # set; this route holds no policy about how an account reaches one.
    if account_status in NON_TRANSACTING_USER_STATUSES:
        logger.warning(
            f"[login] refused suspended account user={user.user_id} "
            f"status={account_status} source={request.source or '-'}"
        )
        raise AuthError(ACCOUNT_SUSPENDED, "Account is not available", status_code=403)

    token = create_token(user.user_id, role)
    logger.info(
        f"netmind-login ok: user={user.user_id} new={is_new} "
        f"source={request.source or '-'}"
    )
    _schedule_login_rearm(user.user_id)

    # Provision the user's providers — free tier FIRST, then their own NetMind
    # account. Both are fire-and-forget (login must never block on, or fail
    # from, either) but they are chained rather than started side by side.
    #
    # Started independently they raced, and the race was silently harmful: each
    # provisioner reads "does this user already have a usable config" to decide
    # whether to bind the slots, and both read it before either had written. The
    # NetMind one finished second (it has to mint a key first) and rebound the
    # slots to a brand-new Power account with NO balance, so every agent call
    # failed with an opaque upstream error while a funded $10 wallet sat unused.
    #
    # Ordering free tier first is not arbitrary: it is the credential we know
    # works. A fresh Power account has no credit, and an existing funded one is
    # unaffected — the NetMind provisioner still registers-only when it finds a
    # complete config, which after chaining it actually does.
    #
    # A blocked (throwaway) email domain gets neither the wallet nor the guide
    # agent below — see _free_credit_withheld for why this gate lives at login.
    free_credit = not _free_credit_withheld(user.user_id, netmind_user.email or "")
    _schedule_provider_provisioning(
        user.user_id, request.netmind_token, free_tier=free_credit
    )

    # Onboarding guide agent — new users and existing zero-agent users get
    # their first companion. Behind the suspended-account gate above, so a
    # suspended login never provisions anything.
    if free_credit:
        _schedule_guide_agent_provisioning(user.user_id, is_new=is_new)

    return NetmindLoginResponse(
        success=True,
        user_id=user.user_id,
        token=token,
        role=role,
        is_new_user=is_new,
        # Same gate as the scheduling above: the coachmark must not promise a
        # guide agent that a withheld login will never get.
        guide_agent_provisioning=free_credit and _guide_agent_feature_on(),
        display_name=user.display_name,
        email=user.email,
    )


@router.get("/agents", response_model=AgentListResponse)
async def get_agents(request: Request):
    """
    Get the list of agents visible to the user. Identity from auth_middleware.

    Visibility rules:
    - Agents created by the user (created_by = user_id)
    - Agents set as public (is_public = 1)

    History: ``user_id`` used to be a Query param the client supplied
    directly. That let any client list any other user's owned agents by
    swapping the value, and (in cloud mode) bypass the JWT identity.
    Identity is now strictly derived from auth_middleware.
    """
    user_id = await resolve_current_user_id(request)
    logger.debug(f"Getting agents list for user: {user_id}")

    try:
        db_client = await get_db_client()

        query = """
            SELECT
                agent_id,
                agent_name,
                agent_description,
                agent_type,
                agent_create_time,
                created_by,
                is_public,
                agent_metadata
            FROM agents
            WHERE created_by = %s OR is_public = 1
            ORDER BY agent_create_time DESC
        """
        rows = await db_client.execute(query, (user_id,))

        # Phase C: bulk-fetch any active runs in one SELECT so we don't
        # do N+1 queries when the user has many agents.
        agent_ids = [row['agent_id'] for row in rows]
        active_runs_by_agent: dict[str, dict] = {}
        if agent_ids:
            placeholders = ",".join(["%s"] * len(agent_ids))
            try:
                # Chat-surface runs only. Since trigger runs (lark / team /
                # job / ...) are also recorded as state='running'
                # (run_recorder), an unfiltered SELECT would make ChatPanel
                # auto-attach the web chat to e.g. a Lark turn. 'chat' and
                # 'manyfold' are exactly the BackgroundRun surfaces this
                # enrichment always described; every other run is observable
                # through the WS observe endpoint instead.
                run_rows = await db_client.execute(
                    f"""
                    SELECT event_id, agent_id, state, started_at, last_event_at,
                           tool_call_count, current_stage
                    FROM events
                    WHERE state = 'running' AND user_id = %s
                      AND `trigger` IN ('chat', 'manyfold')
                      AND agent_id IN ({placeholders})
                    """,
                    (user_id, *agent_ids),
                )
                # Keep the latest started_at per agent if there are
                # somehow multiple (should not happen but defensive).
                for r in run_rows or []:
                    aid = r.get('agent_id')
                    if not aid:
                        continue
                    # Skip rows whose heartbeat has died — a dead run must
                    # not keep the sidebar avatar pulsing "running".
                    if not _run_is_live(r):
                        continue
                    existing = active_runs_by_agent.get(aid)
                    if existing is None or (r.get('started_at') or "") > (existing.get('started_at') or ""):
                        active_runs_by_agent[aid] = r
            except Exception as e:  # noqa: BLE001
                # Don't fail the whole listing because the active-run
                # enrichment broke — log and continue with no active_run
                # info attached.
                logger.warning(f"[/api/auth/agents] active_run enrichment failed: {e}")

        # NM sidebar preview — one window-function SELECT pulls the most
        # recent persisted assistant reply per agent in this list. Uses
        # ROW_NUMBER() so we get exactly one row per agent without an
        # N+1 sweep. Both SQLite (>=3.25) and MySQL 8+ support window
        # functions, which are the two backends auto_migrate ships.
        # final_output IS NOT NULL filters out events that crashed before
        # producing a reply; an empty string is treated the same since
        # the user wouldn't want "" rendered as preview.
        # Only the caller's OWN agents get a preview: a public agent's latest
        # reply belongs to whoever is talking to it, not to every viewer
        # (2026-10-28 — the list used to pull previews for public agents too).
        preview_agent_ids = [
            row["agent_id"] for row in rows if row.get("created_by") == user_id
        ]
        last_assistant_by_agent: dict[str, dict] = {}
        if preview_agent_ids:
            placeholders = ",".join(["%s"] * len(preview_agent_ids))
            try:
                preview_rows = await db_client.execute(
                    f"""
                    SELECT agent_id, final_output, created_at FROM (
                        SELECT agent_id, final_output, created_at,
                               ROW_NUMBER() OVER (
                                   PARTITION BY agent_id ORDER BY created_at DESC
                               ) AS rn
                        FROM events
                        WHERE agent_id IN ({placeholders})
                          AND final_output IS NOT NULL
                          AND final_output != ''
                          -- 1:1 sidebar preview only — exclude team group-chat
                          -- (message bus) replies. New runs are tagged at
                          -- creation (step_0_initialize); see the note below on
                          -- why historical rows can't be filtered reliably.
                          AND (`trigger` IS NULL OR `trigger` != 'message_bus')
                    ) ranked
                    WHERE rn = 1
                    """,
                    tuple(preview_agent_ids),
                )
                for r in preview_rows or []:
                    aid = r.get('agent_id')
                    if aid:
                        last_assistant_by_agent[aid] = r
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[/api/auth/agents] last_assistant enrichment failed: {e}"
                )

        # Effective agent-slot identity for the directory table, from the ONE
        # overlay the runtime uses (AgentSlotService → model_identity). Only the
        # caller's own agents are resolved: a public agent owned by someone
        # else reports nothing here. No literal framework fallback in this
        # route — the platform default lives in model_identity.
        llm_by_agent: dict[str, dict] = {
            aid: {"agent_framework": None, "model": None} for aid in agent_ids
        }
        if agent_ids:
            try:
                # Only the caller's own agents; the service re-checks ownership
                # itself, so a public agent in `agent_ids` comes back absent.
                overview = await AgentSlotService(db_client).owner_agents_overview(
                    user_id, agent_ids=agent_ids
                )
                for aid, slots_view in overview.items():
                    if aid not in llm_by_agent:
                        continue
                    agent_view = slots_view.get("agent") or {}
                    llm_by_agent[aid] = {
                        "agent_framework": agent_view.get("agent_framework"),
                        "model": agent_view.get("model") or None,
                    }
            except Exception as e:  # noqa: BLE001
                # Degrade to "unknown" rather than 500 the directory (that would
                # lock the app out at startup) — but this is NOT an expected
                # path, so it logs at error, not warning.
                logger.error(f"[/api/auth/agents] llm summary enrichment failed: {e}")

        # Channel presence for the directory table: one UNION query across the
        # binding sources (channel/binding_tables — one parameterised view of
        # channel_credentials per registered IM channel, so a new channel needs
        # no edit here), not one query per agent. Each branch carries the
        # source's on/off column so the UI can tell "configured" from "live".
        # Only owned agents participate: exposing a public agent's
        # integrations would leak private account metadata to viewers.
        binding_sources = channel_binding_sources()
        channel_order = [src.channel for src in binding_sources]
        bound_channels_by_agent: dict[str, list[BoundChannel]] = {
            aid: [] for aid in agent_ids
        }
        owned_agent_ids = [
            row["agent_id"] for row in rows if row.get("created_by") == user_id
        ]
        if owned_agent_ids:
            query, params = bound_channels_query(binding_sources, owned_agent_ids)
            try:
                channel_rows = await db_client.execute(query, params)
                # channel → active; a channel with any live row counts as live.
                channel_state: dict[str, dict[str, bool]] = {
                    aid: {} for aid in owned_agent_ids
                }
                for channel_row in channel_rows or []:
                    aid = channel_row.get("agent_id")
                    channel_name = channel_row.get("channel_name")
                    if aid not in channel_state or channel_name not in channel_order:
                        continue
                    active = bool(channel_row.get("active"))
                    channel_state[aid][channel_name] = channel_state[aid].get(channel_name, False) or active
                for aid, state in channel_state.items():
                    bound_channels_by_agent[aid] = [
                        BoundChannel(channel=channel, active=state[channel])
                        for channel in channel_order
                        if channel in state
                    ]
            except Exception as e:  # noqa: BLE001
                # Same shape as above: the column degrades to "—" for everyone,
                # which is exactly why it must be loud in the log.
                logger.error(
                    f"[/api/auth/agents] channel summary enrichment failed: {e}"
                )

        agents = []
        for row in rows:
            description = row.get('agent_description')
            # Check if Bootstrap.md exists for this agent (first-run setup pending).
            # NOTE: this is the FRONTEND-facing bootstrap_active — deliberately a
            # looser isfile-only rule (no event-count threshold), because a list
            # endpoint can't afford a per-agent COUNT query. It diverges from
            # narranexus.platform.bootstrap.lifecycle.is_bootstrap_active (the two
            # greeting writers' gate, which DOES apply the threshold) in the
            # narrow "over threshold but Bootstrap.md not yet auto-deleted"
            # window. Keep the two in mind together when touching either.
            bootstrap_active = False
            created_by = row.get('created_by')
            if created_by:
                from narranexus.platform.utils.workspace_paths import resolve_existing_workspace
                bootstrap_path = os.path.join(
                    str(resolve_existing_workspace(
                        row['agent_id'], created_by, app_settings.base_working_path
                    )),
                    "Bootstrap.md"
                )
                bootstrap_active = os.path.isfile(bootstrap_path)

            # Per-agent greeting override (only meaningful while bootstrapping).
            # The frontend's instant greeting must match the DB-persisted one,
            # so both read agent_metadata.bootstrap_greeting.
            bootstrap_greeting = None
            if bootstrap_active:
                raw_meta = row.get('agent_metadata')
                if raw_meta:
                    try:
                        meta = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)
                        bootstrap_greeting = (meta or {}).get('bootstrap_greeting')
                    except (ValueError, TypeError):
                        bootstrap_greeting = None

            active_run = None
            ar_row = active_runs_by_agent.get(row['agent_id'])
            if ar_row:
                active_run = ActiveRunInfo(
                    run_id=ar_row.get('event_id') or "",
                    state=ar_row.get('state') or "running",
                    started_at=format_for_api(ar_row.get('started_at')),
                    last_event_at=format_for_api(ar_row.get('last_event_at')),
                    tool_call_count=int(ar_row.get('tool_call_count') or 0),
                    current_stage=ar_row.get('current_stage') or None,
                )

            # NM sidebar preview — flatten whitespace and truncate so the
            # wire payload stays bounded. Frontend may slice further for
            # its row width, but 200 chars covers both alphabetic and
            # CJK width comfortably without bloating the response.
            last_assistant_preview = None
            last_assistant_at = None
            la_row = last_assistant_by_agent.get(row['agent_id'])
            if la_row:
                raw = la_row.get('final_output') or ""
                if raw:
                    flat = " ".join(raw.split())
                    last_assistant_preview = (
                        flat[:200] if len(flat) <= 200 else flat[:200].rstrip() + "…"
                    )
                last_assistant_at = format_for_api(la_row.get('created_at'))

            agent_info = AgentInfo(
                agent_id=row['agent_id'],
                name=row.get('agent_name') or row['agent_id'],
                description=description[:200] + '...' if description and len(description) > 200 else description,
                status='active',
                created_at=format_for_api(row.get('agent_create_time')),
                is_public=bool(row.get('is_public', 0)),
                created_by=created_by,
                agent_framework=llm_by_agent[row['agent_id']]["agent_framework"],
                model=llm_by_agent[row['agent_id']]["model"],
                bound_channels=bound_channels_by_agent[row['agent_id']],
                bootstrap_active=bootstrap_active,
                bootstrap_greeting=bootstrap_greeting,
                active_run=active_run,
                last_assistant_preview=last_assistant_preview,
                last_assistant_at=last_assistant_at,
            )
            agents.append(agent_info)

        # First-paint ordering — most-recently-active conversation on top, so
        # the sidebar is already sorted before the frontend hydrates its local
        # sessions. Activity = latest of (last assistant reply, agent creation).
        # format_for_api emits fixed-width ISO-UTC ("...Z"), which sorts
        # lexically, so plain string max/compare is correct here. The frontend
        # re-sorts with the same rule PLUS fresh local session activity (see
        # sortAgentsByActivity); this is only the pre-hydration baseline.
        # Two stable passes: agent_id asc as the tie-breaker, then activity
        # desc — matching the frontend's deterministic ordering.
        agents.sort(key=lambda a: a.agent_id)
        agents.sort(
            key=lambda a: max(a.last_assistant_at or "", a.created_at or ""),
            reverse=True,
        )

        logger.debug(f"Found {len(agents)} agents for user {user_id}")

        return AgentListResponse(
            success=True,
            agents=agents,
            count=len(agents),
        )

    except Exception as e:
        logger.exception(f"Error getting agents: {e}")
        return AgentListResponse(
            success=False,
            error=str(e)
        )


async def _mark_first_agent_created(db_client, user_id: str) -> None:
    """Creating an agent through the agents API completes the first-agent
    onboarding step server-side; the welcome flow must not depend on the
    frontend remembering to post it. Other creation doors (bundle import,
    team install, social-network provisioning) do not mark it yet — a user
    whose first agent arrives that way still sees the step. Best-effort,
    never fails the create."""
    try:
        user_repo = UserRepository(db_client)
        user = await user_repo.get_user(user_id)
        if not user:
            return
        current = _read_onboarding(user.metadata)
        if current.first_agent_created:
            return
        metadata = dict(user.metadata or {})
        metadata[_ONBOARDING_METADATA_KEY] = current.model_copy(update={"first_agent_created": True}).model_dump()
        await user_repo.update_user(user_id, {"metadata": metadata})
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"onboarding first_agent_created not recorded for {user_id}: {exc}")


@router.post("/agents", response_model=CreateAgentResponse)
async def create_agent(http_request: Request, request: CreateAgentRequest):
    """
    Create a new agent with default values.
    Generates a unique agent_id automatically. Identity (created_by) comes
    from auth_middleware — clients can no longer create agents under
    someone else's account by writing a different id into the body.
    """
    created_by = await resolve_current_user_id(http_request)
    logger.info(f"Creating new agent for user: {created_by}")

    try:
        db_client = await get_db_client()

        # Validate that the user exists
        user_repo = UserRepository(db_client)
        user = await user_repo.get_user(created_by)
        if not user:
            logger.warning(f"Cannot create agent: user {created_by} not found")
            return CreateAgentResponse(
                success=False,
                error="User not found. Please create an account first."
            )

        # Generate unique agent_id
        agent_id = f"agent_{uuid4().hex[:12]}"

        # Set default name if not provided. Normalized FIRST: "   " is truthy,
        # so an `or` on the raw value lets whitespace through as the name and
        # the default never fires — the row then renders a blank sidebar title
        # (worse than the agent_id fallback, which at least identifies it).
        from narranexus.platform.bootstrap.template import PLACEHOLDER_AGENT_NAME

        agent_name = normalize_agent_text(request.agent_name) or PLACEHOLDER_AGENT_NAME
        # No placeholder: an agent with nothing said about it yet has an EMPTY
        # description. The old filler ("A new agent ready for configuration")
        # was snapshotted into the bus registry and reported to peers as fact,
        # so a configured agent looked unconfigured and askers refused to send
        # (P1 section 02). Peers now see the name + machine-derived capabilities, and
        # the agent records a real description during bootstrap.
        agent_description = normalize_agent_text(request.agent_description)

        # Provisioning (agent row + default instances + peer-discovery
        # registration + bootstrap profile + default skills) is the
        # canonical sequence in `provision_new_agent` — this route is its
        # semantic source. `bootstrap` in the request
        # picks a profile; unknown/None falls back to "default" inside
        # `get_profile`. This route stays the SEMANTIC SOURCE the seam mirrors;
        # everything below that ISN'T the shared sequence (team assignment #43,
        # response shape) stays here.
        from narranexus.platform.bootstrap.provision import provision_new_agent
        provision_result = await provision_new_agent(
            db_client,
            agent_id=agent_id,
            user_id=created_by,
            agent_name=agent_name,
            agent_description=agent_description,
            bootstrap_profile=getattr(request, "bootstrap", None) or "default",
        )
        logger.info(f"Agent created: {agent_id}")

        # Team assignment (#43): when the sidebar "Add agent" was clicked under
        # a specific team, attach the new agent to that team so it lands in the
        # right group instead of Ungrouped. Best-effort + ownership-checked: a
        # missing or foreign team_id never blocks creation — it just leaves the
        # agent ungrouped.
        if request.team_id:
            try:
                from narranexus.platform.repository import (
                    TeamRepository,
                    TeamMemberRepository,
                )
                team = await TeamRepository(db_client).get_team(request.team_id)
                if team and team.owner_user_id == created_by:
                    await TeamMemberRepository(db_client).add_member(
                        request.team_id, agent_id
                    )
                    logger.info(f"Agent {agent_id} added to team {request.team_id}")
                else:
                    logger.warning(
                        f"Skip team assignment for {agent_id}: team "
                        f"{request.team_id} missing or not owned by {created_by}"
                    )
            except Exception as team_err:
                logger.warning(f"Failed to assign {agent_id} to team: {team_err}")

        # Default skills install is scheduled inside `provision_new_agent`
        # (fire-and-forget, same shape as before this extraction).

        # Return the created agent info
        # Re-fetch from DB to get server-generated fields (created_at)
        agent_row = await db_client.get_one("agents", {"agent_id": agent_id})
        agent_info = AgentInfo(
            agent_id=agent_id,
            name=agent_name,
            description=agent_description,
            status='active',
            created_at=format_for_api(agent_row.get("agent_create_time")) if agent_row else None,
            created_by=created_by,
            bootstrap_active=provision_result.bootstrap_active,
        )
        await _mark_first_agent_created(db_client, created_by)

        return CreateAgentResponse(
            success=True,
            agent=agent_info,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error creating agent: {e}")
        return CreateAgentResponse(
            success=False,
            error=str(e)
        )


def _not_persisted(fields: Iterable[str]) -> str:
    """The one wording for "the row is not what you asked for".

    Two checks reach it — the shared transaction's "did my write land" and this
    route's "is the row what the caller requested" (they are different
    questions; see update_agent). The user-facing sentence is the same one, and
    duplicating the literal is how the two drift into two phrasings for one
    failure.
    """
    # Sorts here rather than trusting each caller to: the helper exists so the
    # two checks cannot drift into two sentences, and ordering is part of the
    # sentence.
    return f"The update did not persist: {', '.join(sorted(fields))}"


@router.put("/agents/{agent_id}", response_model=UpdateAgentResponse)
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    http_request: Request,
):
    """
    Update agent information (name, description)

    Ownership: in cloud mode (JWT-bound user_id on ``request.state``),
    only the creator may update. Local mode (no auth middleware) lets
    everything through — same model as the Slack/Telegram bind routes.
    """
    logger.info(f"Updating agent: {agent_id}")

    try:
        db_client = await get_db_client()
        repo = AgentRepository(db_client)

        # Check if the agent exists
        agent = await repo.get_agent(agent_id)
        if not agent:
            return UpdateAgentResponse(
                success=False,
                error=f"Agent {agent_id} not found"
            )

        # Ownership check: parallel to DELETE /agents/{agent_id} below
        # and to ``_verify_agent_ownership`` in the IM routes. Mutating
        # someone else's agent name from inside the same workspace was
        # the actual gap — DELETE was guarded, PUT was not.
        user_id = getattr(http_request.state, "user_id", None) or None
        if user_id and agent.created_by != user_id:
            return UpdateAgentResponse(
                success=False,
                error="Permission denied: only the creator can update this agent.",
            )

        request = body  # preserve old local var name in body below

        # What the caller asked for, in the form it will be STORED in —
        # `normalize_agent_text` on the way in, so "already equal" below and
        # "what the row holds" afterwards can never mean different things.
        requested: dict = {}
        if request.agent_name is not None:
            requested["agent_name"] = normalize_agent_text(request.agent_name)
        if request.agent_description is not None:
            requested["agent_description"] = normalize_agent_text(
                request.agent_description
            )
        if request.is_public is not None:
            requested["is_public"] = int(request.is_public)

        if not requested:
            return UpdateAgentResponse(
                success=False,
                error="No fields to update"
            )

        # An agent with no name falls back to rendering its raw agent_id in
        # every surface. The awareness tool has always refused this; the HTTP
        # route accepted it, so the same input was rejected on one path and
        # stored on the other. Whitespace-only is the same request — it is
        # caught here rather than by a schema `min_length`, which "  " passes.
        if "agent_name" in requested and not requested["agent_name"]:
            return UpdateAgentResponse(
                success=False,
                error="Agent name cannot be empty",
            )

        # One shared rename transaction, not a hand-rolled column write.
        # Setting agents.agent_name is only a THIRD of a rename: the Awareness
        # profile carries a platform-voiced record of the agent's identity, and
        # the peer directory carries the name other agents see. This route used
        # to do the first and the third and skip the second, so a UI rename left
        # a correction behind that still named the PREVIOUS name — and the agent
        # read it and kept introducing itself that way (Shenzhen round 2, P1,
        # prod agent_4a0ae5f40af2: the row said 「小绿」, the profile said "You
        # are 「美食家」", and it answered 美食家 twice). Everything the old inline
        # block did — normalize, value-equality short-circuit, re-read instead
        # of rowcount, unconditional directory refresh — now lives in
        # apply_agent_profile_change, shared with the agent's own tool and the
        # Manyfold route, so no writer can be one step out of date again.
        #
        # is_public rides along as an extra: it carries no identity meaning, but
        # putting it in the same call keeps the row write single rather than
        # opening a window where the row is half-updated.
        result = await apply_agent_profile_change(
            db_client,
            agent_id,
            new_name=requested.get("agent_name"),
            new_description=requested.get("agent_description"),
            # None means "not passed"; False is a value, so read the presence of
            # the key rather than its truthiness.
            is_public=(
                bool(requested["is_public"]) if "is_public" in requested else None
            ),
        )

        if not result.ok:
            # The shared transaction speaks to models; this route speaks to a
            # UI. Map on the structural reason, never on the sentence.
            if result.error_kind == "not_found":
                return UpdateAgentResponse(
                    success=False,
                    error=f"Agent {agent_id} not found",
                )
            if result.error_kind == "not_applied":
                return UpdateAgentResponse(
                    success=False,
                    error=_not_persisted(result.unapplied_fields),
                )
            # Every remaining kind gets a sentence written for this route's
            # reader. `result.error` is composed for a MODEL — "Error: new_name
            # is too long (max 255 characters)" reads as a leaked internal
            # string in a dialog — and the two audiences drift the moment either
            # is reworded. `empty_name` and `too_long` are unreachable through
            # this route's own validation above and the schema's max_length, but
            # a mapping that depends on a caller's other guards is a mapping
            # that breaks when one is relaxed.
            return UpdateAgentResponse(
                success=False,
                error={
                    "empty_name": "Agent name cannot be empty",
                    "too_long": (
                        f"Name and description are limited to "
                        f"{AGENT_TEXT_MAX_LENGTH} characters"
                    ),
                    "nothing_to_update": "No fields to update",
                }.get(result.error_kind or "", "The update could not be applied"),
            )

        # The row is the response. Whether it needed a write ("updated") or
        # already held the values ("unchanged"), the state the caller asked for
        # is the state that exists — both are success.
        updated_agent = await repo.get_agent(agent_id)
        if not updated_agent:
            return UpdateAgentResponse(
                success=False,
                error=f"Agent {agent_id} disappeared while being updated",
            )

        # The transaction verified that what it WROTE landed. That is not the
        # same question this route owes its caller, which is whether the row is
        # now in the state they asked for — a field that compared equal at read
        # time issued no write and so was never on the transaction's list, yet a
        # concurrent writer (a second tab, or the agent's own
        # update_agent_profile) can have moved it inside the window. Benign
        # last-write-wins, but the caller is still told no, because the row
        # genuinely is not what they requested. WARNING, not ERROR: there is no
        # CAS across read-write-reread, so this must not page anyone.
        unapplied = [
            field
            for field, wanted in requested.items()
            if not agent_field_matches(updated_agent, field, wanted)
        ]
        if unapplied:
            logger.warning(
                f"Agent {agent_id} does not hold the requested values for "
                f"{unapplied} after the write — concurrent overwrite, or the "
                f"write did not land"
            )
            return UpdateAgentResponse(
                success=False,
                error=_not_persisted(unapplied),
            )

        # Check bootstrap_active (Bootstrap.md exists in workspace). Frontend-facing,
        # isfile-only rule (no threshold) — see the /api/auth/agents note above and
        # narranexus.platform.bootstrap.lifecycle.is_bootstrap_active (the writers' gate).
        from narranexus.platform.settings import settings
        from narranexus.platform.utils.workspace_paths import resolve_existing_workspace
        workspace_path = str(resolve_existing_workspace(
            agent_id, updated_agent.created_by, settings.base_working_path
        ))
        bootstrap_active = os.path.isfile(os.path.join(workspace_path, "Bootstrap.md"))

        agent_info = AgentInfo(
            agent_id=updated_agent.agent_id,
            name=updated_agent.agent_name,
            description=updated_agent.agent_description,
            status='active',
            created_at=format_for_api(updated_agent.agent_create_time),
            is_public=updated_agent.is_public,
            created_by=updated_agent.created_by,
            bootstrap_active=bootstrap_active,
        )
        logger.info(
            f"Agent {agent_id} now holds the requested values "
            f"({'written' if result.status == 'updated' else 'already stored'})"
        )

        return UpdateAgentResponse(
            success=True,
            agent=agent_info,
            # Surfaced, not swallowed: the shared transaction computes it and
            # the agent-facing tool has always reported it, but this route used
            # to drop it — leaving the UI as the one rename path where handing
            # a name to a second agent happens silently.
            name_clash_with=result.name_clash_with,
            identity_record_updated=result.identity_record_updated,
        )

    except Exception as e:
        logger.exception(f"Error updating agent: {e}")
        return UpdateAgentResponse(
            success=False,
            error=str(e)
        )


@router.delete("/agents/{agent_id}", response_model=DeleteAgentResponse)
async def delete_agent(
    agent_id: str,
    request: Request,
):
    """
    Cascade delete an Agent and all its associated data

    Permission: Only the Agent creator (created_by == user_id) can delete.
    Identity comes from auth_middleware — the old "operator's user_id"
    query param was directly compared against ``agent.created_by``,
    which let a client pass any value (including the real creator's
    user_id) to pass the permission check and nuke someone else's agent.
    Now ``user_id`` is the JWT/X-User-Id identity and unforgeable.

    Deletion order is from leaf to root to ensure foreign key safety:
    1. Instance Memory dynamic tables
    2. Narrative Memory dynamic tables
    3. Jobs + resumable CLI session handles
    4. Instance-Narrative Links
    5. Instance subsidiary data (social_entities, awareness, module_report_memory)
    6. Module Instances
    7. Events
    8. Narratives
    9. MCP URLs
    10. Agent Messages
    11. The Agent itself
    """
    user_id = await resolve_current_user_id(request)
    logger.info(f"Delete agent request: agent_id={agent_id}, user_id={user_id}")

    try:
        db_client = await get_db_client()
        repo = AgentRepository(db_client)

        # 1. Permission check: only the creator can delete
        agent = await repo.get_agent(agent_id)
        if not agent:
            return DeleteAgentResponse(
                success=False,
                agent_id=agent_id,
                error=f"Agent {agent_id} not found",
            )

        if agent.created_by != user_id:
            return DeleteAgentResponse(
                success=False,
                agent_id=agent_id,
                error="Permission denied: only the creator can delete this agent",
            )

        stats: dict[str, int] = {}

        # 2. Collect all associated instance_ids
        inst_rows = await db_client.execute(
            "SELECT instance_id FROM module_instances WHERE agent_id = %s",
            (agent_id,),
            fetch=True,
        )
        instance_ids = [r["instance_id"] for r in inst_rows] if inst_rows else []

        # 3. Collect all associated narrative_ids
        nar_rows = await db_client.execute(
            "SELECT narrative_id FROM narratives WHERE agent_id = %s",
            (agent_id,),
            fetch=True,
        )
        narrative_ids = [r["narrative_id"] for r in nar_rows] if nar_rows else []

        # 4. Discover dynamic Memory tables (compatible with both MySQL and SQLite)
        is_sqlite = hasattr(db_client, '_backend') and db_client._backend and db_client._backend.dialect == "sqlite"
        if is_sqlite:
            mem_rows = await db_client.execute(
                """
                SELECT name AS tbl FROM sqlite_master
                WHERE type='table'
                  AND (name LIKE 'json_format_event_memory_%'
                       OR name LIKE 'instance_json_format_memory_%')
                """,
                params=(),
                fetch=True,
            )
        else:
            mem_rows = await db_client.execute(
                """
                SELECT TABLE_NAME AS tbl FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND (TABLE_NAME LIKE 'json_format_event_memory_%%'
                       OR TABLE_NAME LIKE 'instance_json_format_memory_%%')
                """,
                params=(),
                fetch=True,
            )
        memory_tables = [r["tbl"] for r in mem_rows] if mem_rows else []

        # ========== Delete from leaf to root ==========

        # 4a. Instance Memory dynamic tables (by instance_id)
        if instance_ids:
            ph = ", ".join(["%s"] * len(instance_ids))
            for tbl in memory_tables:
                if tbl.startswith("instance_json_format_memory_"):
                    result = await db_client.execute(
                        f"DELETE FROM `{tbl}` WHERE instance_id IN ({ph})",
                        tuple(instance_ids),
                        fetch=False,
                    )
                    cnt = result if isinstance(result, int) else 0
                    if cnt > 0:
                        stats[tbl] = cnt

        # 4b. Narrative Memory dynamic tables (by narrative_id)
        if narrative_ids:
            ph_n = ", ".join(["%s"] * len(narrative_ids))
            for tbl in memory_tables:
                if tbl.startswith("json_format_event_memory_"):
                    result = await db_client.execute(
                        f"DELETE FROM `{tbl}` WHERE narrative_id IN ({ph_n})",
                        tuple(narrative_ids),
                        fetch=False,
                    )
                    cnt = result if isinstance(result, int) else 0
                    if cnt > 0:
                        stats[tbl] = cnt

        # 5. Jobs (by agent_id)
        result = await db_client.execute(
            "DELETE FROM instance_jobs WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["instance_jobs"] = cnt

        # NOTE (2026-07-29): step 5b used to prune `agent_cli_sessions` here,
        # because a stored CLI session handle outlived both the agent and its
        # workspace and a recycled agent_id would inherit one pointing at a dead
        # transcript. That table is gone — the adapter writes the CLI transcript
        # per turn and deletes it when the turn ends, so nothing durable is left
        # keyed on an agent_id. Deleting from it here would now raise.

        # 6. Instance-Narrative Links (by instance_id)
        if instance_ids:
            ph = ", ".join(["%s"] * len(instance_ids))
            result = await db_client.execute(
                f"DELETE FROM instance_narrative_links WHERE instance_id IN ({ph})",
                tuple(instance_ids),
                fetch=False,
            )
            cnt = result if isinstance(result, int) else 0
            if cnt > 0:
                stats["instance_narrative_links"] = cnt

        # 7. Instance subsidiary data (by instance_id)
        #     (instance_social_entities retired — entities now live in
        #      memory_entity, cleaned by agent_id in the memory sweep below.)
        instance_sub_tables = [
            "instance_awareness",
            "instance_module_report_memory",
            "instance_json_format_memory",
            # Was missing — separate single-row-per-instance memory table
            # used by the chat-format memory writer.
            "instance_json_format_memory_chat",
            # `module_report_memory` is also keyed by instance_id (per-instance
            # report blob, distinct from instance_module_report_memory).
            "module_report_memory",
        ]
        if instance_ids:
            ph = ", ".join(["%s"] * len(instance_ids))
            for sub_tbl in instance_sub_tables:
                try:
                    result = await db_client.execute(
                        f"DELETE FROM `{sub_tbl}` WHERE instance_id IN ({ph})",
                        tuple(instance_ids),
                        fetch=False,
                    )
                    cnt = result if isinstance(result, int) else 0
                    if cnt > 0:
                        stats[sub_tbl] = cnt
                except Exception:
                    # Table may not exist, skip
                    pass

        # 7b. Unified memory tables (by agent_id) — observation/entity/chat/...
        # are all agent-scoped; without this an account deletion would leave
        # orphaned memory rows (entities, learned facts, etc.).
        from narranexus.platform.utils.db.schema_registry import MEMORY_KINDS
        for _kind in MEMORY_KINDS:
            _tbl = f"memory_{_kind}"
            try:
                result = await db_client.execute(
                    f"DELETE FROM `{_tbl}` WHERE agent_id = %s",
                    (agent_id,),
                    fetch=False,
                )
                cnt = result if isinstance(result, int) else 0
                if cnt > 0:
                    stats[_tbl] = cnt
            except Exception:
                pass  # Table may not exist on older DBs — skip.

        # 7c. Memory consolidation queue (by agent_id) — without this the
        # consolidation worker's idle trigger reprocesses the dead agent's
        # scopes on every poll, logging "no owner row" warnings forever.
        result = await db_client.execute(
            "DELETE FROM memory_consolidation_queue WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["memory_consolidation_queue"] = cnt

        # 8. Module Instances (by agent_id)
        result = await db_client.execute(
            "DELETE FROM module_instances WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["module_instances"] = cnt

        # 9. Events (by agent_id)
        result = await db_client.execute(
            "DELETE FROM events WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["events"] = cnt

        # 10. Narratives (by agent_id)
        result = await db_client.execute(
            "DELETE FROM narratives WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["narratives"] = cnt

        # 11. MCP URLs (by agent_id)
        result = await db_client.execute(
            "DELETE FROM mcp_urls WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["mcp_urls"] = cnt

        # 12. Agent Messages (by agent_id)
        try:
            result = await db_client.execute(
                "DELETE FROM agent_messages WHERE agent_id = %s",
                (agent_id,),
                fetch=False,
            )
            cnt = result if isinstance(result, int) else 0
            if cnt > 0:
                stats["agent_messages"] = cnt
        except Exception:
            pass

        # 13. Workspace directory
        try:
            import os
            import shutil
            from narranexus.platform.settings import settings
            from narranexus.platform.utils.workspace_paths import resolve_existing_workspace
            workspace_path = str(
                resolve_existing_workspace(
                    agent_id, agent.created_by, settings.base_working_path
                )
            )
            if os.path.isdir(workspace_path):
                shutil.rmtree(workspace_path)
                stats["workspace_dir"] = 1
                logger.info(f"Deleted workspace: {workspace_path}")
        except Exception as e:
            logger.warning(f"Workspace cleanup failed (non-critical): {e}")

        # 14. Channel cleanups — registry-driven walk over every
        # ChannelModuleBase subclass in module_registry. Each subclass owns
        # its own cleanup_for_agent (default: credential row + inbox
        # channels by channel_id prefix; Lark overrides to also drop
        # CLI profile + workspace dir). Adding a new IM channel requires
        # zero edits here.
        try:
            from narranexus.platform.channel.channel_module_base import (
                ChannelModuleBase,
            )
            from narranexus.platform.module_system import module_registry

            for module_name, cls in module_registry.items():
                if not (isinstance(cls, type) and issubclass(cls, ChannelModuleBase)):
                    continue
                try:
                    mod = cls(
                        agent_id=agent_id,
                        user_id=user_id,
                        database_client=db_client,
                    )
                    result_stats = await mod.cleanup_for_agent(agent_id, db_client)
                    stats.update(result_stats)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"Channel cleanup for {module_name} failed (non-critical): {e}"
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Channel cleanup walk failed (non-critical): {e}")

        # 14a. Retired per-channel credential tables — ONE distribution-independent
        # sweep. The walk above only visits channels whose plugin this
        # distribution installs and registry.json has not disabled; a channel
        # that was dropped still has its pre-cutover `lark_credentials` /
        # `channel_*_credentials` rows (base64 bot tokens, app secrets) in the
        # database, and nothing else would ever delete them. `LEGACY_TABLES` is
        # the single source of truth for which tables ever held a channel secret,
        # so this is keyed on agent_id alone and needs no descriptor. Overlaps
        # harmlessly with the per-channel purge (the second DELETE matches
        # nothing); best-effort per table, never fails the deletion.
        try:
            from narranexus.platform.channel.credential_legacy import purge_legacy_for_agent

            stats.update(await purge_legacy_for_agent(db_client, agent_id))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Legacy channel-credential purge failed (non-critical): {e}")

        # 14b. team_members (subproject 1) — drop this agent from every team it's a member of.
        # Without this, the team panel keeps showing the deleted agent_id as a ghost member.
        try:
            result = await db_client.execute(
                "DELETE FROM team_members WHERE agent_id = %s",
                (agent_id,), fetch=False,
            )
            cnt = result if isinstance(result, int) else 0
            if cnt > 0:
                stats["team_members"] = cnt
        except Exception as e:
            logger.warning(f"team_members cleanup failed: {e}")

        # 14c. Full message-bus cascade (the existing block only cleaned `lark_*` channels).
        # Strategy:
        #   - Pull every channel where this agent is a member (any prefix).
        #   - Remove this agent's membership.
        #   - For channels created_by this agent, OR channels whose membership is
        #     now empty, blow away the channel + its messages.
        #   - Drop diagnostic / registry rows for this agent.
        try:
            members_for_self = await db_client.get("bus_channel_members", {"agent_id": agent_id})
            channels_touched = {m.get("channel_id") for m in members_for_self if m.get("channel_id")}

            # Remove this agent's membership rows
            mres = await db_client.execute(
                "DELETE FROM bus_channel_members WHERE agent_id = %s",
                (agent_id,), fetch=False,
            )
            mcnt = mres if isinstance(mres, int) else 0
            if mcnt > 0:
                stats["bus_channel_members"] = mcnt

            # For each touched channel: if no members remain OR the agent created it, delete the channel + messages
            killed_channels: list[str] = []
            for cid in channels_touched:
                ch_row = await db_client.get_one("bus_channels", {"channel_id": cid})
                remaining = await db_client.get("bus_channel_members", {"channel_id": cid})
                creator_match = ch_row and ch_row.get("created_by") == agent_id
                if not remaining or creator_match:
                    killed_channels.append(cid)

            if killed_channels:
                ph = ", ".join(["%s"] * len(killed_channels))
                msgs_res = await db_client.execute(
                    f"DELETE FROM bus_messages WHERE channel_id IN ({ph})",
                    tuple(killed_channels), fetch=False,
                )
                msgs_cnt = msgs_res if isinstance(msgs_res, int) else 0
                if msgs_cnt > 0:
                    stats["bus_messages"] = stats.get("bus_messages", 0) + msgs_cnt

                # Also drop their remaining members (if any) — channel is gone
                others_res = await db_client.execute(
                    f"DELETE FROM bus_channel_members WHERE channel_id IN ({ph})",
                    tuple(killed_channels), fetch=False,
                )
                others_cnt = others_res if isinstance(others_res, int) else 0
                if others_cnt > 0:
                    stats["bus_channel_members"] = stats.get("bus_channel_members", 0) + others_cnt

                ch_res = await db_client.execute(
                    f"DELETE FROM bus_channels WHERE channel_id IN ({ph})",
                    tuple(killed_channels), fetch=False,
                )
                ch_cnt = ch_res if isinstance(ch_res, int) else 0
                if ch_cnt > 0:
                    stats["bus_channels"] = ch_cnt

            # Registry: agent's "I'm alive" advertisement on the bus
            reg_res = await db_client.execute(
                "DELETE FROM bus_agent_registry WHERE agent_id = %s",
                (agent_id,), fetch=False,
            )
            reg_cnt = reg_res if isinstance(reg_res, int) else 0
            if reg_cnt > 0:
                stats["bus_agent_registry"] = reg_cnt

            # Diagnostic: per-(message, agent) failure log
            mf_res = await db_client.execute(
                "DELETE FROM bus_message_failures WHERE agent_id = %s",
                (agent_id,), fetch=False,
            )
            mf_cnt = mf_res if isinstance(mf_res, int) else 0
            if mf_cnt > 0:
                stats["bus_message_failures"] = mf_cnt
        except Exception as e:
            logger.warning(f"bus cascade cleanup failed (non-critical): {e}")

        # 14e. Orphan inbox entries pointing at deleted events
        try:
            if is_sqlite:
                ib_sql = (
                    "DELETE FROM inbox_table WHERE event_id IS NOT NULL "
                    "AND event_id NOT IN (SELECT event_id FROM events)"
                )
            else:
                ib_sql = (
                    "DELETE FROM inbox_table WHERE event_id IS NOT NULL "
                    "AND event_id NOT IN (SELECT event_id FROM events)"
                )
            ib_res = await db_client.execute(ib_sql, (), fetch=False)
            ib_cnt = ib_res if isinstance(ib_res, int) else 0
            if ib_cnt > 0:
                stats["inbox_table_orphans"] = ib_cnt
        except Exception as e:
            logger.warning(f"inbox orphan sweep failed (non-critical): {e}")

        # 14f. Per-agent LLM slot overrides (agent_slots). Keyed by agent_id;
        # without this, a deleted agent's override rows linger as orphans.
        try:
            as_res = await db_client.execute(
                "DELETE FROM agent_slots WHERE agent_id = %s",
                (agent_id,), fetch=False,
            )
            as_cnt = as_res if isinstance(as_res, int) else 0
            if as_cnt > 0:
                stats["agent_slots"] = as_cnt
        except Exception as e:
            logger.warning(f"agent_slots cascade cleanup failed (non-critical): {e}")

        # 15. The Agent itself
        result = await db_client.execute(
            "DELETE FROM agents WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["agents"] = cnt

        total = sum(stats.values())
        logger.info(f"Agent {agent_id} deleted, total {total} rows removed: {stats}")

        return DeleteAgentResponse(
            success=True,
            agent_id=agent_id,
            deleted_counts=stats,
        )

    except Exception as e:
        logger.exception(f"Error deleting agent {agent_id}: {e}")
        return DeleteAgentResponse(
            success=False,
            agent_id=agent_id,
            error=str(e),
        )


@router.post("/create-user", response_model=CreateUserResponse)
async def create_user(request: CreateUserRequest):
    """
    Create a new local user (local mode only).

    In cloud mode this endpoint is gone: it sat in AUTH_EXEMPT_PATHS with
    no credential check, i.e. an open account-creation hole. Cloud users
    are provisioned via netmind-login.
    """
    if _is_cloud_mode():
        raise HTTPException(status_code=404, detail="Not available in cloud mode")

    logger.info(f"Create user request for: {request.user_id}")

    try:
        db_client = await get_db_client()
        user_repo = UserRepository(db_client)

        # Check if user already exists
        existing_user = await user_repo.get_user(request.user_id)
        if existing_user:
            logger.warning(f"User {request.user_id} already exists")
            return CreateUserResponse(
                success=False,
                error="User already exists"
            )

        # Create new user
        await user_repo.add_user(
            user_id=request.user_id,
            user_type="individual",
            display_name=request.display_name or request.user_id,
        )

        logger.info(f"User {request.user_id} created successfully")
        await track(
            user_id=request.user_id,
            event=EVENT_SIGNED_UP,
            properties={PROP_METHOD: "create_user"},
        )
        _schedule_guide_agent_provisioning(request.user_id, is_new=True)
        return CreateUserResponse(
            success=True,
            user_id=request.user_id,
            guide_agent_provisioning=_guide_agent_feature_on(),
        )

    except Exception as e:
        logger.exception(f"Error creating user: {e}")
        return CreateUserResponse(
            success=False,
            error=str(e)
        )


@router.post("/timezone", response_model=UpdateTimezoneResponse)
async def update_timezone(http_request: Request, request: UpdateTimezoneRequest):
    """
    Update the authenticated user's timezone.

    Automatically called when the browser page loads to sync the user's local
    timezone setting. IANA format, e.g. 'Asia/Shanghai'. Identity comes from
    auth_middleware — the body no longer carries (or trusts) a user_id.
    """
    user_id = await resolve_current_user_id(http_request)
    logger.info(f"Timezone update request: user={user_id}, timezone={request.timezone}")

    try:
        # Validate timezone format
        if not is_valid_timezone(request.timezone):
            logger.warning(f"Invalid timezone format: {request.timezone}")
            return UpdateTimezoneResponse(
                success=False,
                error=f"Invalid timezone format: {request.timezone}. Use IANA format like 'Asia/Shanghai'"
            )

        db_client = await get_db_client()
        user_repo = UserRepository(db_client)

        # Check if user exists
        user = await user_repo.get_user(user_id)
        if not user:
            logger.warning(f"User {user_id} not found")
            return UpdateTimezoneResponse(
                success=False,
                error="User not found"
            )

        # Update timezone
        await user_repo.update_timezone(user_id, request.timezone)

        logger.info(f"User {user_id} timezone updated to {request.timezone}")
        return UpdateTimezoneResponse(
            success=True,
            user_id=user_id,
            timezone=request.timezone,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating timezone: {e}")
        return UpdateTimezoneResponse(
            success=False,
            error=str(e)
        )


# =============================================================================
# Onboarding checklist
# =============================================================================

# Key under users.metadata where the onboarding checklist state lives. The
# metadata column is a shared JSON blob, so reads must merge-and-write to
# avoid clobbering sibling keys.
_ONBOARDING_METADATA_KEY = "onboarding_progress"


def _read_onboarding(metadata: Optional[dict]) -> OnboardingProgress:
    """Extract OnboardingProgress from a user's metadata dict (or defaults)."""
    raw = (metadata or {}).get(_ONBOARDING_METADATA_KEY) or {}
    if not isinstance(raw, dict):
        raw = {}
    return OnboardingProgress(
        first_agent_created=bool(raw.get("first_agent_created", False)),
        template_applied=bool(raw.get("template_applied", False)),
        dismissed=bool(raw.get("dismissed", False)),
        landing_completed=bool(raw.get("landing_completed", False)),
    )


class SessionResponse(BaseModel):
    """Liveness of the caller's own session."""

    user_id: str
    # Epoch seconds. None in local mode, where identity is the X-User-Id
    # header and there is nothing to expire.
    expires_at: Optional[int] = None
    issued_at: Optional[int] = None


@router.get("/session", response_model=SessionResponse)
async def get_session(http_request: Request):
    """Confirm the caller's session is still alive, and say when it dies.

    Two consumers, both in the frontend:

    1. **Second opinion before a forced logout.** A single 401 on some
       unrelated endpoint is not proof the session is dead — it may be a
       transient failure or a bug in that one route. The frontend probes
       here first and only destroys the session if the probe agrees.
       (Before this existed, one stray 401 tore down the whole SPA — the
       2026-08-02 demo incident.)
    2. **Pre-expiry warning.** `expires_at` lets the UI warn the user
       *before* the JWT dies, instead of teleporting them to /login
       mid-sentence. There is no refresh flow, so an unannounced expiry
       is a hard stop.

    Deliberately does NO database work: it runs on every suspected-dead
    session, and a burst of 401s must not become a burst of queries. The
    JWT check already happened in auth_middleware — reaching this handler
    at all *is* the answer.
    """
    user_id = await resolve_current_user_id(http_request)
    expires_at: Optional[int] = None
    issued_at: Optional[int] = None

    auth_header = http_request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        # Verified again rather than read raw: cheap (HMAC over a short
        # string) and keeps this handler from ever reporting claims the
        # middleware would have rejected.
        try:
            claims = decode_token(auth_header[7:])
            expires_at = claims.get("exp")
            issued_at = claims.get("iat")
        except jwt.InvalidTokenError:
            # Unreachable in practice — the middleware verified the same
            # token moments ago. Degrade to "alive, expiry unknown"
            # rather than 500 on a race with a key rotation.
            logger.warning("[session-probe] token verified by middleware but not here")

    return SessionResponse(
        user_id=user_id, expires_at=expires_at, issued_at=issued_at
    )


@router.get("/onboarding", response_model=OnboardingResponse)
async def get_onboarding(http_request: Request):
    """Return the authenticated user's onboarding checklist state.

    2026-08-19: the checklist card that read this on chat-page mount was
    retired, leaving this GET without a frontend caller for a while.
    2026-08-27: it has one again — the root redirect reads
    `landing_completed` here to decide whether a user still owes the
    one-time welcome flow (WelcomePage), and the flow POSTs the flag back.
    Identity comes from auth_middleware (was a client-supplied query param).
    """
    user_id = await resolve_current_user_id(http_request)
    try:
        db_client = await get_db_client()
        user_repo = UserRepository(db_client)
        user = await user_repo.get_user(user_id)
        if not user:
            return OnboardingResponse(success=False, error="User not found")
        return OnboardingResponse(
            success=True,
            progress=_read_onboarding(user.metadata),
        )
    except Exception as e:
        logger.exception(f"Error reading onboarding state: {e}")
        return OnboardingResponse(success=False, error=str(e))


@router.post("/onboarding", response_model=OnboardingResponse)
async def update_onboarding(http_request: Request, request: UpdateOnboardingRequest):
    """Mark one or more onboarding steps complete.

    Write-once-true: only fields explicitly True in the request are
    applied; None / False are ignored so a completed step can never be
    reverted. The merge reads the user's full metadata, updates only the
    `onboarding_progress` sub-key, and writes the whole dict back so
    sibling metadata keys are preserved.
    """
    user_id = await resolve_current_user_id(http_request)
    try:
        db_client = await get_db_client()
        user_repo = UserRepository(db_client)
        user = await user_repo.get_user(user_id)
        if not user:
            return OnboardingResponse(success=False, error="User not found")

        current = _read_onboarding(user.metadata)
        merged = OnboardingProgress(
            first_agent_created=current.first_agent_created
            or request.first_agent_created is True,
            template_applied=current.template_applied
            or request.template_applied is True,
            dismissed=current.dismissed or request.dismissed is True,
            landing_completed=current.landing_completed
            or request.landing_completed is True,
        )

        metadata = dict(user.metadata or {})
        metadata[_ONBOARDING_METADATA_KEY] = merged.model_dump()
        await user_repo.update_user(user_id, {"metadata": metadata})

        logger.info(
            f"Onboarding updated for {user_id}: {merged.model_dump()}"
        )
        return OnboardingResponse(success=True, progress=merged)
    except Exception as e:
        logger.exception(f"Error updating onboarding state: {e}")
        return OnboardingResponse(success=False, error=str(e))


# =============================================================================
# Analytics opt-out
# =============================================================================


def _require_request_user(http_request: Request) -> str:
    """Identity for the analytics endpoints comes from auth_middleware
    (request.state.user_id) — never from the query string or body — so one
    user can't read or flip another user's privacy preference."""
    uid = getattr(http_request.state, "user_id", None)
    if not uid:
        raise AuthError(IDENTITY_UNRESOLVED, "Authentication required")
    return uid


class SetAnalyticsOptOutRequest(BaseModel):
    opted_out: bool


class SetReplyLanguageRequest(BaseModel):
    # i18n code ("zh", "en", ...); empty string clears the preference.
    language: str = Field(default="", max_length=16, pattern=r"^[a-zA-Z-]*$")


@router.get("/settings/analytics")
async def get_analytics_opt_out(http_request: Request):
    """Return whether the current user has opted out of product analytics.

    No-row means not opted out (tracking on by default).
    """
    uid = _require_request_user(http_request)
    repo = UserSettingsRepository(await get_db_client())
    return {"opted_out": await repo.is_analytics_opted_out(uid)}


@router.put("/settings/analytics")
async def set_analytics_opt_out(request: SetAnalyticsOptOutRequest,
                                http_request: Request):
    """Set the current user's analytics opt-out preference."""
    uid = _require_request_user(http_request)
    repo = UserSettingsRepository(await get_db_client())
    await repo.set_analytics_opt_out(uid, request.opted_out)
    return {"success": True, "opted_out": request.opted_out}


# =============================================================================
# Telemetry (diagnostic log shipping) consent
# =============================================================================
#
# Unlike analytics (a per-USER row in user_settings), telemetry consent
# is a marker file (~/.narranexus/telemetry_optout — per USER ACCOUNT
# on this host, shared by all sidecars on a desktop install) read by
# utils/logging at every send — logging starts before the DB does, so
# the DB cannot hold this state. Host-level scope has two consequences
# the endpoints must enforce:
#   - multi-tenant cloud: one user must not silence (or re-enable)
#     telemetry for everyone → PUT is 403, GET says controllable=false
#     (that surface is governed by the deployment env instead);
#   - an explicit NEXUS_DIAG_SHIP env override is the deployment's
#     decision: a marker write would be silently ineffective, so PUT is
#     409 and GET reports source=env / controllable=false.


class SetTelemetryOptOutRequest(BaseModel):
    opted_out: bool


def _telemetry_state() -> dict:
    consent = telemetry_consent()
    # managed_by tells the UI WHO owns a non-controllable state — "your
    # deployment set an env var" and "this is a multi-tenant install"
    # are different facts, and showing the env wording on a cloud
    # install whose telemetry comes from the built-in default would
    # attribute a decision nobody made.
    managed_by = None
    if consent["source"] == "env":
        managed_by = "env"
    elif _is_cloud_mode():
        managed_by = "cloud"
    return {
        "mode": consent["mode"],
        "source": consent["source"],
        "opted_out": consent["source"] == "optout",
        "controllable": managed_by is None,
        "managed_by": managed_by,
    }


@router.get("/settings/telemetry")
async def get_telemetry_consent_state(http_request: Request):
    """Current telemetry consent state + whether the toggle applies."""
    _require_request_user(http_request)
    return _telemetry_state()


@router.put("/settings/telemetry")
async def set_telemetry_consent_state(request: SetTelemetryOptOutRequest,
                                      http_request: Request):
    """Flip the per-machine telemetry opt-out marker."""
    uid = _require_request_user(http_request)
    if _is_cloud_mode():
        raise HTTPException(
            status_code=403,
            detail="telemetry consent is per-machine; on a multi-tenant "
                   "install it is governed by the deployment, not a user",
        )
    if telemetry_consent()["source"] == "env":
        raise HTTPException(
            status_code=409,
            detail="NEXUS_DIAG_SHIP is set: the deployment overrides "
                   "telemetry mode and a marker write would be ineffective",
        )
    set_telemetry_optout(request.opted_out)
    logger.info(
        f"Telemetry opt-out set to {request.opted_out} by {uid}"
    )
    return {"success": True, "opted_out": request.opted_out}
@router.get("/settings/reply-language")
async def get_reply_language(http_request: Request):
    """The user's reply-language preference; null = never set (model free)."""
    uid = _require_request_user(http_request)
    repo = UserSettingsRepository(await get_db_client())
    return {"language": await repo.get_reply_language(uid)}


@router.put("/settings/reply-language")
async def set_reply_language(request: SetReplyLanguageRequest,
                             http_request: Request):
    """Persist the reply-language preference (empty clears it).

    Written by the frontend replyLanguageSync (i18n languageChanged +
    one-time backfill), read by
    ContextRuntime into the system prompt — the fix for "UI set to
    Chinese but replies stay English": the preference used to live only
    in frontend i18n and never reached the model."""
    uid = _require_request_user(http_request)
    repo = UserSettingsRepository(await get_db_client())
    await repo.set_reply_language(uid, request.language)
    return {"success": True, "language": request.language or None}
