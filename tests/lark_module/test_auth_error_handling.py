"""
@file_name: test_auth_error_handling.py
@date: 2026-09-09
@description: The Lark OAuth routes (login/complete/status) had no outer
fallback: an unexpected exception raised anywhere in the handler body
propagated all the way out of the route, and Starlette's default
ServerErrorMiddleware turns an unhandled exception into a plain-text
"Internal Server Error" 500 (GitHub #118 residue; the root-cause #120
NameError itself was already fixed elsewhere). The frontend cannot parse
a plain-text body for an `error` field, so every unrelated crash in this
handler surfaced to the user as a blank/garbled failure instead of the
`{"success": False, "error": ...}` shape every other outcome in this file
already uses.

Three things are pinned here, because the first fix got two of them wrong:

1. an unexpected exception comes back as the structured envelope;
2. the envelope never carries the exception's own text (a driver error
   names hosts and SQL) — a fixed sentence plus a trace id instead;
3. an `HTTPException` is NOT an unexpected exception: `_verify_agent_ownership`
   (`check_agent_owner` -> `_ownership.check_owned`) raises 503 on purpose
   when the ownership lookup itself fails, so a db outage produces a 5xx
   the access-log middleware can alarm on. A bare `except Exception`
   swallowed it into a 200 envelope and erased that signal (review C1).
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from narranexus_plugins.lark_module import routes as lark_routes
from narranexus_plugins.lark_module.routes import (
    AgentRequest,
    AuthCompleteRequest,
    lark_auth_complete,
    lark_auth_login,
    lark_auth_status,
)

AGENT = "agent_lark_test"
TRACE_ID = re.compile(r"^err_[0-9a-f]{8}$")


def _mock_request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(role="user"))


@pytest.fixture(autouse=True)
def _no_ownership_rejection(monkeypatch):
    """Every test here is about the fallback, not the ownership check itself —
    let the caller through unless a test overrides it."""
    monkeypatch.setattr(lark_routes, "_verify_agent_ownership", AsyncMock(return_value=None))
    monkeypatch.setattr(lark_routes, "_get_db", AsyncMock(return_value=object()))


def _assert_envelope_without_leak(result: dict) -> None:
    assert isinstance(result, dict)
    assert result["success"] is False
    assert isinstance(result["error"], str) and result["error"]
    # The exception's own text must not reach the client...
    assert "boom" not in result["error"]
    # ...but the trace id that joins the response to the log line must.
    assert TRACE_ID.match(result["trace_id"])
    assert result["trace_id"] in result["error"]


# ── an unexpected exception → the structured envelope ──────────────────────


@pytest.mark.asyncio
async def test_login_survives_an_unexpected_exception():
    with patch.object(lark_routes, "LarkCredentialManager", side_effect=RuntimeError("unexpected boom")):
        result = await lark_auth_login(_mock_request(), AgentRequest(agent_id=AGENT))
    _assert_envelope_without_leak(result)


@pytest.mark.asyncio
async def test_complete_survives_an_unexpected_exception():
    with patch.object(lark_routes, "LarkCredentialManager", side_effect=RuntimeError("unexpected boom")):
        result = await lark_auth_complete(
            _mock_request(),
            AuthCompleteRequest(agent_id=AGENT, device_code="abc123"),
        )
    _assert_envelope_without_leak(result)


@pytest.mark.asyncio
async def test_status_survives_an_unexpected_exception():
    with patch.object(lark_routes, "LarkCredentialManager", side_effect=RuntimeError("unexpected boom")):
        result = await lark_auth_status(_mock_request(), agent_id=AGENT)
    _assert_envelope_without_leak(result)


@pytest.mark.asyncio
async def test_two_crashes_get_two_trace_ids():
    """The id must identify ONE failure, or support cannot join it to a log line."""
    with patch.object(lark_routes, "LarkCredentialManager", side_effect=RuntimeError("unexpected boom")):
        first = await lark_auth_status(_mock_request(), agent_id=AGENT)
        second = await lark_auth_status(_mock_request(), agent_id=AGENT)
    assert first["trace_id"] != second["trace_id"]


# ── HTTPException (a typed rejection, not an "unexpected exception") must ──
# ── keep its own status code — never get downgraded to a 200 envelope ─────

_DB_DOWN = HTTPException(status_code=503, detail="Ownership check unavailable (database error).")


@pytest.mark.asyncio
async def test_login_does_not_downgrade_a_503_ownership_failure_to_200():
    with patch.object(lark_routes, "_verify_agent_ownership", AsyncMock(side_effect=_DB_DOWN)):
        with pytest.raises(HTTPException) as exc_info:
            await lark_auth_login(_mock_request(), AgentRequest(agent_id=AGENT))
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_complete_does_not_downgrade_a_503_ownership_failure_to_200():
    with patch.object(lark_routes, "_verify_agent_ownership", AsyncMock(side_effect=_DB_DOWN)):
        with pytest.raises(HTTPException) as exc_info:
            await lark_auth_complete(
                _mock_request(),
                AuthCompleteRequest(agent_id=AGENT, device_code="abc123"),
            )
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_status_does_not_downgrade_a_503_ownership_failure_to_200():
    with patch.object(lark_routes, "_verify_agent_ownership", AsyncMock(side_effect=_DB_DOWN)):
        with pytest.raises(HTTPException) as exc_info:
            await lark_auth_status(_mock_request(), agent_id=AGENT)
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_an_auth_error_keeps_its_status_too():
    """The host's `AuthError` IS an HTTPException (401/403 by construction);
    an identity rejection must not become a 200 "permission" envelope."""
    from backend.auth_errors import AuthError

    rejected = AuthError(code="token_expired", detail="session expired", status_code=401)
    with patch.object(lark_routes, "_verify_agent_ownership", AsyncMock(side_effect=rejected)):
        with pytest.raises(HTTPException) as exc_info:
            await lark_auth_status(_mock_request(), agent_id=AGENT)
    assert exc_info.value.status_code == 401


# ── through FastAPI: the decorated route still builds and the 503 is on the wire


def test_over_http_a_503_is_a_503_and_a_crash_is_a_200_envelope():
    """The wrapper pins a resolved `__signature__` so FastAPI can build the
    route from the decorated function (string annotations would otherwise be
    evaluated in the wrapper's module). Both halves of the contract are
    asserted over an actual request, which is where the status code lives."""
    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = "u1"
        return await call_next(request)

    app.include_router(lark_routes.router, prefix="/api/lark")
    client = TestClient(app, raise_server_exceptions=False)

    with patch.object(lark_routes, "_verify_agent_ownership", AsyncMock(side_effect=_DB_DOWN)):
        r = client.get("/api/lark/auth/status", params={"agent_id": AGENT})
    assert r.status_code == 503

    with patch.object(lark_routes, "LarkCredentialManager", side_effect=RuntimeError("unexpected boom")):
        r = client.get("/api/lark/auth/status", params={"agent_id": AGENT})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    _assert_envelope_without_leak(r.json())
