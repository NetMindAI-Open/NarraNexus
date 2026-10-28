"""
@file_name: web.py
@author: Bin Liang
@date: 2026-09-07
@description: Contract for the request-scoped host API a plugin router needs (identity, ownership, CSRF, host settings, egress).

Why this exists
---------------
Thirteen builtin routers moved into plugin packages in batch 6b, but their
dependencies did not move with them: they kept importing
``backend.routes._ownership``, ``backend.auth``, ``backend.auth_errors`` and
``backend.config`` — six of those are underscore-private modules. A plugin
reaching into the host's private modules is exactly what
``docs/API_POLICY.md`` §1 forbids, and it is worse than the pre-move state:
a third party copying ``builtin.job`` (the documented router sample) cannot
import any of it, so their only options are to rewrite ownership checks by
hand (the 2026-08-12 IDOR batch came from exactly that) or to depend on a
private module of ours.

The seam
--------
``WebHost`` is everything a router legitimately needs from the HTTP host.
The host registers ONE implementation on the service locator under
``narranexus.contracts.services:WEB_HOST`` at boot; plugins call it through
``narranexus.sdk.web``. Every method is request-scoped on purpose: identity
and ownership are properties of a request, never of a process, so the seam
takes the request object rather than a pre-extracted user id — a plugin that
had to extract the id itself would have to reimplement the local-mode /
cloud-mode difference the host middleware exists to hide.

``request`` is typed ``Any`` so this package stays framework-free (the same
reason ``contracts.route.RouterSpec.router`` is ``Any``); the backend host
passes a Starlette ``Request``.

Contract version: ``API_VERSIONS["web"]``. Stability: ALPHA — the surface is
one batch old and the ownership/visibility split is still being reviewed.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

# ── The auth-code vocabulary a plugin may emit ────────────────────────────
# The host owns the full vocabulary (backend/auth_errors.py) and the rule for
# which codes end the SPA session; these three are the ones plugin code has a
# legitimate reason to raise. Adding one here is a contract change.
TOKEN_EXPIRED = "token_expired"
TOKEN_INVALID = "token_invalid"
IDENTITY_UNRESOLVED = "identity_unresolved"


class AuthError(Exception):
    """Base class of every auth rejection the host renders as ``{detail, code}``.

    A plugin never constructs this: it calls ``auth_error(code, detail)`` on
    the host, which returns the host's own concrete subclass so the host's
    exception handler (and its rejection logging) still applies. This base
    exists so a plugin can ``except AuthError`` without importing the host.
    """

    code: str = ""
    status_code: int = 401


# ── The upload ceiling's source of truth ──────────────────────────────────
# ``HostSettings.max_upload_bytes`` is deployment-wide, not request-scoped:
# every process role (backend, workers, MCP) reads the same env var with the
# same default. The backend's ``config.settings`` and the SDK's no-HTTP-host
# fallback both resolve through these two names so they cannot drift.
MAX_UPLOAD_BYTES_ENV = "MAX_UPLOAD_BYTES"
DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@runtime_checkable
class HostSettings(Protocol):
    """The host's deployment-wide settings a plugin may read.

    Deliberately tiny: it holds only what plugin code actually consumes today
    (the upload ceiling, read by six channel triggers plus teams and skills).
    Growing it is a contract change, which is the point — the alternative was
    eight plugins importing ``backend.config.settings`` and coupling to every
    field on it.
    """

    max_upload_bytes: int


@runtime_checkable
class WebHost(Protocol):
    """The host API a plugin router calls. One implementation per process, registered at boot."""

    # ── identity ──────────────────────────────────────────────────────────
    async def current_user_id(self, request: Any) -> str:
        """The authenticated caller. Raises the host's ``AuthError`` when the middleware left no identity."""

    def decode_session_token(self, token: str) -> Mapping[str, Any]:
        """Verify + decode a host session JWT. Raises the ``jwt`` library's own errors.

        Only an ``auth`` provider plugin has a reason to call this — it is the
        one plugin kind that must speak the host's session format.
        """

    def auth_error(self, code: str, detail: str, status_code: int = 401) -> Exception:
        """Build the host's auth rejection for ``code`` — raise the result."""

    # ── authorization ─────────────────────────────────────────────────────
    async def require_agent_owner(self, request: Any, agent_id: str) -> None:
        """Deny (raise 404/403, 503 on lookup failure) unless the caller owns ``agent_id``."""

    async def check_agent_owner(self, request: Any, agent_id: str) -> str | None:
        """Same decision as ``require_agent_owner``, as an error string or None.

        Two surfaces because the channel routes answer with a
        ``{"success": False, "error": ...}`` payload rather than an HTTP error;
        both map from ONE decision in the host, so neither parses the other's
        prose.
        """

    async def require_workspace_agent_owner(self, request: Any, agent_id: str) -> str:
        """For routes that turn ``agent_id`` into a workspace DIRECTORY: identity
        required (401), path-unsafe or unknown or not-owned all 404 (no
        existence oracle), 503 on lookup failure. Returns the caller's user_id."""

    async def resolve_viewer(self, request: Any) -> str:
        """The dashboard viewer: ``current_user_id`` plus the refusal of a ``user_id`` query param."""

    async def agent_visible(self, viewer_id: str, agent_id: str) -> Mapping[str, Any]:
        """The agent row when ``viewer_id`` may SEE it (owned or public); 404 otherwise, without leaking existence."""

    def reject_cross_origin(self, request: Any) -> None:
        """CSRF guard for unauthenticated local-mode writes. Raises 403 on a cross-site request."""

    # ── host facts and policy ─────────────────────────────────────────────
    def settings(self) -> HostSettings:
        """The host's deployment settings."""

    async def filter_public_mcp_servers(self, mcp_servers: Mapping[str, Any]) -> Mapping[str, Any]:
        """Drop MCP servers whose URL fails the host's SSRF egress check (cloud only, fail-closed)."""

    def artifact_view_token(self, *, agent_id: str, artifact_id: str) -> str:
        """Mint a short-TTL view token for the host's public artifact raw route.

        The token IS the auth for a route the HOST serves, so minting it is a
        host service — a plugin holding the signing secret would be a second
        copy of the host's trust boundary.
        """


__all__ = [
    "DEFAULT_MAX_UPLOAD_BYTES",
    "IDENTITY_UNRESOLVED",
    "MAX_UPLOAD_BYTES_ENV",
    "TOKEN_EXPIRED",
    "TOKEN_INVALID",
    "AuthError",
    "HostSettings",
    "WebHost",
]
