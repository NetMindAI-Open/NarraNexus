"""
@file_name: test_wallet_client.py
@description: NarraNexus's view of the deploy-side wallet service.

The error SPLIT is what matters here. Callers act differently on each:
transport trouble is retried next login, a rejected token is a deployment bug
that must be loud, and "no wallet" is an ordinary state a new user is in for a
few seconds. Collapsing them into one exception is how a misconfiguration ends
up looking like "the user has no free tier".
"""
from __future__ import annotations

import httpx
import pytest

from xyz_agent_context.integrations.free_tier.wallet_client import (
    WalletClient,
    WalletDenied,
    WalletMissing,
    WalletUnavailable,
)


def _client(handler) -> WalletClient:
    return WalletClient(
        "http://quota-api", "tok", transport=httpx.MockTransport(handler)
    )


@pytest.mark.asyncio
async def test_provision_returns_the_one_time_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json={
            "user_id": "u1", "created": True, "api_key": "sk-wallet",
            "wallet": {"currency": "USD", "max_budget": 10.0, "spend": 0.0,
                       "remaining": 10.0, "exhausted": False},
        })

    result = await _client(handler).provision("u1")
    assert result.created is True
    assert result.api_key == "sk-wallet"
    assert result.balance.remaining == 10.0


@pytest.mark.asyncio
async def test_provision_of_an_existing_wallet_yields_no_key():
    """The secret is shown once. A caller that finds `api_key is None` must NOT
    treat it as a usable provisioning result."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "user_id": "u1", "created": False,
            "wallet": {"currency": "USD", "max_budget": 10.0, "spend": 4.0,
                       "remaining": 6.0, "exhausted": False},
        })

    result = await _client(handler).provision("u1")
    assert result.created is False
    assert result.api_key is None


@pytest.mark.asyncio
async def test_balance_parses_the_exhausted_flag():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "currency": "USD", "max_budget": 10.0, "spend": 10.0,
            "remaining": 0.0, "exhausted": True,
        })

    balance = await _client(handler).balance("u1")
    assert balance.exhausted is True
    assert balance.remaining == 0.0


@pytest.mark.asyncio
async def test_404_is_missing_not_a_failure():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "no wallet"})

    with pytest.raises(WalletMissing):
        await _client(handler).balance("u1")


@pytest.mark.asyncio
async def test_401_is_denied_so_the_misconfiguration_is_visible():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Invalid service token"})

    with pytest.raises(WalletDenied):
        await _client(handler).balance("u1")


@pytest.mark.asyncio
async def test_5xx_and_transport_errors_are_unavailable():
    def failing(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "gateway down"})

    with pytest.raises(WalletUnavailable):
        await _client(failing).balance("u1")

    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(WalletUnavailable):
        await _client(boom).balance("u1")


@pytest.mark.asyncio
async def test_non_json_body_is_unavailable_not_a_crash():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>proxy error</html>")

    with pytest.raises(WalletUnavailable):
        await _client(handler).balance("u1")


def test_from_settings_is_none_when_unconfigured(monkeypatch):
    """Local mode / free tier off: every caller branches on this single None."""
    monkeypatch.delenv("FREE_TIER_WALLET_API_URL", raising=False)
    monkeypatch.delenv("FREE_TIER_WALLET_API_TOKEN", raising=False)
    assert WalletClient.from_settings() is None

    monkeypatch.setenv("FREE_TIER_WALLET_API_URL", "http://quota-api:8110")
    assert WalletClient.from_settings() is None  # token still missing

    monkeypatch.setenv("FREE_TIER_WALLET_API_TOKEN", "tok")
    assert WalletClient.from_settings() is not None
