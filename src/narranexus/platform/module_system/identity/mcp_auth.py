"""
@file_name: mcp_auth.py
@author:
@date: 2026-08-10
@description: Server-side identity AUTH for module MCP servers (blueprint P1).

``module/_mcp_identity.py`` answers "who does the caller SAY they are" — a
deliberately fail-open convenience. This module answers "can they PROVE it":
Ed25519 verification of the identity token the platform stamped into the same
header channel (the bearer's identity_token field / X-NarraNexus-Identity-Token), plus the
owner-scoped access policy built on that proof.

Gated by ``NX_MCP_AUTH_MODE``:
  off     (default) — nothing changes anywhere; a local ``bash run.sh`` stays
          byte-identical (iron rule #7).
  audit   — verify + log; NEVER rejects. The measurement phase: its logs and
          audit rows decide when enforce is safe (which callers still arrive
          tokenless).
  enforce — tool-call POSTs without a valid token are 401'd at the door, and
          OwnerScopedPolicy denies cross-owner agent access at the tool layer.

Scope of the door check: POSTs only. Tool calls are POSTs on both transports
(/messages/ on SSE, /mcp on streamable HTTP); GETs are connection handshakes
and stay open, as does /health (compose probes carry no identity).

Missing public key under enforce fails OPEN with a loud warning: mcp is the
data plane — a deploy misconfiguration must degrade to audit semantics, not
take every agent's tools down.
"""
from __future__ import annotations

import json
import os
import time
from contextvars import ContextVar
from typing import Any, Optional

from loguru import logger

from narranexus.platform.module_system.identity.tokens import (
    VerifiedIdentity,
    load_public_key_pem,
)

AUTH_MODE_ENV = "NX_MCP_AUTH_MODE"
_MODES = ("off", "audit", "enforce")

# Paths that stay open in every mode: health probes carry no identity.
_EXEMPT_PATHS = frozenset({"/health", "/healthz"})

# The verified caller of the CURRENT request. Set by IdentityAuthMiddleware,
# read by the ownership policy below (and any tool that wants the proven
# identity). None = no proof presented / verification failed / auth off.
_verified_var: ContextVar[Optional[VerifiedIdentity]] = ContextVar(
    "nx_mcp_verified_identity", default=None
)


# Last unknown mode value already warned about — auth_mode() runs per request
# AND per tool call, so an unmemoized warning would flood a busy process over
# one typo. Warn once per distinct value; the signal survives, the flood doesn't.
_warned_unknown_mode: Optional[str] = None


def auth_mode() -> str:
    """The configured mode. An unknown value reads as *audit*, not off — a
    typo'd mode must surface in logs rather than silently disable auth."""
    global _warned_unknown_mode
    raw = (os.environ.get(AUTH_MODE_ENV) or "off").strip().lower()
    if raw in _MODES:
        return raw
    if raw != _warned_unknown_mode:
        _warned_unknown_mode = raw
        logger.warning(f"[mcp-auth] unknown {AUTH_MODE_ENV}={raw!r}; treating as audit")
    return "audit"


def verified_caller() -> Optional[VerifiedIdentity]:
    """The cryptographically proven caller of the current request, or None."""
    return _verified_var.get()


def verified_caller_for_tool_call() -> Optional[VerifiedIdentity]:
    """The proven caller of the CURRENT TOOL CALL — per-message, not
    per-connection.

    Why this exists (pipeline review of PR #260, verified against mcp 1.24
    sources): the middleware's ContextVar is a snapshot of the request that
    STARTED the transport session — on SSE the tool handler runs inside the
    GET /sse task, on stateful streamable HTTP inside the long-lived
    initialize-time session task. The self-declared facts (`agent_id`,
    `user_id`) are read per-message via `request_ctx` (explicitly threaded
    through ServerMessageMetadata), so the PROOF must come from the same
    source or an adapter that only sets Authorization on tool-call POSTs
    would pass the door yet leave the ownership policy blind.

    Precedence: ambient (per-message) headers when a request is in scope —
    and their verdict is FINAL, even when it is "no proof" (falling back to
    the connection snapshot there would resurrect the mismatch this fixes).
    The ContextVar is only the fallback when there is no ambient MCP request
    at all (direct calls, unit tests).
    """
    from narranexus.platform.module_system._mcp_identity import _ambient_headers

    headers = _ambient_headers()
    if headers is None:
        return verified_caller()
    public_key = load_public_key_pem()
    if public_key is None:
        return None
    from narranexus.platform.module_system.identity.verify import verify_caller_identity

    identity, _reason = verify_caller_identity(headers, public_key)
    return identity


