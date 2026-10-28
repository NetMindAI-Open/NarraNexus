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

import re

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request
from loguru import logger

from narranexus.kernel.deployment import is_cloud_mode as _kernel_is_cloud_mode

from narranexus.platform.schema import NON_TRANSACTING_USER_STATUSES

from backend.auth_errors import (
    ACCOUNT_SUSPENDED,
    GATEWAY_TOKEN_INVALID,
    IDENTITY_MISSING,
    IDENTITY_UNRESOLVED,
    TOKEN_EXPIRED,
    TOKEN_INVALID,
    TOKEN_MISSING,
    AuthError,
    auth_error_response,
)


# =============================================================================
# Configuration
# =============================================================================

_DEFAULT_JWT_SECRET = "dev-secret-do-not-use-in-production"
# Known placeholders shipped in the deploy templates (stacks/.../.env.example,
# .env.cloud.example). A cloud .env copied from the template but never edited
# carries one of these, and they're public in git — the real failure mode is
# "copied the template, forgot to change it", not "forgot the line". Treat them
# exactly like the default.
_PLACEHOLDER_JWT_SECRETS = frozenset(
    {
        _DEFAULT_JWT_SECRET,
        "CHANGE_ME_TO_A_RANDOM_64_CHAR_STRING",
    }
)
_MIN_CLOUD_JWT_SECRET_LEN = 32
JWT_SECRET = os.environ.get("JWT_SECRET", _DEFAULT_JWT_SECRET)
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 7


