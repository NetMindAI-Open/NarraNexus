"""
@file_name: plugin_sdk_host.py
@author: Bin Liang
@date: 2026-09-07
@description: The backend's implementation of ``contracts.web.WebHost`` — the one seam plugin routers call for identity, ownership, CSRF, settings and egress.

Thirteen builtin routers live in plugin packages since batch 6b. Their
dependencies (``backend.auth``, ``backend.routes._ownership``,
``backend.routes._mcp_egress``, ``backend.config``, the artifact view-token
minter) did not move with them, so the packages imported the host's private
modules — a coupling a third-party plugin cannot reproduce and the API policy
forbids. This module is the honest inverse: the host keeps every
implementation where it is and publishes ONE object under
``contracts.services:WEB_HOST``; the plugins call it through
``narranexus.sdk.web``.

Registered from ``backend.main`` at import time, next to
``register_builtins_for_import``: the route table is built at import, so a
first request can arrive before the lifespan's plugin boot has run and the
seam must already be resolvable. Registration is idempotent (``replace=True``)
because ``backend.main`` is imported once per process but several test
processes build more than one app.

Nothing here contains logic. Every method delegates to the existing host
helper, on purpose: a second copy of the ownership decision is precisely the
drift that ``backend/routes/_ownership.py`` exists to prevent.
"""
from __future__ import annotations

from typing import Any, Mapping

from narranexus.contracts.services import WEB_HOST
from narranexus.contracts.web import HostSettings

HOST_OWNER = "builtin.kernel"


class BackendWebHost:
    """``contracts.web.WebHost`` over this backend process's own helpers."""

    # ── identity ──────────────────────────────────────────────────────────
    async def current_user_id(self, request: Any) -> str:
        from backend.auth import resolve_current_user_id

        return await resolve_current_user_id(request)

    def decode_session_token(self, token: str) -> Mapping[str, Any]:
        from backend.auth import decode_token

        return decode_token(token)

    def auth_error(self, code: str, detail: str, status_code: int = 401) -> Exception:
        from backend.auth_errors import AuthError

        return AuthError(code, detail, status_code)

    # ── authorization ─────────────────────────────────────────────────────
    async def require_agent_owner(self, request: Any, agent_id: str) -> None:
        from backend.routes._ownership import assert_owned

        await assert_owned(request, agent_id)

    async def check_agent_owner(self, request: Any, agent_id: str) -> str | None:
        from backend.routes._ownership import check_owned

        return await check_owned(request, agent_id)

    async def require_workspace_agent_owner(self, request: Any, agent_id: str) -> str:
        from backend.routes._ownership import require_owned_workspace_agent

        return await require_owned_workspace_agent(request, agent_id)

    async def resolve_viewer(self, request: Any) -> str:
        from backend.routes.dashboard.routes import resolve_viewer

        return await resolve_viewer(request)

    async def agent_visible(self, viewer_id: str, agent_id: str) -> Mapping[str, Any]:
        from backend.routes.dashboard.routes import assert_agent_visible

        return await assert_agent_visible(viewer_id, agent_id)

    def reject_cross_origin(self, request: Any) -> None:
        from backend.auth import reject_cross_origin

        reject_cross_origin(request)

    # ── host facts and policy ─────────────────────────────────────────────
    def settings(self) -> HostSettings:
        from backend.config import settings

        return settings

    async def filter_public_mcp_servers(self, mcp_servers: Mapping[str, Any]) -> Mapping[str, Any]:
        from backend.routes._mcp_egress import filter_public_mcp_servers

        return await filter_public_mcp_servers(dict(mcp_servers))

    def artifact_view_token(self, *, agent_id: str, artifact_id: str) -> str:
        from backend.routes.artifacts import _token

        return _token.mint(agent_id=agent_id, artifact_id=artifact_id)


def install_web_host() -> None:
    """Publish this process's ``WebHost`` on the kernel service locator."""
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    KERNEL_REGISTRIES.services.expose(WEB_HOST, BackendWebHost(), owner=HOST_OWNER, replace=True)


__all__ = ["HOST_OWNER", "BackendWebHost", "install_web_host"]
