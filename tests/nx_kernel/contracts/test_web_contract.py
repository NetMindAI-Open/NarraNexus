"""
@file_name: test_web_contract.py
@author: Bin Liang
@date: 2026-09-07
@description: The `web` contract: the WebHost surface plugin routers call, its service ref, the AuthError base a plugin may catch, and its honest ALPHA grading.
"""
from __future__ import annotations

import inspect

import pytest

from narranexus.contracts import API_VERSIONS, STABILITY, Stability
from narranexus.contracts.services import WEB_HOST
from narranexus.contracts.web import (
    IDENTITY_UNRESOLVED,
    TOKEN_EXPIRED,
    TOKEN_INVALID,
    AuthError,
    HostSettings,
    WebHost,
)


def test_the_web_host_ref_is_a_contract_not_a_kernel_detail():
    # Naming a host service must not require editing narranexus.kernel
    # (charter 2.9): the ref lives in the contract package with the Protocol
    # it keys.
    assert WEB_HOST.id == "host.web"
    assert WEB_HOST.__module__ == "narranexus.contracts.services"


def test_the_surface_is_exactly_what_a_router_needs():
    # Concrete membership: silently dropping a method would otherwise only
    # surface as an AttributeError inside somebody's route handler.
    members = {n for n, _ in inspect.getmembers(WebHost, predicate=inspect.isfunction) if not n.startswith("_")}
    assert members == {
        "current_user_id",
        "decode_session_token",
        "auth_error",
        "require_agent_owner",
        "require_workspace_agent_owner",
        "check_agent_owner",
        "resolve_viewer",
        "agent_visible",
        "reject_cross_origin",
        "settings",
        "filter_public_mcp_servers",
        "artifact_view_token",
    }


def test_ownership_is_request_scoped_not_user_id_scoped():
    # The local/cloud identity difference is encapsulated in the host
    # middleware; a seam that took a pre-extracted user_id would force every
    # plugin to reimplement it (and local mode's deliberate no-enforcement).
    for name in (
        "require_agent_owner",
        "require_workspace_agent_owner",
        "check_agent_owner",
        "current_user_id",
        "resolve_viewer",
    ):
        assert list(inspect.signature(getattr(WebHost, name)).parameters)[1] == "request"


def test_a_plugin_can_catch_auth_error_without_importing_the_host():
    from backend.auth_errors import AuthError as HostAuthError

    assert issubclass(HostAuthError, AuthError)
    err = HostAuthError(IDENTITY_UNRESOLVED, "Authentication required")
    assert isinstance(err, AuthError)
    assert (err.code, err.status_code, err.detail) == (IDENTITY_UNRESOLVED, 401, "Authentication required")
    # 403 for the authenticated-but-not-permitted case must survive the base class.
    assert HostAuthError("account_suspended", "no", 403).status_code == 403


def test_the_codes_a_plugin_may_emit_are_the_host_vocabulary():
    import backend.auth_errors as host

    assert (TOKEN_EXPIRED, TOKEN_INVALID, IDENTITY_UNRESOLVED) == (
        host.TOKEN_EXPIRED,
        host.TOKEN_INVALID,
        host.IDENTITY_UNRESOLVED,
    )


def test_host_settings_is_deliberately_narrow():
    # Widening it to backend.config.Settings would make every field on that
    # object a contract; growing this Protocol is meant to be a decision.
    assert set(HostSettings.__annotations__) == {"max_upload_bytes"}
    from backend.config import settings

    assert isinstance(settings.max_upload_bytes, int)


def test_the_new_authoring_surfaces_are_graded_alpha_not_stable():
    assert API_VERSIONS["web"] == 0 and API_VERSIONS["channel_authoring"] == 0
    assert STABILITY["web"] is Stability.ALPHA
    assert STABILITY["channel_authoring"] is Stability.ALPHA


def test_the_sdk_facade_fails_loud_when_the_process_is_not_an_http_host():
    from narranexus.contracts import UnknownEntry
    from narranexus.kernel.plugins.services import ServiceLocator

    import narranexus.sdk.web as sdk_web

    # A worker/MCP process that imports a router must get an error, never a
    # silent "allow" — a seam that degrades open turns every route unauthenticated.
    locator = ServiceLocator()
    with pytest.raises(UnknownEntry):
        locator.require(WEB_HOST)
    assert sdk_web.host is not None
