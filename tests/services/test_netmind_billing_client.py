"""
@file_name: test_netmind_billing_client.py
@author: NarraNexus
@date: 2026-07-02
@description: Unit tests for NetmindBillingClient (NetMind billing proxy).

Mocks the HTTP layer with httpx.MockTransport — no real network. Covers the
two-valued error contract (auth 401/403 -> BillingAuthError; 5xx/network ->
BillingUpstreamError) and the loginToken header contract.
"""
from __future__ import annotations

import json

import httpx
import pytest

from xyz_agent_context.services.netmind_billing_client import (
    BillingAuthError,
    BillingBusinessError,
    BillingForbiddenError,
    BillingNotFoundError,
    BillingUpstreamError,
    NetmindBillingClient,
)

_ME_FREE = {
    "plan_id": "free",
    "name": "NetMind Free",
    "quota_limits": {"rpm": 60},
    "features": {"support": False, "member_price": False},
    "monthly_grant_usd": 0.0,
    "prices": [],
    "subscription": None,
}


def _client_with(handler) -> NetmindBillingClient:
    return NetmindBillingClient(
        base_url="https://billing.test.invalid",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_get_subscription_returns_flat_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ME_FREE)

    data = await _client_with(handler).get_subscription("jwt-abc")
    assert data["plan_id"] == "free"
    assert data["subscription"] is None


@pytest.mark.asyncio
async def test_get_subscription_sends_logintoken_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("loginToken")
        seen["path"] = request.url.path
        return httpx.Response(200, json=_ME_FREE)

    await _client_with(handler).get_subscription("jwt-abc")
    assert seen["auth"] == "Bearer jwt-abc"  # custom header, Bearer prefix
    assert seen["path"] == "/v1/power-subscription/me"


@pytest.mark.asyncio
async def test_get_plans_is_public_no_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("loginToken")
        return httpx.Response(200, json={"plans": []})

    data = await _client_with(handler).get_plans()
    assert data == {"plans": []}
    assert seen["auth"] is None  # no token forwarded for public catalog


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.asyncio
async def test_auth_statuses_map_to_auth_error(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"message": "Invalid API key"})

    with pytest.raises(BillingAuthError):
        await _client_with(handler).get_subscription("bad-jwt")


@pytest.mark.asyncio
async def test_5xx_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="oops")

    with pytest.raises(BillingUpstreamError):
        await _client_with(handler).get_subscription("jwt-abc")


@pytest.mark.asyncio
async def test_network_error_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(BillingUpstreamError):
        await _client_with(handler).get_plans()


@pytest.mark.asyncio
async def test_non_json_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(BillingUpstreamError):
        await _client_with(handler).get_plans()


# --- Phase 3: subscribe / cancel / reactivate ------------------------------

@pytest.mark.asyncio
async def test_subscribe_returns_checkout_and_posts():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"session_id": "cs_1", "checkout_url": "https://x/y"})

    data = await _client_with(handler).subscribe("jwt")
    assert data["checkout_url"] == "https://x/y"
    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/power-subscription/subscribe"


@pytest.mark.asyncio
async def test_cancel_returns_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "auto_renew_off"})

    data = await _client_with(handler).cancel("jwt")
    assert data["status"] == "auto_renew_off"


@pytest.mark.asyncio
async def test_reactivate_posts_to_reactivate_path():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"status": "auto_renew_on"})

    await _client_with(handler).reactivate("jwt")
    assert seen["path"] == "/v1/power-subscription/reactivate"


@pytest.mark.asyncio
async def test_business_400_maps_to_business_error_with_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Already subscribed to Pro."})

    with pytest.raises(BillingBusinessError) as ei:
        await _client_with(handler).subscribe("jwt")
    assert ei.value.message == "Already subscribed to Pro."
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_business_400_extracts_detail_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "No active Pro subscription."})

    with pytest.raises(BillingBusinessError) as ei:
        await _client_with(handler).cancel("jwt")
    assert ei.value.message == "No active Pro subscription."


@pytest.mark.asyncio
async def test_get_fee_info_returns_body_and_hits_finance_path():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("loginToken")
        return httpx.Response(200, json={"eligible": True, "metrics": {"free_credit": "5.00"}})

    data = await _client_with(handler).get_fee_info("jwt")
    assert data["metrics"]["free_credit"] == "5.00"
    assert seen["path"] == "/v1/finance/user-fee-info"
    assert seen["auth"] == "Bearer jwt"


