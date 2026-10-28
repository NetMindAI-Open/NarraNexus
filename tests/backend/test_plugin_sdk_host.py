"""
@file_name: test_plugin_sdk_host.py
@author: Bin Liang
@date: 2026-09-07
@description: The backend publishes ONE WebHost at import time and every method delegates to the host helper that already owns that decision.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from narranexus.contracts.services import WEB_HOST
from narranexus.contracts.web import AuthError, WebHost
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES


class _Req:
    """The two things every seam method reads off a request."""

    def __init__(self, user_id: str | None = None, headers: dict[str, str] | None = None, query: dict[str, str] | None = None):
        self.state = type("S", (), {"user_id": user_id})()
        self.headers = headers or {}
        self.query_params = query or {}


def test_importing_backend_main_publishes_the_web_host():
    # Import-time, not lifespan: the route table is built at import and a
    # request can arrive before the lifespan boot has run.
    import backend.main  # noqa: F401

    host = KERNEL_REGISTRIES.services.require(WEB_HOST)
    assert isinstance(host, WebHost)
    assert KERNEL_REGISTRIES.services.owner_of(WEB_HOST) == "builtin.kernel"


def test_installing_twice_is_idempotent():
    from backend.plugin_sdk_host import install_web_host

    install_web_host()
    install_web_host()  # several TestClients per process must not RegistryConflict
    assert KERNEL_REGISTRIES.services.try_require(WEB_HOST) is not None


@pytest.fixture
def host() -> WebHost:
    import backend.main  # noqa: F401

    return KERNEL_REGISTRIES.services.require(WEB_HOST)


async def test_current_user_id_and_its_auth_error(host: WebHost):
    assert await host.current_user_id(_Req(user_id="u1")) == "u1"
    with pytest.raises(AuthError) as e:
        await host.current_user_id(_Req(user_id=None))
    assert e.value.code == "identity_unresolved"  # NOT session death — see backend/auth_errors.py


def test_auth_error_builds_the_hosts_own_class(host: WebHost):
    from backend.auth_errors import AuthError as HostAuthError

    err = host.auth_error("token_expired", "Token expired")
    # It must be the concrete class the exception handler is registered on;
    # the contract base alone would render without the `code` field.
    assert type(err) is HostAuthError


async def test_ownership_denies_a_non_owner_on_both_surfaces(host: WebHost, monkeypatch):
    class _Repo:
        def __init__(self, db):
            pass

        async def resolve_owner(self, agent_id: str) -> str:
            return {"mine": "u1", "theirs": "u2", "ghost": ""}[agent_id]

    import backend.routes._ownership as own

    monkeypatch.setattr(own, "AgentRepository", _Repo)
    monkeypatch.setattr(own, "get_db_client", lambda: _noop())
    req = _Req(user_id="u1")

    await host.require_agent_owner(req, "mine")
    assert await host.check_agent_owner(req, "mine") is None

    with pytest.raises(HTTPException) as e:
        await host.require_agent_owner(req, "theirs")
    assert e.value.status_code == 403
    assert "Permission denied" in (await host.check_agent_owner(req, "theirs") or "")

    with pytest.raises(HTTPException) as e:
        await host.require_agent_owner(req, "ghost")
    assert e.value.status_code == 404  # unknown agent, not "not yours"


async def _noop():
    return object()


async def test_resolve_viewer_refuses_a_user_id_query_param(host: WebHost):
    assert await host.resolve_viewer(_Req(user_id="u1")) == "u1"
    with pytest.raises(HTTPException) as e:
        await host.resolve_viewer(_Req(user_id="u1", query={"user_id": "u2"}))
    assert e.value.status_code == 400  # the viewer is the session, never a query param


def test_reject_cross_origin_is_the_hosts_csrf_guard(host: WebHost):
    host.reject_cross_origin(_Req(headers={}))  # no Origin (CLI / same-origin) → allowed
    for headers in ({"origin": "null"}, {"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"}):
        with pytest.raises(HTTPException) as e:
            host.reject_cross_origin(_Req(headers=headers))
        assert e.value.status_code == 403


def test_settings_and_artifact_token_come_from_the_host(host: WebHost):
    from backend.config import settings
    from backend.routes.artifacts import _token

    assert host.settings().max_upload_bytes == settings.max_upload_bytes
    token = host.artifact_view_token(agent_id="a1", artifact_id="art1")
    claims = _token.verify(token)  # the host mints what the host's public raw route verifies
    assert (claims.agent_id, claims.artifact_id) == ("a1", "art1")


async def test_mcp_egress_filter_is_the_hosts_policy(host: WebHost, monkeypatch):
    import backend.routes._mcp_egress as egress

    monkeypatch.setattr(egress, "is_cloud_mode", lambda: True)

    async def _check(url: str) -> None:
        if "internal" in url:
            raise ValueError("internal")

    monkeypatch.setattr(egress, "assert_public_http_url", _check)
    kept = await host.filter_public_mcp_servers({"ok": {"url": "https://x/mcp"}, "bad": {"url": "https://internal/mcp"}})
    assert set(kept) == {"ok"}