# ---------------------------------------------------------------------------
# Tokenless measurement — audit mode's entire purpose
# ---------------------------------------------------------------------------
#
# An unauthenticated POST is the one fact the audit window exists to count
# (which callers must be onboarded before enforce can flip). Logging each one
# would flood; logging none reads as "everyone has a token" even when NOBODY
# does (incident lesson #4: no signal is not a signal). So: aggregate per
# (declared caller, method, path) — tokenless is not identity-less, the old
# bearer channel still carries the SELF-DECLARED user_id, which is exactly
# who to go onboard — and flush one WARNING line + one sampled
# instance_executor_audit row per window, so the flip decision reads SQL,
# not grep (incident lesson #5). Two deliberate approximations, both safe
# for the binary "anyone still unauthenticated?" question:
#   * handshake POSTs (initialize/initialized ride the same /mcp path) are
#     counted too — the label says "unauthenticated POST(s)", and filtering
#     would mean parsing request bodies here;
#   * the tail window flushes on the NEXT unauthenticated call (the first
#     call always flushes immediately, deadline starts at 0) — a final
#     sub-window's counts can go unreported, never the fact that calls
#     existed.
# Enforce mode needs none of this: its unauthenticated POSTs are
# individually rejected and logged at the door.
_TOKENLESS_FLUSH_SECONDS = 60.0
# EVERY caller-controlled dimension of the aggregation is hard-capped,
# because audit mode by definition rejects nothing (round-4/5 review #1):
# declared value length (a real user_id is usr_ + hex; 64 is generous), path
# length (the middleware runs BEFORE routing, so a POST to any made-up path
# is counted first and 404s later), and key cardinality — past the cap
# everything folds into ONE constant bucket with every dimension collapsed,
# so the dict is truly bounded at _TOKENLESS_KEYS_MAX + 1 entries no matter
# which dimension an attacker varies. The total and the binary "anyone still
# unauthenticated?" signal stay exact; only per-key attribution saturates.
# (method needs no cap: the unauthed_post predicate pins it to "POST";
# port comes from scope["server"] — a server-side fact.)
_DECLARED_MAX_LEN = 64
_PATH_MAX_LEN = 128
_TOKENLESS_KEYS_MAX = 256
_OVERFLOW_KEY = ("<overflow>", "", "", 0)
# The flush log line names at most this many entries (highest counts first);
# the DB row carries them all — the log is a human signal, the row is the
# queryable worklist, and a bounded dict can still be ~257 entries wide.
_LOG_TOP_N = 20
_tokenless_counts: dict[tuple[str, str, str, int], int] = {}
_tokenless_flush_deadline: float = 0.0


def _declared_caller(headers) -> str:
    """The SELF-DECLARED user behind an unauthenticated call, or "anonymous".

    Old-broker executors are exactly the "bearer present, identity_token missing"
    shape, so the declared user_id is the actionable aggregation key —
    unverified, which is fine: this feeds an onboarding worklist, not an
    authorization decision. Reuses _mcp_identity's own readers (in-package
    private use is the round-1 boundary), which also filters placeholder
    strings — a model-guessed "current_user" listed as a real name would
    only pollute the worklist, so it reads as anonymous.
    """
    from narranexus.platform.module_system._mcp_identity import (
        USER_ID_HEADER,
        _bearer,
        _explicit_header,
        is_placeholder_user_id,
    )

    # Same two-stage semantics as caller_user_id_from_request: a placeholder
    # in the explicit header FALLS THROUGH to the bearer — a caller declaring
    # "current_user" explicitly while its bearer carries the real usr_… must
    # not drop off the worklist (round-5 review, minor #1).
    explicit = _explicit_header(headers, USER_ID_HEADER)
    if explicit and not is_placeholder_user_id(explicit):
        return explicit[:_DECLARED_MAX_LEN]
    candidate = _bearer(headers).user_id
    if candidate and not is_placeholder_user_id(candidate):
        return candidate[:_DECLARED_MAX_LEN]
    return "anonymous"