def assert_jwt_secret_safe() -> None:
    """Fail-fast in cloud mode when JWT_SECRET is unset, a known template
    placeholder, or too short to be a real secret. A guessable signing secret
    lets anyone forge any user's JWT, so we refuse to boot rather than sign
    with it — the same cloud-mode posture the artifact-token secret takes
    (routes/artifacts/_token.py). Local mode keeps the default: single trusted
    user, no JWT required.

    Reads the effective module constant `JWT_SECRET` (resolved from the env at
    import, i.e. at startup) as the single source of truth.
    """
    if not _is_cloud_mode():
        return
    secret = (JWT_SECRET or "").strip()
    if (
        not secret
        or secret in _PLACEHOLDER_JWT_SECRETS
        or len(secret) < _MIN_CLOUD_JWT_SECRET_LEN
    ):
        raise RuntimeError(
            "JWT_SECRET is required in cloud mode but is unset, a known "
            "placeholder, or shorter than 32 chars. Set a strong, unique "
            "JWT_SECRET; we refuse to sign tokens with a guessable secret."
        )


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
    from narranexus.platform.utils.db.db_factory import get_db_client
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
    from narranexus.platform.utils.db.db_factory import get_db_client
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

    Forwards to the kernel's single resolver (``narranexus.kernel.deployment``):
    explicit ``NARRANEXUS_DEPLOYMENT_MODE`` wins, then the ``DATABASE_URL``
    heuristic, then ``DB_HOST``. With NO explicit env var an unset / empty /
    sqlite ``DATABASE_URL`` MUST resolve to local — a packaged desktop app
    (Tauri dmg) sets ``DATABASE_URL`` via Rust's ``std::env::set_var``, which is
    NOT thread-safe on macOS, so the bundled backend must never demand a
    NetMind login because the variable failed to propagate (the v0.1.0 dmg bug).
    """
    return _kernel_is_cloud_mode()


# =============================================================================
# NarraNexus service identity (blueprint Q6 — mcp→backend trust path)
# =============================================================================
#
# Cloud-mode requests whose Authorization bearer is the nx-agent positional
# record (the executor→mcp identity channel, forwarded verbatim by the mcp
# container's HttpStore). Verification is asymmetric: the broker signed the
# identity_token field (last in BEARER_FIELDS) with its Ed25519 PRIVATE key;
# we hold only the PUBLIC key, so a
# compromised backend/mcp cannot mint identities. Consumed by auth_middleware
# right before the user-JWT decode; see the block comment there for the
# no-fail-open rationale.


def _is_nx_service_bearer(auth_header: str) -> bool:
    from narranexus.platform.module_system import BEARER_AGENT_PREFIX

    return auth_header.startswith(f"Bearer {BEARER_AGENT_PREFIX}")


def _verify_nx_service_bearer(request: "Request"):
    """The proven service identity, or None (invalid/absent/unverifiable).

    Verification itself is the shared identity/verify.py algorithm — this
    wrapper only owns backend's degradation policy (fail CLOSED, unlike the
    mcp middleware: an nx-agent bearer reaching backend is always a service
    call and must prove itself, so a missing public key rejects too) and the
    per-reason logging (0806 discipline: every reject must be diagnosable
    from server logs).
    """
    from narranexus.platform.module_system.identity.tokens import load_public_key_pem
    from narranexus.platform.module_system.identity.verify import verify_caller_identity

    public_key = load_public_key_pem()
    if public_key is None:
        logger.warning(
            "[nx-service-auth] no identity public key provisioned "
            "(NX_IDENTITY_PUBLIC_KEY_FILE) — rejecting service call"
        )
        return None
    identity, reason = verify_caller_identity(request.headers, public_key)
    if identity is None:
        logger.warning(f"[nx-service-auth] {reason}")
        return None
    return identity


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

    # Cloud mode: the bound authProviders plugin must name the user
    from backend.auth_provider import auth_provider

    identity = await auth_provider().authenticate(request)
    if identity is None:
        raise AuthError(TOKEN_MISSING, "Missing or invalid Authorization header")
    return CurrentUser(user_id=identity["user_id"], role=identity.get("role", "user"))


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
    # Self-serve signup: by definition there is no identity yet. Both routes
    # carry their own protection instead — per-email rate limits, server-side
    # password policy, and the upstream's own verification code. Missing these
    # two is why signup 401'd on first deploy.
    "/api/auth/signup",
    "/api/auth/signup/send-code",
    # Client-side auth-funnel failure reports: by definition the reporter
    # has no session (their login just failed). Log-only, rate-limited.
    "/api/auth/funnel-report",
    "/api/auth/create-user",
    # Admin identity migration (Phase 1): gated on the X-Admin-Secret header
    # inside the handler (admin_secret_key), not a user JWT — the offline batch
    # migrator has no JWT. Same self-credentialed pattern as netmind-login.
    "/api/admin/migrate-identity",
    # Account-suspension mechanism: self-credentialed on X-Admin-Secret inside
    # the handlers (same pattern as migrate-identity). A private operator, not
    # a user JWT, drives these — and a suspended account holds no usable JWT
    # anyway, so gating them on the user auth path would be circular. The
    # account-state READ is a path-parameter route, so it is exempted via the
    # prefix list below rather than here.
    "/api/admin/suspend",
    "/api/admin/reinstate",
    # Internal gateway-key-misuse writer: self-credentialed on X-Admin-Secret
    # inside the handler (same lock as suspend). The caller is the internal
    # server-to-server path forwarding an authoritative gateway-key misuse event
    # — a machine with no user JWT — so gating it on the user auth path would be
    # wrong. Sole writer of gateway_key_misuse.
    "/api/admin/gateway-key-misuse",
    # Sensitive-operation warning: self-credentialed on X-Admin-Secret inside the
    # handler (same lock as suspend). The caller is the private monitor
    # forwarding a soft-signal detection — a machine with no user JWT — so gating
    # it on the user auth path would be wrong. Writes one generic abuse_warning
    # notification + a ban_audit(action="warn") row.
    "/api/admin/warn-user",
    # Runtime observability: polled by the deploy-side alert watcher (a
    # headless container with no user JWT), gated on the same X-Admin-Secret
    # inside the handler. Read-only.
    "/api/admin/runtime/status",
    # NarraMessenger sandbox prewarm (F28): machine-to-machine, self-
    # credentialed inside the handler with the per-agent bearer_token
    # (constant-time compare) — the caller is the NarraMessenger backend,
    # which has no user JWT. Same pattern as /api/admin/runtime/status.
    "/api/narramessenger/prewarm",
    "/api/narramessenger/prewarm/status",
    # Marketplace publish: self-credentialed via the X-Publish-Token header
    # (MARKETPLACE_PUBLISH_TOKEN env) — CI/ops publishers have no user JWT.
    # Same pattern as migrate-identity above.
    "/api/marketplace/skills/publish",
    "/api/providers/claude-status",
    "/docs",
    "/openapi.json",
    "/health",
    "/healthz",  # Manyfold readiness probe (no auth — platform health check)
}

# Prefixes that don't require auth
# Marketplace READ surface (GET only): public like a package registry, so
# desktop deployments — whose users are NOT logged into the cloud — can
# search/inspect/download skills. Auth is OPTIONAL here: when a credential
# is present (JWT in cloud, X-User-Id in local) the middleware still
# resolves the user so agent-scoped features (installed flags, per-agent
# update checks) work; when absent the request proceeds anonymously and
# routes degrade gracefully. POSTs under these prefixes (install) are NOT
# covered and keep strict auth; publish has its own exemption above.
# Covers both marketplace objects: skills/* and teams/* (a desktop client
# browsing/downloading team templates has no cloud JWT).
MARKETPLACE_PUBLIC_READ_PREFIXES = ("/api/marketplace/skills", "/api/marketplace/teams")


def _is_marketplace_public_read(request: "Request") -> bool:
    # path_under_prefix, not startswith: a prefix set is a set of PATH prefixes,
    # and string matching is not segment matching (`/api/marketplace/skills`
    # would otherwise open `/api/marketplace/skills-admin`). Same rule for every
    # prefix set in this file.
    return request.method == "GET" and any(
        path_under_prefix(request.url.path, p) for p in MARKETPLACE_PUBLIC_READ_PREFIXES
    )


# Prefixes plugin routers declared with ``auth="none"`` (RouterSpec). Filled by
# ``backend.plugins_host.mount_plugin_routes`` at boot — the ONLY way a plugin
# obtains an unauthenticated endpoint, and always explicit in its manifest
# code. A set (not the tuple below) because it is populated at runtime.
PLUGIN_EXEMPT_PREFIXES: set[str] = set()

# Route prefixes plugin routers declared with ``quota_bypass=True`` (RouterSpec):
# config-class endpoints a user whose free tier is exhausted must still reach
# (the same reason QUOTA_BYPASS_PREFIXES exists). Filled at mount time.
PLUGIN_QUOTA_BYPASS_PREFIXES: set[str] = set()


def path_under_prefix(path: str, prefix: str) -> bool:
    """True when ``path`` IS ``prefix`` or lies beneath it as a whole path segment.

    String prefix matching is not segment matching: ``/api/x/acme.w`` used to
    exempt ``/api/x/acme.w2/...`` and ``/api/x/p/webhook`` exempted
    ``/api/x/p/webhook-admin``. Starlette routes by segment; auth must too.
    """
    base = prefix.rstrip("/")
    return path == base or path.startswith(base + "/")

# Inbound channel webhooks (plugin platform batch 4c): the external platform
# has no session; the binding's webhook_secret is the auth, verified in the
# handler (backend/routes/channels/generic.py). Exact shape only — every
# other /api/channels/* route keeps the normal auth.
_CHANNEL_WEBHOOK_RE = re.compile(r"^/api/channels/[^/]+/webhook/[^/]+$")


def _is_channel_webhook_path(path: str) -> bool:
    return bool(_CHANNEL_WEBHOOK_RE.match(path))


def _is_plugin_exempt(path: str) -> bool:
    return any(path_under_prefix(path, p) for p in PLUGIN_EXEMPT_PREFIXES) or _is_channel_webhook_path(path)


def _is_plugin_quota_bypass(path: str) -> bool:
    return any(path_under_prefix(path, p) for p in PLUGIN_QUOTA_BYPASS_PREFIXES)


AUTH_EXEMPT_PREFIXES = (
    # Account-state READ (GET /api/admin/account-state/{user_id}): a
    # path-parameter route, so it cannot be an exact entry in
    # AUTH_EXEMPT_PATHS. Self-credentialed on X-Admin-Secret inside the
    # handler, same as the two suspend/reinstate POSTs above.
    "/api/admin/account-state/",
    "/ws/",  # WebSocket handles its own auth via message payload
    # Public transcription audio: NetMind's STT worker fetches via
    # HMAC-signed token URLs; the token IS the auth. Without bypass,
    # NetMind can't fetch (it has no JWT). See
    # backend/routes/transcription/public.py and
    # src/narranexus/platform/agent_framework/llm/transcription/url_signer.py.
    "/api/public/",
)

# Prefixes that STILL require JWT auth but must SKIP the provider_resolver
# quota gate. These routes are pure configuration / self-service CRUD and
# do not spend quota. Without this list, a user whose free tier is
# exhausted cannot add or fix a provider — the middleware 402s them before
# they ever reach the handler, creating a dead-end the user cannot escape.
QUOTA_BYPASS_PREFIXES = (
    "/api/providers",  # add / remove / edit provider, set slot model
    "/api/quota",      # read own quota (GET /me)
    "/api/admin",      # staff operations (grant, init)
    "/api/auth",       # login / register / me / logout
    # `/api/transcription/availability` is a pure capability probe — it
    # has no LLM cost, and the frontend uses it to decide whether the
    # mic button records on click or opens a "configure a provider"
    # dialog. Without this bypass, the very state we want to surface
    # (user with no free-tier grant and no own transcription provider)
    # gets a 402 from the resolver instead of an actionable
    # `{available: false, reason: "free_tier_not_granted"}` response.
    "/api/transcription",
    # `/api/billing/*` proxies the user's NetMind subscription/balance. A
    # quota-exhausted user is EXACTLY who needs to reach the "upgrade to Pro"
    # panel — a 402 from the resolver before the route runs would dead-end
    # them. Billing calls carry no NarraNexus LLM cost (they proxy NetMind),
    # so bypass the quota gate.
    "/api/billing",
    # First-party product facts are metadata-only and do not invoke an LLM.
    # Quota-exhausted users must still be able to record upgrade and checkout
    # actions; otherwise the payment funnel is biased exactly at the paywall.
    "/api/analytics",
    # Notices are pure metadata (system notifications + mark-read) with no
    # LLM cost. The primary notice class is "your agent's provider is
    # broken / quota exhausted" — exactly the users the quota gate would
    # otherwise 402 on the mark-read POST.
    "/api/notices",
)

# HTTP methods that never spend LLM quota. Safe/read-only requests must
# reach their handler even when the free tier is exhausted -- otherwise an
# exhausted account is locked out of reading its OWN data (dashboard,
# agent list, chat history, ...), not just blocked from starting new LLM
# work (GH #61: quota exhaustion 402'd every /api/ call, including plain
# GETs, wholesale-locking the dashboard).
#
# This is safe because it only widens WHICH requests skip
# `resolver.resolve_and_set` -- it does not touch the JWT check above,
# which still runs unconditionally. `resolve_and_set` only pushes LLM
# provider config onto ContextVars for the current task; GET/HEAD
# handlers never read those ContextVars because none of them invoke the
# agent loop or the LLM-facing routes (verified: every quota-spending
# endpoint in backend/routes/**, including /v1/chat/completions and every
# agent-run/trigger route, is a POST; the only GET handlers that stream
# a response, e.g. backend/routes/manyfold/files.py's file-read endpoint,
# stream bytes off disk and never touch an LLM).
SAFE_HTTP_METHODS = frozenset({"GET", "HEAD"})


# ---------------------------------------------------------------------------
# Account-state gate
# ---------------------------------------------------------------------------
# A per-request check on the caller's account state. An account whose state is
# not one that may transact is refused at the door with a 403, so a suspended
# account cannot use an already-issued (still-valid) JWT.
#
# The states that forbid transacting are the shared
# ``NON_TRANSACTING_USER_STATUSES`` set (imported above). Kept as a single
# source of truth so this gate, the WS run gate, and the login gate can never
# drift apart. This file holds no policy about how an account reaches one of
# those states; a private operator drives that via the admin endpoints.

# The middleware runs on every authenticated request, so a DB read per request
# would put the users table on the hot path. A tiny in-process TTL cache keeps
# the common case (an active account making many requests) DB-free; the TTL
# bounds how long a just-suspended account can keep transacting to at most
# ~_ACCOUNT_STATE_TTL seconds even if the invalidate hook is missed (e.g. a
# suspend applied on a different backend process). A module-level dict is fine:
# it is per-process, best-effort, and self-correcting.
#
# Bounded so a burst of distinct user_ids (many short-lived sessions, or a
# probe hitting the gate with junk-but-valid JWTs) cannot grow it without limit.
# At the cap we drop the whole dict rather than evict one entry: the cache is
# best-effort and self-refilling, so a wholesale clear is the cheapest bound and
# costs at most one extra DB read per still-active user afterwards.
_ACCOUNT_STATE_TTL_SECONDS = 30.0
_ACCOUNT_STATE_MAX_ENTRIES = 10_000
_account_state_cache: dict[str, "tuple[str, float]"] = {}


def invalidate_account_state(user_id: str) -> None:
    """Best-effort drop of a cached account state.

    Called after a suspend/reinstate so the change is visible immediately in
    THIS process. Cross-process staleness is bounded by the TTL instead.
    """
    _account_state_cache.pop(user_id, None)


async def _account_state(user_id: str) -> str:
    """Return the caller's ``users.status`` (cached ~TTL seconds).

    Fails OPEN: on any lookup error, or a user row that cannot be found, it
    returns ``"active"`` and does not cache. A transient DB blip must never
    lock every user out of the product — the gate exists to stop a specific
    suspended account, not to become a global availability dependency.

    Reads through ``UserRepository.get_user`` (a ``WHERE BINARY user_id`` query)
    so the account-state READ is case-sensitive in exactly the same way the
    suspension WRITE is — a plain ``get_one`` uses MySQL's default
    case-INSENSITIVE collation and would let a look-alike user_id dodge the gate
    (invisible on SQLite, which has no such collation). Its strong
    ``_row_to_entity`` cast can raise on a ``status`` value not in
    ``UserStatus``; the ``except Exception`` below fails that open too.
    """
    # Repository is imported lazily (inside the function) on purpose: it pulls
    # the DB layer in, and auth.py must stay importable at process start without
    # it.
    now = time.monotonic()
    cached = _account_state_cache.get(user_id)
    if cached is not None and cached[1] > now:
        return cached[0]

    try:
        from narranexus.platform.repository.user_repository import UserRepository
        from narranexus.platform.utils.db.db_factory import get_db_client

        db = await get_db_client()
        user = await UserRepository(db).get_user(user_id)
    except Exception as e:  # noqa: BLE001 — availability over strictness
        logger.warning(
            f"[account-state] lookup failed for user={user_id!r}: "
            f"{type(e).__name__}: {e} (failing open)"
        )
        return "active"

    if user is None:
        # No such row: nothing to suspend. Don't cache — the row may appear
        # (lazy user creation) and we don't want a stale miss to linger.
        return "active"

    status = user.status.value
    if len(_account_state_cache) >= _ACCOUNT_STATE_MAX_ENTRIES:
        _account_state_cache.clear()
    _account_state_cache[user_id] = (status, now + _ACCOUNT_STATE_TTL_SECONDS)
    return status


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
    # Since 2026-08-06 CORSMiddleware is the OUTERMOST layer (see the
    # ordering note in backend/main.py), so a preflight is answered before it
    # ever reaches this function and this branch is defence in depth. It used
    # to be load-bearing: CORS was registered innermost, and without this
    # bypass every cross-origin preflight died here. Kept because the cost is
    # one comparison and the failure mode of losing it — should the ordering
    # ever regress — is "all cross-origin calls stop working".
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
                return auth_error_response(
                    GATEWAY_TOKEN_INVALID,
                    (
                        "missing or invalid MANYFOLD_GATEWAY_TOKEN — "
                        "manyfold-class endpoints require Authorization: "
                        "Bearer <token>"
                    ),
                    path=path,
                    method=request.method,
                )
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
                from narranexus.platform.agent_framework.api_config import (
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
            and not any(path_under_prefix(local_path, p) for p in AUTH_EXEMPT_PREFIXES)
            and not _is_plugin_exempt(local_path)
        ):
            from backend.auth_provider import auth_provider

            identity = await auth_provider().authenticate(request)
            header_uid = identity["user_id"] if identity else None
            if not header_uid and _is_marketplace_public_read(request):
                # Anonymous marketplace read — proceed without identity;
                # routes skip agent-scoped annotations.
                return await call_next(request)
            if not header_uid:
                return auth_error_response(
                    IDENTITY_MISSING,
                    (
                        "Missing X-User-Id header. The frontend must send "
                        "this header for every authenticated request in "
                        "local mode. If you just registered, log in first."
                    ),
                    path=local_path,
                    method=request.method,
                    extra={"success": False},
                )
            request.state.user_id = header_uid
            # Mirror cloud mode: tag the cost-tracker ContextVar so usage
            # records get attributed to the right user even in local mode.
            from narranexus.platform.agent_framework.api_config import set_current_user_id
            set_current_user_id(header_uid)
        response = await call_next(request)
        return response

    path = request.url.path

    # Check exemptions
    if path in AUTH_EXEMPT_PATHS or any(path_under_prefix(path, p) for p in AUTH_EXEMPT_PREFIXES) or _is_plugin_exempt(path):
        response = await call_next(request)
        return response

    # Static files (frontend assets)
    if not path.startswith("/api/"):
        response = await call_next(request)
        return response

    auth_header = request.headers.get("Authorization", "")
    has_bearer = auth_header.startswith("Bearer ")

    # ---- NarraNexus service identity (blueprint Q6) -----------------------
    # The mcp container's HttpStore forwards the executor→mcp identity
    # headers verbatim; the bearer is the nx-agent positional record whose
    # identity_token field (last in BEARER_FIELDS) is a broker/local-signed
    # Ed25519 identity token. Verify it
    # with the identity PUBLIC key and trust its sub as the effective user —
    # routes still owner-check the target agent_id. Parallel to the manyfold
    # gateway-token bypass above, but NEVER fail-open: an nx-agent bearer
    # reaching backend is always a service call and must prove itself (no
    # key provisioned = still 401). Early return also skips the provider/
    # quota resolver below on purpose — these are repository operations, not
    # LLM spends (same reason SAFE_HTTP_METHODS skip it).
    #
    # Account state is enforced here too (2026-10-28): a suspended / banned
    # user's service bearer gets the same 403 ACCOUNT_SUSPENDED as their JWT,
    # so background work already running for them (jobs, agent turns calling
    # backend via the executor) stops at its next backend call instead of
    # riding the token's TTL. ``_account_state`` is TTL-cached, so this costs
    # at most one users-table read per user per TTL on the internal hot path.
    if has_bearer and _is_nx_service_bearer(auth_header):
        identity = _verify_nx_service_bearer(request)
        if identity is None:
            return auth_error_response(
                TOKEN_INVALID, "Invalid NarraNexus service identity",
                path=path, method=request.method,
            )
        # A sandbox token must die with the account (2026-10-28: an attacker
        # replayed one from the internet; a banned user's executor token kept
        # working for its 72h TTL). Same reader, same statuses as the JWT path.
        if (await _account_state(identity.user_id)) in NON_TRANSACTING_USER_STATUSES:
            return auth_error_response(
                ACCOUNT_SUSPENDED,
                "Account is not available",
                path=path,
                method=request.method,
                user_id=identity.user_id,
                status_code=403,
            )
        request.state.user_id = identity.user_id
        request.state.role = "user"
        request.state.nx_service_authed = True
        from narranexus.platform.agent_framework.api_config import set_current_user_id

        set_current_user_id(identity.user_id)
        return await call_next(request)

    # ---- the bound authProviders plugin (kernel.auth) names the user -------
    # builtin.auth.netmind decodes the NetMind JWT; a distribution may bind
    # its own (SSO). The provider raises AuthError with the code to report.
    # The provider decides whether a credential is present at all: a
    # distribution binding a cookie / SSO / header provider must be reachable
    # without a bearer (the old "Bearer or 401" gate ran first and made the
    # seam impossible to exercise). ``None`` from the provider with no bearer
    # is "no credential" (TOKEN_MISSING); with a bearer it is "bad credential".
    from backend.auth_provider import auth_provider

    token = auth_header[7:] if has_bearer else ""  # only a bearer's value belongs in the error log
    try:
        identity = await auth_provider().authenticate(request)
    except AuthError as exc:
        return auth_error_response(
            exc.code, str(exc.detail),
            path=path, method=request.method, token=token, status_code=exc.status_code,
        )
    if identity is None:
        if _is_marketplace_public_read(request):
            # Anonymous marketplace read (desktop clients have no cloud JWT).
            return await call_next(request)
        if not has_bearer:
            return auth_error_response(
                TOKEN_MISSING, "Authentication required",
                path=path, method=request.method,
            )
        return auth_error_response(
            TOKEN_INVALID, "Invalid token",
            path=path, method=request.method, token=token,
        )
    request.state.user_id = identity["user_id"]
    request.state.role = identity.get("role", "user")

    # Account-state gate. The JWT is valid, but a still-valid token must not
    # keep working once the account it names has been suspended. This is a 403
    # (authenticated, not permitted) with a distinct code — never a 401, which
    # the SPA reads as "session dead" and would bounce to a login loop that
    # only re-mints the same token. Cached ~TTL seconds so this is DB-free on
    # the common (active) path.
    account_state = await _account_state(request.state.user_id)
    if account_state in NON_TRANSACTING_USER_STATUSES:
        return auth_error_response(
            ACCOUNT_SUSPENDED,
            "Account is not available",
            path=path,
            method=request.method,
            user_id=request.state.user_id,
            status_code=403,
        )

    # Provider routing. Tag current_user_id on the ContextVar (consumed by
    # cost_tracker to attribute usage) and dispatch the resolver, which puts
    # this user's provider config on the request's ContextVars. The resolver
    # short-circuits in local mode, so that path is a no-op end-to-end.
    #
    # Config-class paths (QUOTA_BYPASS_PREFIXES) skip the resolver entirely so
    # a user with NO usable provider can still reach /api/providers to fix it.
    # Safe/read-only methods (SAFE_HTTP_METHODS) skip it for the same reason on
    # EVERY path, since reads never spend anything. JWT auth above still
    # applies in both cases.
    from narranexus.platform.agent_framework.api_config import set_current_user_id
    from narranexus.platform.agent_framework.providers.resolver import (
        ProviderResolverError,
    )

    set_current_user_id(request.state.user_id)

    if (
        request.method in SAFE_HTTP_METHODS
        or any(path_under_prefix(path, p) for p in QUOTA_BYPASS_PREFIXES)
        or _is_plugin_quota_bypass(path)
    ):
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

def reject_cross_origin(request) -> None:
    """CSRF guard for unauthenticated local-mode writes (e.g. tokenless
    marketplace publish). Shared so no route depends on another route's
    private helper.

    A cross-site browser POST carries an Origin header pointing at the
    attacker page; same-origin UI and CLI tools do not (or point at
    loopback). CRITICAL: ``Origin: null`` (sandboxed iframe / data: form) is
    treated as CROSS-origin, not same-origin — a sandboxed iframe form POST
    is exactly the CSRF vector, and multipart POST is a CORS simple request
    (no preflight). As defense-in-depth we also honor ``Sec-Fetch-Site``
    (modern browsers always send it; CLIs don't): anything other than
    same-origin/none is rejected.
    """
    from fastapi import HTTPException
    from urllib.parse import urlparse

    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site and fetch_site not in ("same-origin", "none"):
        raise HTTPException(status_code=403, detail="cross-origin request not allowed")

    origin = request.headers.get("origin")
    if not origin:
        return  # no Origin (CLI, same-origin GET-turned-POST) — allow
    if origin.lower() == "null":
        raise HTTPException(status_code=403, detail="cross-origin request not allowed")
    host = (urlparse(origin).hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise HTTPException(status_code=403, detail="cross-origin request not allowed")


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
    uid = getattr(request.state, "user_id", None)
    if not uid:
        # NOT session death — the middleware already accepted this request's
        # credential; an empty request.state.user_id here is our own wiring
        # bug. Tagged so the frontend surfaces it locally instead of logging
        # the user out (see backend/auth_errors.py).
        raise AuthError(IDENTITY_UNRESOLVED, "Authentication required")
    return uid


async def resolve_optional_user_id(request) -> Optional[str]:
    """Like resolve_current_user_id, but returns None for anonymous requests
    on optional-auth surfaces (marketplace public reads) instead of raising."""
    return getattr(request.state, "user_id", None)


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
    from narranexus.platform.utils.db.db_factory import get_db_client
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
