"""
@file_name: auth.py
@author: NexusAgent
@date: 2026-04-08
@description: Authentication utilities for cloud deployment

Provides JWT token generation/verification and the auth middleware that
extracts the current user from requests. Cloud identity is established
via NetMind login (see routes/auth.py netmind_login); this module only
ever sees our own self-issued JWTs. In local mode (SQLite), auth is
bypassed — no JWT required.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request
from loguru import logger


# =============================================================================
# Configuration
# =============================================================================

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-do-not-use-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 7


# =============================================================================
# Manyfold gateway-token mode (Part 4.8 of docker-cloud-agent-design spec)
# =============================================================================
#
# When ENABLE_MANYFOLD_API=1, an additional auth path opens up: any request
# carrying ``Authorization: Bearer <MANYFOLD_GATEWAY_TOKEN>`` is trusted as
# coming from the Manyfold platform (or the platform-issued URL fragment
# token captured by the frontend bootstrap). This token covers three classes
# of paths:
#   * /v1/* (OpenAI-compat endpoints — server-to-server from Manyfold api)
#   * /manyfold/* (custom cross-user / diagnostics endpoints)
#   * /api/*, /ws/* when the user navigates the native UI through Manyfold's
#     ingress (the URL fragment #token=... is captured by the frontend and
#     stamped onto every subsequent request as an Authorization header)
#
# Per Owner decision 2026-05-25: container is single-user, so once the
# gateway token matches we resolve to the "first user" in the DB as the
# effective identity. The cross-user /manyfold/agents endpoint reads all
# rows regardless, so this assignment only matters for /api/* surfaces.


def _is_manyfold_api_enabled() -> bool:
    """Manyfold API is opt-in via env. Default off — local/cloud unaffected."""
    return os.environ.get("ENABLE_MANYFOLD_API", "").strip() in ("1", "true", "yes")


def _manyfold_gateway_token() -> str:
    """The shared secret platform injects + the URL fragment also carries.
    Empty string disables the mode (must be set when ENABLE_MANYFOLD_API=1)."""
    return os.environ.get("MANYFOLD_GATEWAY_TOKEN", "").strip()


def _request_has_manyfold_token(request: Request) -> bool:
    """Returns True iff Authorization header carries the gateway token.
    Constant-time-ish comparison via secrets.compare_digest (token strings
    are short; HMAC overkill but safe)."""
    import secrets as _secrets
    expected = _manyfold_gateway_token()
    if not expected:
        return False
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    provided = auth_header[7:].strip()
    if not provided:
        return False
    return _secrets.compare_digest(provided, expected)


def _is_manyfold_path(path: str) -> bool:
    """Paths covered by the manyfold gateway-token rule."""
    return (
        path.startswith("/v1/")
        or path.startswith("/manyfold/")
    )


async def _resolve_manyfold_default_user_id() -> Optional[str]:
    """Legacy fallback for Manyfold handoff URLs that don't carry a
    ``#user=`` fragment param. Returns the first user row's id.

    Replaced in the multi-Manyfold-user flow by the X-User-Id header
    path — see auth_middleware. Kept so older URLs (pre-2026-05-26 build
    of the frontend) keep working without 401.
    """
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    row = await db.get_one("users", {})
    return row.get("user_id") if row else None


async def _ensure_manyfold_user_exists(user_id: str) -> None:
    """Idempotently create a ``mf_<...>`` user row on first contact.

    Triggered when the frontend `initManyfoldFragmentAuth()` decoded a
    Manyfold-side user from the handoff URL and that user has never
    chatted / never been provisioned via POST /manyfold/agents before
    (rare but possible: user opens "Open Native UI" before creating any
    agent in Manyfold).

    We DO NOT clone provider config here — that requires opting in via
    ``NARRANEXUS_INHERIT_PROVIDER_FROM`` on the agent-create path (and
    we don't have access to that env's *value* policy at the auth
    layer). Provider config can still be set up later through the
    native UI's Settings → Providers flow.
    """
    if not user_id.startswith("mf_"):
        return
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    existing = await db.get_one("users", {"user_id": user_id})
    if existing:
        return
    # Display name = the manyfold-side id with the mf_ prefix stripped.
    display_name = user_id[3:] if user_id.startswith("mf_") else user_id
    await db.insert("users", {
        "user_id": user_id,
        "user_type": "local",
        "role": "user",
        "display_name": display_name,
    })
    logger.info(
        f"[manyfold-fragment] auto-created NarraNexus user {user_id!r} "
        f"on first native-UI access"
    )


def _is_cloud_mode() -> bool:
    """Check if running in cloud mode (MySQL) vs local mode (SQLite).

    Precedence (consistent with utils.deployment_mode, the canonical
    resolver the rest of the codebase uses):
      1. An explicit NARRANEXUS_DEPLOYMENT_MODE ("cloud"/"local") wins —
         this is what lets a sqlite + NARRANEXUS_DEPLOYMENT_MODE=cloud
         local smoke run cloud semantics.
      2. Else the legacy heuristic below.

    SAFETY: with NO explicit env var, an unset / empty / sqlite DATABASE_URL
    MUST default to local mode, not cloud. A packaged desktop app (Tauri
    dmg) sets DATABASE_URL via Rust's std::env::set_var, which is NOT
    thread-safe on macOS — the tokio-spawned Python subprocess may not see
    it. If we defaulted to cloud here, the bundled backend would demand
    NetMind login from users running the desktop app in its intended local
    mode (the v0.1.0 dmg bug). The dmg does NOT set
    NARRANEXUS_DEPLOYMENT_MODE, so step 1 never trips it into cloud.
    """
    explicit = os.environ.get("NARRANEXUS_DEPLOYMENT_MODE", "").strip().lower()
    if explicit in ("cloud", "local"):
        return explicit == "cloud"
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        return not db_url.startswith("sqlite")
    # Fallback: individual DB_HOST field means cloud deployment
    return bool(os.environ.get("DB_HOST", ""))


# =============================================================================
# JWT Token
# =============================================================================

def create_token(user_id: str, role: str) -> str:
    """Create a JWT token with user_id and role."""
    payload = {
        "user_id": user_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and verify a JWT token. Raises on invalid/expired tokens."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# =============================================================================
# FastAPI Dependency
# =============================================================================

class CurrentUser:
    """Represents the authenticated user extracted from JWT or local session."""

    def __init__(self, user_id: str, role: str = "user"):
        self.user_id = user_id
        self.role = role

    @property
    def is_staff(self) -> bool:
        return self.role == "staff"


async def get_current_user(request: Request) -> Optional[CurrentUser]:
    """
    FastAPI dependency that extracts the current user.

    - Cloud mode: Requires valid JWT in Authorization header
    - Local mode: Reads user_id from query params or request body (backward compatible)

    Returns None for auth endpoints (login, register) which handle their own auth.
    """
    if not _is_cloud_mode():
        # Local mode: no JWT enforcement, extract user_id from request
        return None

    # Cloud mode: require JWT
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]
    try:
        payload = decode_token(token)
        return CurrentUser(
            user_id=payload["user_id"],
            role=payload.get("role", "user"),
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_auth(request: Request) -> CurrentUser:
    """Synchronous version for use in route signatures. Use as Depends(require_auth)."""
    # This is handled via middleware instead — see below
    pass


# =============================================================================
# Middleware
# =============================================================================

# Paths that don't require authentication (even in cloud mode). Note:
# in local mode, "no auth" means specifically "no X-User-Id required" —
# these endpoints either don't need an identity (account creation,
# health probes) or carry their own (login).
AUTH_EXEMPT_PATHS = {
    "/api/auth/login",
    # NetMind-account login (cloud): carries its own credential — the
    # NetMind loginToken is verified server-side against NetMind's auth
    # API inside the route handler, then exchanged for our own JWT.
    "/api/auth/netmind-login",
    "/api/auth/create-user",
    # Admin identity migration (Phase 1): gated on the X-Admin-Secret header
    # inside the handler (admin_secret_key), not a user JWT — the offline batch
    # migrator has no JWT. Same self-credentialed pattern as netmind-login.
    "/api/admin/migrate-identity",
    "/api/providers/claude-status",
    "/docs",
    "/openapi.json",
    "/health",
    "/healthz",  # Manyfold readiness probe (no auth — platform health check)
}

# Prefixes that don't require auth
AUTH_EXEMPT_PREFIXES = (
    "/ws/",  # WebSocket handles its own auth via message payload
    # Public transcription audio: NetMind's STT worker fetches via
    # HMAC-signed token URLs; the token IS the auth. Without bypass,
    # NetMind can't fetch (it has no JWT). See
    # backend/routes/transcription_public.py and
    # src/xyz_agent_context/agent_framework/transcription/url_signer.py.
    "/api/public/",
)

# Prefixes that STILL require JWT auth but must SKIP the provider_resolver
# quota gate. These routes are pure configuration / self-service CRUD and
# do not spend quota. Without this list, a user whose free tier is
# exhausted cannot add a provider or toggle the "Use free quota" switch
# off — the middleware 402s them before they ever reach the handler,
# creating a dead-end the user cannot escape.
QUOTA_BYPASS_PREFIXES = (
    "/api/providers",  # add / remove / edit provider, set slot model
    "/api/quota",      # read own quota, flip prefer_system_override
    "/api/admin",      # staff operations (grant, init)
    "/api/auth",       # login / register / me / logout
    # `/api/transcription/availability` is a pure capability probe — it
    # has no LLM cost, and the frontend uses it to decide whether the
    # mic button records on click or opens a "configure a provider"
    # dialog. Without this bypass, the very state we want to surface
    # (user opted out of free tier without configuring their own
    # provider) gets a 402 from the resolver instead of an actionable
    # `{available: false, reason: "free_tier_opted_out"}` response.
    "/api/transcription",
)


async def auth_middleware(request: Request, call_next):
    """
    Middleware that enforces JWT authentication in cloud mode.

    Local mode: passes through all requests unchanged.
    Cloud mode: validates JWT for all non-exempt paths, injects user info into request.state.
    """
    # CORS preflight (OPTIONS) requests MUST bypass auth entirely.
    #
    # The CORS spec requires browsers to omit the Authorization header on
    # preflight, so any JWT check here would 401 every cross-origin non-simple
    # request (e.g. requests with Authorization or custom Content-Type). That
    # would kill all /api/* calls from the dev server or from a cloud-app
    # frontend on a different origin.
    #
    # CORSMiddleware is registered in backend/main.py, but FastAPI middleware
    # is LIFO — this auth middleware runs FIRST, so CORSMiddleware never gets
    # a chance at the preflight unless we call_next here. Let the request fall
    # through; CORSMiddleware will intercept and return the correct headers.
    if request.method == "OPTIONS":
        return await call_next(request)

    # ---- Manyfold gateway-token mode (deployment-gated) -------------------
    # When ENABLE_MANYFOLD_API=1, any request carrying a valid Bearer token
    # equal to MANYFOLD_GATEWAY_TOKEN is trusted. For /v1/* and /manyfold/*
    # (platform server-to-server) we accept the token alone — endpoint
    # handlers resolve the real user via agent_id. For /api/* arriving with
    # the same token (URL-fragment captured by frontend), we map to the
    # single container user (Owner decision 2026-05-25 — container is
    # single-user). Token mismatch falls through to existing auth rules.
    if _is_manyfold_api_enabled():
        path = request.url.path
        has_token = _request_has_manyfold_token(request)
        if _is_manyfold_path(path):
            # /v1/* and /manyfold/* are platform-class — require the
            # token unconditionally; never fall through to local/cloud
            # auth rules (which would either let them pass without auth
            # in local mode or 401 via JWT logic — both wrong here).
            if not has_token:
                return _json_response(401, {
                    "detail": (
                        "missing or invalid MANYFOLD_GATEWAY_TOKEN — "
                        "manyfold-class endpoints require Authorization: "
                        "Bearer <token>"
                    ),
                })
            request.state.manyfold_authed = True
            return await call_next(request)
        if has_token and (path.startswith("/api/") or path.startswith("/ws/")):
            # Native UI path through Manyfold ingress (or WS).
            #
            # Identity precedence (most-specific first):
            #   1. X-User-Id header — set by frontend after
            #      `initManyfoldFragmentAuth()` decoded `#user=` from
            #      the URL fragment. This is the multi-Manyfold-user case
            #      where the platform handed us a specific identity.
            #   2. First user in DB — legacy "single-user container"
            #      fallback. Kept for backwards compat with the
            #      pre-fragment-user-param URLs.
            #
            # For case 1, if the requested user_id looks like our
            # auto-generated ``mf_<x>`` shape but doesn't yet exist
            # (this is the user's very first NarraNexus visit through
            # the Manyfold ingress — they haven't created an agent
            # yet so the user row was never auto-provisioned), we
            # eagerly create them now so the native UI doesn't bounce
            # them through some "user not found" dead-end.
            header_uid = request.headers.get("x-user-id", "").strip()
            user_id: Optional[str] = None
            if header_uid:
                user_id = header_uid
                if header_uid.startswith("mf_"):
                    await _ensure_manyfold_user_exists(header_uid)
            else:
                user_id = await _resolve_manyfold_default_user_id()

            if user_id:
                request.state.user_id = user_id
                request.state.manyfold_authed = True
                from xyz_agent_context.agent_framework.api_config import (
                    set_current_user_id,
                )
                set_current_user_id(user_id)
                return await call_next(request)
            # Token valid but no user in DB yet — let normal flow continue
            # so /api/auth/register-style paths still work.

    if not _is_cloud_mode():
        # Local mode: the OS user is the security boundary, so we don't
        # verify signatures — but we DO require the frontend to declare
        # *which* logged-in user this request is for via the X-User-Id
        # header (set by configStore.userId). No header → identity is
        # genuinely unknown, so we reject (401) rather than silently
        # picking a default.
        #
        # The previous version fell back to "the first row in users" when
        # the header was missing. That assumption held in v1 (single
        # local user) but turned into a cross-user write/read corruption
        # bug once we supported multiple local accounts: anyone whose
        # frontend hadn't populated configStore.userId yet (fresh login,
        # cleared localStorage, pre-login probe by a different page) was
        # silently writing to whoever happened to have user_id=1.
        # Auth-exempt paths (login, register, public probes) bypass
        # this so the frontend can still bootstrap.
        local_path = request.url.path
        if (
            local_path.startswith("/api/")
            and local_path not in AUTH_EXEMPT_PATHS
            and not any(local_path.startswith(p) for p in AUTH_EXEMPT_PREFIXES)
        ):
            header_uid = request.headers.get("x-user-id")
            if not header_uid:
                return _json_response(401, {
                    "success": False,
                    "detail": (
                        "Missing X-User-Id header. The frontend must send "
                        "this header for every authenticated request in "
                        "local mode. If you just registered, log in first."
                    ),
                })
            request.state.user_id = header_uid
            # Mirror cloud mode: tag the cost-tracker ContextVar so usage
            # records get attributed to the right user even in local mode.
            from xyz_agent_context.agent_framework.api_config import set_current_user_id
            set_current_user_id(header_uid)
        response = await call_next(request)
        return response

    path = request.url.path

    # Check exemptions
    if path in AUTH_EXEMPT_PATHS or any(path.startswith(p) for p in AUTH_EXEMPT_PREFIXES):
        response = await call_next(request)
        return response

    # Static files (frontend assets)
    if not path.startswith("/api/"):
        response = await call_next(request)
        return response

    # Require JWT
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return _json_response(401, {"detail": "Authentication required"})

    token = auth_header[7:]
    try:
        payload = decode_token(token)
        request.state.user_id = payload["user_id"]
        request.state.role = payload.get("role", "user")
    except jwt.ExpiredSignatureError:
        return _json_response(401, {"detail": "Token expired"})
    except jwt.InvalidTokenError:
        return _json_response(401, {"detail": "Invalid token"})

    # System-default quota routing. Tag current_user_id on the ContextVar
    # (consumed by cost_tracker to attribute usage) and dispatch the
    # resolver to decide user-vs-system routing + quota gating. Resolver
    # itself short-circuits when SystemProviderService.is_enabled()==False,
    # so local mode / feature-off is a no-op end-to-end.
    #
    # Config-class paths (QUOTA_BYPASS_PREFIXES) skip the resolver entirely
    # so users with an exhausted free tier can still reach /api/providers
    # or flip /api/quota/me/preference to escape the dead-end. JWT auth
    # above still applies to those paths.
    from xyz_agent_context.agent_framework.api_config import set_current_user_id
    from xyz_agent_context.agent_framework.provider_resolver import (
        ProviderResolverError,
    )

    set_current_user_id(request.state.user_id)

    if any(path.startswith(p) for p in QUOTA_BYPASS_PREFIXES):
        return await call_next(request)

    resolver = getattr(request.app.state, "provider_resolver", None)
    if resolver is not None:
        try:
            await resolver.resolve_and_set(request.state.user_id)
        except ProviderResolverError as exc:
            return _json_response(402, {
                "success": False,
                "error": "quota_gated",
                "error_code": exc.error_code,
                "message": str(exc),
            })

    response = await call_next(request)
    return response


def _json_response(status_code: int, body: dict):
    """Create a JSON response without importing starlette at module level."""
    from starlette.responses import JSONResponse
    return JSONResponse(status_code=status_code, content=body)


# ---------------------------------------------------------------------------
# Local-mode identity (dashboard v2 TDR-12)
# ---------------------------------------------------------------------------

async def resolve_current_user_id(request) -> str:
    """Single source of truth for "who is the current user" on this request.

    Both cloud and local modes populate ``request.state.user_id`` via the
    ``auth_middleware`` before the route handler runs:

    - cloud: from the verified JWT Bearer token (signed identity)
    - local: from the ``X-User-Id`` header set by the frontend, with a
      fallback to the singleton "first user" for legacy frontends that
      don't send the header

    Route handlers should call this helper instead of branching on
    ``_is_cloud_mode()``. The mode difference is fully encapsulated in
    the middleware, which keeps the rest of the code path identical for
    both modes — a key compatibility goal (cloud multi-user isolation
    and local multi-user isolation share the same downstream code).

    Raises 401 if the middleware skipped setting the field (e.g., the
    route is on an exempt path and called this helper by mistake) —
    treats it as an authentication failure rather than masking the bug.
    """
    from fastapi import HTTPException
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    return uid


async def ensure_local_default_user() -> str:
    """Bootstrap a 'local-default' user row if the users table is empty.

    Called by OS-side scripts (CLI tools, one-shot migrations) that need
    *some* user to exist before they can do work. NEVER called from a
    request handler — request identity comes from the X-User-Id header
    (local) or JWT (cloud), and falling back to "first row" is the bug
    this function used to embody.

    Returns the user_id of an existing row when one is present, or
    creates 'local-default' and returns it. Idempotent.
    """
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    row = await db.get_one("users", {})
    if row:
        return row["user_id"]
    await db.insert(
        "users",
        {
            "user_id": "local-default",
            "user_type": "local",
            "role": "user",
            "display_name": "Local User",
        },
    )
    return "local-default"