async def _note_tokenless(declared_user: str, method: str, path: str, port: int) -> None:
    global _tokenless_flush_deadline

    key = (declared_user, method, path[:_PATH_MAX_LEN], port)
    if key not in _tokenless_counts and len(_tokenless_counts) >= _TOKENLESS_KEYS_MAX:
        # ONE constant bucket, every dimension collapsed — an overflow key
        # that kept path/port would itself be a fresh key per value and the
        # cap would only hold for the caller dimension (round-5 review #1).
        key = _OVERFLOW_KEY
    _tokenless_counts[key] = _tokenless_counts.get(key, 0) + 1
    now = time.monotonic()
    if now < _tokenless_flush_deadline:
        return
    _tokenless_flush_deadline = now + _TOKENLESS_FLUSH_SECONDS
    counts = dict(_tokenless_counts)
    _tokenless_counts.clear()
    total = sum(counts.values())
    # Structured entries, not a joined string: the declared value may contain
    # anything (spaces included), and the flip decision queries this with SQL
    # — ambiguity in the serialization would corrupt the worklist. The port
    # names WHICH module server was called (one process fronts them all).
    entries = [
        {"caller": u, "method": m, "path": p, "port": pt, "n": n}
        for (u, m, p, pt), n in sorted(counts.items())
    ]
    top = sorted(entries, key=lambda e: -e["n"])[:_LOG_TOP_N]
    suffix = "" if len(entries) <= _LOG_TOP_N else f" (+{len(entries) - _LOG_TOP_N} more in the audit row)"
    logger.warning(
        f"[mcp-auth] audit: {total} unauthenticated POST(s) this window — "
        + ", ".join(
            f"{e['caller']} | {e['method']} {e['path']} :{e['port']} ×{e['n']}"
            for e in top
        )
        + suffix
    )
    try:
        from narranexus.platform.repository.executor_audit_repository import (
            ExecutorAuditRepository,
        )
        from narranexus.platform.schema.executor_audit import EVENT_MCP_AUTH_TOKENLESS
        from narranexus.platform.utils.db.db_factory import get_db_client

        db = await get_db_client()
        await ExecutorAuditRepository(db).record(
            event_type=EVENT_MCP_AUTH_TOKENLESS,
            detail={"total": total, "counts": entries},
        )
    except Exception as e:  # noqa: BLE001 — the observer must not break the observed
        logger.debug(f"[mcp-auth] tokenless audit row not written: {e}")


