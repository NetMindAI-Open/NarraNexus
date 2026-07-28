"""
@file_name: test_netmind_auth_client.py
@author: NarraNexus
@date: 2026-06-11
@description: Unit tests for NetmindAuthClient (NetMind token verification).

NetMind JWTs cannot be verified offline (per-user signing factor lives in
NetMind's DB), so the client delegates verification to POST /user/balance,
mirroring Arena's integration. These tests mock the HTTP layer with
httpx.MockTransport — no real network.

Covers:
- happy path: token accepted, identity fields extracted + normalised
- request contract: header name is `token` (NOT Authorization), Bearer prefix
- invalid token (4xx / success:false envelope) -> NetmindAuthError
- upstream trouble (network error / 5xx / malformed body) -> NetmindUpstreamError
- dev-bypass: double switch (env + token prefix), never hits the network
"""
from __future__ import annotations

import json

import httpx
import pytest

from backend.integrations.netmind.netmind_auth_client import (
    NetmindAuthClient,
    NetmindAuthError,
    NetmindUpstreamError,
)


_BALANCE_OK = {
    "data": {
        "user": {
            "email": "  Alice@Example.COM ",
            "userSystemCode": "a" * 32,
            "nickName": "Alice",
            "userHeadImage": "https://cdn.netmind.ai/a.png",
            "loginToken": "sensitive-should-not-be-needed",
        },
        "userAccount": {},
    }
}


def _client_with(handler) -> NetmindAuthClient:
    transport = httpx.MockTransport(handler)
    return NetmindAuthClient(
        base_url="https://userauth.test.invalid", transport=transport
    )


@pytest.mark.asyncio
async def test_verify_token_extracts_normalised_identity():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_BALANCE_OK)

    user = await _client_with(handler).verify_token("jwt-abc")

    assert user.user_system_code == "a" * 32
    assert user.email == "alice@example.com"  # lowercased + trimmed
    assert user.nickname == "Alice"
    assert user.avatar_url == "https://cdn.netmind.ai/a.png"


@pytest.mark.asyncio
async def test_verify_token_sends_netmind_token_header_to_balance():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["token_header"] = request.headers.get("token")
        seen["auth_header"] = request.headers.get("authorization")
        return httpx.Response(200, json=_BALANCE_OK)

    await _client_with(handler).verify_token("jwt-abc")

    assert seen["url"].endswith("/user/balance")
    # NetMind convention: custom header named `token`, not Authorization.
    assert seen["token_header"] == "Bearer jwt-abc"
    assert seen["auth_header"] is None


@pytest.mark.asyncio
async def test_verify_token_rejects_invalid_token_on_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid token"})

    with pytest.raises(NetmindAuthError):
        await _client_with(handler).verify_token("expired-jwt")


@pytest.mark.asyncio
async def test_verify_token_rejects_success_false_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "message": "bad"})

    with pytest.raises(NetmindAuthError):
        await _client_with(handler).verify_token("bad-jwt")


@pytest.mark.asyncio
async def test_verify_token_maps_network_error_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(NetmindUpstreamError):
        await _client_with(handler).verify_token("jwt-abc")


@pytest.mark.asyncio
async def test_verify_token_maps_5xx_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="nope")

    with pytest.raises(NetmindUpstreamError):
        await _client_with(handler).verify_token("jwt-abc")


@pytest.mark.asyncio
async def test_verify_token_5xx_with_success_false_is_auth_error():
    # A token NetMind explicitly rejects is the user's problem (401), even
    # when NetMind wraps the rejection in a 5xx (observed: a non-NetMind JWT
    # yields 500 carrying the {success:false} envelope). Must NOT look like
    # an upstream outage (502).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={"success": False, "errorcode": "NOT_LOGGEDIN"}
        )

    with pytest.raises(NetmindAuthError):
        await _client_with(handler).verify_token("garbage-jwt")


@pytest.mark.asyncio
async def test_verify_token_5xx_without_envelope_stays_upstream_error():
    # A genuine server error with no NetMind envelope stays 502.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<html>Internal Server Error</html>")

    with pytest.raises(NetmindUpstreamError):
        await _client_with(handler).verify_token("jwt-abc")


@pytest.mark.asyncio
async def test_verify_token_treats_missing_identity_fields_as_upstream_error():
    # Contract drift on NetMind's side must not look like a user auth failure.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"user": {"email": ""}}})

    with pytest.raises(NetmindUpstreamError):
        await _client_with(handler).verify_token("jwt-abc")


@pytest.mark.asyncio
async def test_dev_bypass_requires_env_and_prefix(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={})

    # Switch ON + prefix -> bypass, no network.
    monkeypatch.setenv("NETMIND_DEV_BYPASS", "1")
    user = await _client_with(handler).verify_token("dev-bypass-bob@test.dev")
    assert user.email == "bob@test.dev"
    assert user.user_system_code.startswith("devbp_")
    assert calls["n"] == 0

    # Switch OFF + prefix -> treated as a normal (invalid) token.
    monkeypatch.delenv("NETMIND_DEV_BYPASS")
    with pytest.raises(NetmindAuthError):
        await _client_with(handler).verify_token("dev-bypass-bob@test.dev")
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_dev_bypass_same_email_is_deterministic(monkeypatch):
    monkeypatch.setenv("NETMIND_DEV_BYPASS", "1")

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not hit network")

    c = _client_with(handler)
    u1 = await c.verify_token("dev-bypass-carol@test.dev")
    u2 = await c.verify_token("dev-bypass-carol@test.dev")
    assert u1.user_system_code == u2.user_system_code
