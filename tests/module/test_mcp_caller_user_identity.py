"""
@file_name: test_mcp_caller_user_identity.py
@author: Bin Liang
@date: 2026-08-04
@description: user_id rides the same caller-identity channel as agent_id (W1).

The remaining half of the shared-server identity problem: ``user_id`` is
also a normal tool parameter the MODEL fills. A wrong fill on job_create is
the worst failure shape we have — the job IS created (success=True), but
under the wrong user, so the owner's Jobs list stays empty forever while
the agent reports success ("假成功").

The user_id discipline is DELIBERATELY weaker than agent_id's:
- placeholder ("user_current", ...) → injected turn owner replaces it
- ``None`` / absent → left alone. Retrieval tools use user_id=None as a
  legitimate "no filter" value; injecting there would silently change
  query semantics.
- mismatching real value → KEPT, with a warning as the audit trail.
  Unlike agent identity (the platform launched you — one truth), a
  different user_id can be legitimate in multi-user flows; we measure
  before we override (PR #230 discipline).
- bearer: user_id is field 5 of the positional record — old 4-field
  bearers must still parse (codex carries identity ONLY via bearer;
  header-only would leave codex a degraded consumer, the #229 lesson).
"""
from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest

from xyz_agent_context.module._mcp_identity import (
    AGENT_ID_HEADER,
    BEARER_AGENT_PREFIX,
    USER_ID_HEADER,
    agent_id_headers,
    caller_user_id_from_request,
    install_caller_identity,
    is_placeholder_user_id,
    resolve_caller_user_id,
)

AGENT = "agent_39b2b72b823b"
USER = "binliang"
OTHER_USER = "user_someone_else"


class _Headers(dict):
    def get(self, key, default=None):  # noqa: D102
        return super().get(key.lower(), default)


@contextlib.contextmanager
def injected(headers: dict | None):
    from mcp.server.lowlevel.server import request_ctx

    if headers is None:
        token = request_ctx.set(type("Ctx", (), {"request": None})())
    else:
        request = type("Req", (), {"headers": _Headers({k.lower(): v for k, v in headers.items()})})()
        token = request_ctx.set(type("Ctx", (), {"request": request})())
    try:
        yield
    finally:
        request_ctx.reset(token)


# ---------------------------------------------------------------------------
# Placeholder detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [
    "user_current", "current_user", "current", "me", "self",
    "user_id", "{user_id}", "<user_id>", "requesting_user",
    "", "   ",
])
def test_user_placeholders_are_recognised(value):
    assert is_placeholder_user_id(value) is True


@pytest.mark.parametrize("value", [USER, "user_abc123", "briefing_demo_03"])
def test_real_user_ids_are_not_placeholders(value):
    assert is_placeholder_user_id(value) is False


def test_none_is_not_a_user_placeholder():
    """None is a legitimate 'no filter' value — never treated as a guess."""
    assert is_placeholder_user_id(None) is False


# ---------------------------------------------------------------------------
# Reading the injected user
# ---------------------------------------------------------------------------


def test_user_read_from_explicit_header():
    with injected({USER_ID_HEADER: USER}):
        assert caller_user_id_from_request() == USER


def test_user_read_from_bearer_field_five():
    bearer = f"Bearer {BEARER_AGENT_PREFIX}{AGENT}~chat~~~{USER}"
    with injected({"Authorization": bearer}):
        assert caller_user_id_from_request() == USER


def test_old_four_field_bearer_still_parses_as_no_user():
    bearer = f"Bearer {BEARER_AGENT_PREFIX}{AGENT}~chat"
    with injected({"Authorization": bearer}):
        assert caller_user_id_from_request() is None


def test_headers_builder_emits_user_on_both_channels():
    hdrs = agent_id_headers(AGENT, turn_source="chat", user_id=USER)
    assert hdrs[USER_ID_HEADER] == USER
    assert hdrs["Authorization"].endswith(f"~{USER}")
    with injected(hdrs):
        assert caller_user_id_from_request() == USER


def test_headers_builder_omits_user_when_unknown():
    hdrs = agent_id_headers(AGENT, turn_source="chat")
    assert USER_ID_HEADER not in hdrs
    with injected(hdrs):
        assert caller_user_id_from_request() is None


# ---------------------------------------------------------------------------
# Resolution: placeholder → inject; None → untouched; mismatch → kept
# ---------------------------------------------------------------------------


def test_placeholder_user_is_replaced_by_injected():
    with injected({USER_ID_HEADER: USER}):
        assert resolve_caller_user_id("user_current") == USER


def test_none_user_is_left_alone_even_with_injection():
    with injected({USER_ID_HEADER: USER}):
        assert resolve_caller_user_id(None) is None


def test_none_user_does_not_read_request_headers():
    with patch(
        "xyz_agent_context.module._mcp_identity.caller_user_id_from_request"
    ) as read_injected:
        assert resolve_caller_user_id(None) is None
    read_injected.assert_not_called()


def test_mismatching_user_is_kept_not_overridden():
    with injected({USER_ID_HEADER: USER}):
        assert resolve_caller_user_id(OTHER_USER) == OTHER_USER


def test_without_injection_everything_passes_through():
    assert resolve_caller_user_id("user_current") == "user_current"
    assert resolve_caller_user_id(USER) == USER


# ---------------------------------------------------------------------------
# Wrapped tools resolve user_id before the body runs
# ---------------------------------------------------------------------------


def _server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("user-identity-test")

    @mcp.tool()
    async def needs_both(agent_id: str, user_id: str, title: str) -> dict:
        return {"agent_id": agent_id, "user_id": user_id, "title": title}

    @mcp.tool()
    async def optional_user_filter(agent_id: str, user_id: str | None = None) -> dict:
        return {"agent_id": agent_id, "user_id": user_id}

    install_caller_identity(mcp)
    return mcp


def _fn(mcp, name):
    return {t.name: t for t in mcp._tool_manager.list_tools()}[name].fn


@pytest.mark.asyncio
async def test_wrapped_tool_replaces_placeholder_user():
    fn = _fn(_server(), "needs_both")
    with injected(agent_id_headers(AGENT, user_id=USER)):
        out = await fn(agent_id="agent_current", user_id="user_current", title="t")
    assert out == {"agent_id": AGENT, "user_id": USER, "title": "t"}


@pytest.mark.asyncio
async def test_wrapped_tool_keeps_explicit_none_filter():
    """user_id=None means 'unfiltered query' — injection must not scope it."""
    fn = _fn(_server(), "optional_user_filter")
    with injected(agent_id_headers(AGENT, user_id=USER)):
        out = await fn(agent_id=AGENT)
    assert out == {"agent_id": AGENT, "user_id": None}


@pytest.mark.asyncio
async def test_wrapped_tool_keeps_mismatching_real_user():
    fn = _fn(_server(), "needs_both")
    with injected(agent_id_headers(AGENT, user_id=USER)):
        out = await fn(agent_id=AGENT, user_id=OTHER_USER, title="t")
    assert out["user_id"] == OTHER_USER