@pytest.mark.asyncio
async def test_get_fee_info_403_maps_to_auth_error():
    # finance signals a rejected token with 403 (not 401) — must be BillingAuthError.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Invalid API key"})

    with pytest.raises(BillingAuthError):
        await _client_with(handler).get_fee_info("jwt")


@pytest.mark.asyncio
async def test_get_records_sends_direction_and_returns_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["direction"] = request.url.params.get("direction")
        seen["page_size"] = request.url.params.get("page_size")
        return httpx.Response(200, json={
            "success": True,
            "data": [{"record_id": "r1", "direction": "expense", "amount": "0.10"}],
            "has_next": False,
        })

    body = await _client_with(handler).get_records("jwt", direction="expense")
    assert body["data"][0]["record_id"] == "r1"
    assert seen["path"] == "/v1/finance/records"
    assert seen["direction"] == "expense"
    assert seen["page_size"] == "20"


# --- Phase 4: recharge / by-session ----------------------------------------

@pytest.mark.asyncio
async def test_recharge_posts_hosted_checkout_and_returns_checkout_url():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={
            "success": True,
            "data": {
                "recharge_id": "rc_1", "session_id": "cs_1",
                "checkout_url": "https://checkout.stripe.com/c/pay/cs_1",
                "status": "pending",
            },
        })

    body = await _client_with(handler).recharge("jwt", 10, "USD")
    assert body["data"]["checkout_url"].startswith("https://checkout.stripe.com/")
    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/finance/recharge/stripe/checkout"
    # Only amount+currency are forwarded — no redirect-URL passthrough (attack
    # surface with no current use; see recharge() docstring).
    assert seen["body"] == {"amount": 10, "currency": "USD"}


@pytest.mark.asyncio
async def test_recharge_status_hits_by_session_path():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"success": True, "data": {"status": "succeeded"}})

    body = await _client_with(handler).recharge_status("jwt", "cs_abc")
    assert body["data"]["status"] == "succeeded"
    assert seen["path"] == "/v1/finance/recharge/by-session/cs_abc"


@pytest.mark.asyncio
async def test_recharge_status_403_maps_to_forbidden_not_auth():
    # by-session 403 = "not your session", distinct from a bad token.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    with pytest.raises(BillingForbiddenError):
        await _client_with(handler).recharge_status("jwt", "cs_x")


@pytest.mark.asyncio
async def test_recharge_status_404_maps_to_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "unknown session"})

    with pytest.raises(BillingNotFoundError):
        await _client_with(handler).recharge_status("jwt", "cs_missing")


@pytest.mark.asyncio
async def test_subscribe_403_still_maps_to_auth_by_default():
    # Regression: endpoints that DON'T opt in keep 403 -> BillingAuthError.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "bad token"})

    with pytest.raises(BillingAuthError):
        await _client_with(handler).subscribe("jwt")


@pytest.mark.asyncio
async def test_business_message_scrubs_token_shaped_value():
    # If the upstream echoes a JWT-shaped value under an allowed key, it must
    # NOT be passed through (defense against token/PII leak into client + logs).
    jwt = "abcdefghij0123456789.klmnopqrstuvwx.yz0123456789ABCD"
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": jwt})

    with pytest.raises(BillingBusinessError) as ei:
        await _client_with(handler).subscribe("jwt")
    assert jwt not in ei.value.message
    assert ei.value.message  # falls back to a generic string


@pytest.mark.asyncio
async def test_business_message_scrubs_id_embedded_in_sentence():
    # An opaque id embedded mid-sentence must NOT reach the client, even though
    # it isn't a full 3-segment JWT (defense against upstream echoing a
    # session/account id in a natural-language rejection).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={
            "message": "Duplicate charge for session cs_test_a1b2c3d4e5, contact support",
        })

    with pytest.raises(BillingBusinessError) as ei:
        await _client_with(handler).subscribe("jwt")
    assert "cs_test_a1b2c3d4e5" not in ei.value.message
    assert ei.value.message  # generic fallback, not empty


@pytest.mark.asyncio
async def test_business_message_keeps_plain_language():
    # No id/token shape → the human-readable message is preserved verbatim.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "No active Pro subscription."})

    with pytest.raises(BillingBusinessError) as ei:
        await _client_with(handler).cancel("jwt")
    assert ei.value.message == "No active Pro subscription."
