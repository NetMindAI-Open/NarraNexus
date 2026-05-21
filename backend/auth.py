"""
@file_name: auth.py
@author: NexusAgent
@date: 2026-04-08
@description: Authentication utilities for cloud deployment

Provides JWT token generation/verification, password hashing,
and FastAPI dependency for extracting current user from requests.
In local mode (SQLite), auth is bypassed — no JWT required.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from loguru import logger


# =============================================================================
# Configuration
# =============================================================================

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-do-not-use-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 7
# Invite-code registration gating is no longer a single global env var.
# It is now a per-code DB mechanism — see `invite_codes` table,
# `InviteCodeRepository`, and `backend/routes/invite.py`.


def _is_cloud_mode() -> bool:
    """Check if running in cloud mode (MySQL) vs local mode (SQLite).

    SAFETY: an unset / empty DATABASE_URL MUST default to local mode, not
    cloud. A packaged desktop app (Tauri dmg) sets DATABASE_URL via Rust's
    std::env::set_var, which is NOT thread-safe on macOS — the tokio-spawned
    Python subprocess may not see it. If we defaulted to cloud here, the
    bundled backend would demand passwords from users who are using the
    desktop app in its intended local mode, which is exactly the bug that
    surfaced in the v0.1.0 dmg. Cloud mode is only active when someone
    explicitly provides a non-sqlite DATABASE_URL.
    """
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        return not db_url.startswith("sqlite")
    # Fallback: individual DB_HOST field means cloud deployment
    return bool(os.environ.get("DB_HOST", ""))


# =============================================================================
# Password Hashing
# =============================================================================

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


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
    "/api/auth/register",
    "/api/auth/create-user",
    # Internal invite-issuance endpoint — server-to-server, called by the
    # narranexus-website backend. It authenticates via the
    # X-Internal-Secret header (matched against INTERNAL_INVITE_SECRET env
    # var) inside the route handler itself, NOT via JWT. Admin invite
    # operations live under /api/admin/invite and DO require a staff JWT.
    "/api/invite/internal/issue",
    "/api/providers/claude-status",
    "/docs",
    "/openapi.json",
    "/health",
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