class IdentityAuthMiddleware:
    """Pure ASGI middleware — one instance wraps each module server's app.

    Kept ASGI-level (not Starlette BaseHTTPMiddleware) so it composes with the
    streamable-HTTP transport's custom lifespan and never re-buffers SSE
    streams.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        mode = auth_mode()
        if mode == "off":
            await self.app(scope, receive, send)
            return

        public_key = load_public_key_pem()
        if public_key is None:
            # Whole-middleware degradation, BEFORE looking at any token: with
            # no key provisioned the broker cannot have signed one either, so
            # every request arrives tokenless and enforce would take the whole
            # data plane down over a deploy misconfiguration. Fail OPEN, loudly.
            if mode == "enforce":
                logger.warning(
                    "[mcp-auth] enforce requested but no identity public key is "
                    "provisioned — FAILING OPEN (audit semantics). Fix the "
                    "deploy: mount the key and set NX_IDENTITY_PUBLIC_KEY_FILE."
                )
            await self.app(scope, receive, send)
            return

        headers = _ScopeHeaders(scope)
        from narranexus.platform.module_system.identity.verify import verify_caller_identity

        identity, reason = verify_caller_identity(headers, public_key)
        path = scope.get("path", "")
        method = scope.get("method", "")

        # One predicate for both mode branches, so a future exemption change
        # cannot drift them apart (round-3 review, minor #3).
        unauthed_post = (
            identity is None and method == "POST" and path not in _EXEMPT_PATHS
        )

        if identity is None and reason != "no-token":
            # Bad proof — log in every mode (audit's whole job).
            logger.warning(f"[mcp-auth] {mode}: {reason} method={method} path={path}")
        elif mode == "audit" and unauthed_post:
            # reason == "no-token": the measurement the audit window exists
            # for, keyed by the self-declared caller. See _note_tokenless.
            server_port = (scope.get("server") or ("", 0))[1] or 0
            await _note_tokenless(_declared_caller(headers), method, path, server_port)

        if mode == "enforce" and unauthed_post:
            if reason == "no-token":
                logger.warning(f"[mcp-auth] enforce: no token method={method} path={path}")
            await _reject(send, reason)
            return

        token = _verified_var.set(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            _verified_var.reset(token)


class _ScopeHeaders:
    """Minimal case-insensitive header lookup over a raw ASGI scope."""

    def __init__(self, scope) -> None:
        self._items = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }

    def get(self, key: str, default=None):
        return self._items.get(key.lower(), default)


async def _reject(send, reason: str) -> None:
    body = json.dumps(
        {
            "error": "identity required",
            "detail": (
                "This MCP server requires a platform-signed identity token. "
                f"Verification said: {reason}"
            ),
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------
# OwnerScopedPolicy (blueprint P1 authorization layer)
# ---------------------------------------------------------------------------

# agent_id -> (owner, monotonic deadline). An agent's owner (agents.created_by)
# never changes in practice, but a short TTL keeps this self-correcting rather
# than a second source of truth — the point is only to keep the hot tool-call
# path from adding one MySQL point-read per call.
_OWNER_CACHE_TTL_SECONDS = 60.0
_OWNER_CACHE_MAX = 4096
_owner_cache: dict[str, tuple[str, float]] = {}


async def _resolve_owner_cached(db, agent_id: str) -> Optional[str]:
    now = time.monotonic()
    cached = _owner_cache.get(agent_id)
    if cached is not None and cached[1] > now:
        return cached[0]
    from narranexus.platform.repository import AgentRepository

    owner = await AgentRepository(db).resolve_owner(agent_id)
    # Cache POSITIVE resolutions only. "" (unknown agent) and None (failed
    # lookup — the PR #258 split) both make the policy fail open, so caching
    # either would pin an "unknown → allow" verdict for 60s off one MySQL
    # hiccup, invisibly (no owner = no mcp_auth_denied row either). Unknown
    # agents re-query every call, which is fine: they are never the hot path.
    if owner:
        if len(_owner_cache) >= _OWNER_CACHE_MAX:
            # Bounded: drop expired entries; if everything is somehow live,
            # reset — correctness never depends on this cache.
            expired = [k for k, (_, dl) in _owner_cache.items() if dl <= now]
            for k in expired:
                del _owner_cache[k]
            if len(_owner_cache) >= _OWNER_CACHE_MAX:
                _owner_cache.clear()
        _owner_cache[agent_id] = (owner, now + _OWNER_CACHE_TTL_SECONDS)
    return owner


async def check_agent_ownership(agent_id: Any) -> Optional[str]:
    """The verified caller must OWN the target agent. None = allow; an error
    string = deny (enforce only).

    No verified identity / local mode / unknown agent → allow: this policy can
    only ever TIGHTEN a proven identity, never break the fail-open baseline.
    Denials are recorded to instance_executor_audit in audit AND enforce mode
    (incident lesson #5 — the DB row is the measurement that gates the
    audit→enforce flip).
    """
    # Cheapest gates first: with the default (off) / local mode, a tool call
    # must cost literally nothing extra here — verified_caller_for_tool_call
    # does a stat() + an Ed25519 verify and runs only once these pass.
    if not isinstance(agent_id, str) or not agent_id:
        return None
    from narranexus.platform.utils.deployment_mode import is_cloud_mode

    if not is_cloud_mode():
        return None
    mode = auth_mode()
    if mode == "off":
        return None
    ident = verified_caller_for_tool_call()
    if ident is None:
        return None
    from narranexus.platform.repository.executor_audit_repository import (
        ExecutorAuditRepository,
    )
    from narranexus.platform.schema.executor_audit import EVENT_MCP_AUTH_DENIED
    from narranexus.platform.utils.db.db_factory import get_db_client

    db = await get_db_client()
    owner = await _resolve_owner_cached(db, agent_id)
    if not owner or owner == ident.user_id:
        return None
    logger.warning(
        f"[mcp-auth] {mode}: token user {ident.user_id!r} does not own "
        f"agent {agent_id!r} (owner {owner!r})"
    )
    await ExecutorAuditRepository(db).record(
        event_type=EVENT_MCP_AUTH_DENIED,
        user_id=ident.user_id,
        detail={"agent_id": agent_id, "owner": owner, "mode": mode},
    )
    if mode == "enforce":
        return (
            f"Error: your verified identity ({ident.user_id}) does not own "
            f"agent {agent_id}. You can only operate your own agents."
        )
    return None


__all__ = [
    "AUTH_MODE_ENV",
    "IdentityAuthMiddleware",
    "auth_mode",
    "check_agent_ownership",
    "verified_caller",
    "verified_caller_for_tool_call",
]
