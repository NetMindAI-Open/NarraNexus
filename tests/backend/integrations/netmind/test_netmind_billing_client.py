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

from backend.integrations.netmind.netmind_billing_client import (
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

    body = await _client_with(handler).recharge("jwt", 10)
    assert body["data"]["checkout_url"].startswith("https://checkout.stripe.com/")
    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/finance/recharge/stripe/checkout"
    # A caller that supplies no redirect targets sends the pre-2026-07-30 body
    # byte-for-byte — NOT explicit nulls. Upstream accepts null, but "omitted"
    # is the shape that has always worked, so an unconfigured deployment keeps
    # exactly today's behavior.
    assert seen["body"] == {"amount": 10, "currency": "USD", "payment_method": "default"}


@pytest.mark.asyncio
async def test_recharge_forwards_redirect_urls_when_given():
    """The route resolved a return target -> it reaches the upstream body.

    Verified live against dev 2026-07-30: this field IS consumed and handed to
    Stripe (an illegal value makes the upstream answer 500 "Failed to create
    Stripe checkout session"), so what we send here decides where the payer
    lands after paying.
    """
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"success": True, "data": {}})

    await _client_with(handler).recharge(
        "jwt",
        10,
        success_url="https://agent.narra.nexus/app/settings?status=success",
        cancel_url="https://agent.narra.nexus/app/settings?status=cancelled",
    )
    assert seen["body"] == {
        "amount": 10,
        "currency": "USD",
        "payment_method": "default",
        "success_url": "https://agent.narra.nexus/app/settings?status=success",
        "cancel_url": "https://agent.narra.nexus/app/settings?status=cancelled",
    }


@pytest.mark.asyncio
async def test_subscribe_omits_redirect_urls_when_unresolved():
    """An unconfigured deployment must send NO redirect key — not a null one.

    The upstream treats both as Optional[str] and accepts explicit nulls
    (probed dev+prod 2026-07-30), but "omitted" is the shape that has shipped
    for months, so an install without PUBLIC_BASE_URL keeps its exact behaviour.

    (Before the nexus account landed this asserted an EMPTY body. The body is
    no longer empty — `payment_method` now selects card vs one-time purchase on
    the same endpoint — so the assertion moved to the invariant that actually
    mattered.)
    """
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"session_id": "cs_1", "checkout_url": "https://x/y"})

    await _client_with(handler).subscribe("jwt")
    assert seen["body"] == {"payment_method": "stripe"}
    assert "success_url" not in seen["body"] and "cancel_url" not in seen["body"]


@pytest.mark.asyncio
async def test_subscribe_forwards_redirect_urls_when_given():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"session_id": "cs_1", "checkout_url": "https://x/y"})

    await _client_with(handler).subscribe(
        "jwt",
        success_url="https://agent.narra.nexus/app/settings?status=success",
        cancel_url="https://agent.narra.nexus/app/settings?status=cancelled",
    )
    assert seen["body"] == {
        "payment_method": "stripe",
        "success_url": "https://agent.narra.nexus/app/settings?status=success",
        "cancel_url": "https://agent.narra.nexus/app/settings?status=cancelled",
    }


@pytest.mark.asyncio
async def test_subscribe_forwards_only_the_url_that_was_given():
    """Half-configured is still a legal body — no None smuggled through."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"session_id": "cs_1", "checkout_url": "https://x/y"})

    await _client_with(handler).subscribe("jwt", success_url="https://agent.narra.nexus/ok")
    assert seen["body"] == {"payment_method": "stripe", "success_url": "https://agent.narra.nexus/ok"}


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


# =============================================================================
# nexus account — payment methods, currency derivation, fx-rate (2026-08-18)
# =============================================================================


def _body_capture(seen: dict, payload=None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = dict(request.url.params)
        raw = request.content.decode() or "{}"
        seen["body"] = json.loads(raw)
        return httpx.Response(200, json=payload if payload is not None else {"success": True, "data": {}})

    return handler


# --- currency is a FUNCTION of payment_method ------------------------------
# Upstream 400s when the two disagree, so the mapping is modelled here rather
# than at each call site: a caller that could pass both could break its own
# payment. WeChat is CNY-only on this account; card and Alipay are USD.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,currency",
    [
        ("default", "USD"),
        ("alipay", "USD"),
        ("wechat", "CNY"),
        # The route's Literal is the real gate, so this is unreachable from HTTP.
        # Pinned anyway because the fallback is a JUDGEMENT — USD is the
        # conservative answer, not an accident — and a future `.get(m)` returning
        # None would silently send `"currency": null`.
        ("something-upstream-added-later", "USD"),
    ],
)
async def test_recharge_derives_currency_from_payment_method(method, currency):
    seen = {}
    await _client_with(_body_capture(seen)).recharge(
        "jwt", 10.0, payment_method=method, channel="nexus"
    )
    assert seen["body"]["payment_method"] == method
    assert seen["body"]["currency"] == currency
    assert seen["body"]["amount"] == 10.0  # always the USD figure, even for CNY


@pytest.mark.asyncio
async def test_recharge_sends_channel():
    seen = {}
    await _client_with(_body_capture(seen)).recharge("jwt", 10.0, channel="nexus")
    assert seen["body"]["channel"] == "nexus"


@pytest.mark.asyncio
async def test_subscribe_card_sends_stripe_and_omits_months():
    """Months is meaningless for an auto-renewing card subscription; sending it
    would describe a state upstream does not have."""
    seen = {}
    await _client_with(_body_capture(seen)).subscribe(
        "jwt", payment_method="stripe", channel="nexus"
    )
    assert seen["body"]["payment_method"] == "stripe"
    assert "months" not in seen["body"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["alipay", "wechat"])
async def test_subscribe_one_time_sends_months(method):
    seen = {}
    await _client_with(_body_capture(seen)).subscribe(
        "jwt", payment_method=method, months=6, channel="nexus"
    )
    assert seen["body"]["payment_method"] == method
    assert seen["body"]["months"] == 6


@pytest.mark.asyncio
async def test_subscribe_still_sends_redirect_urls_alongside_months():
    seen = {}
    await _client_with(_body_capture(seen)).subscribe(
        "jwt",
        payment_method="alipay",
        months=2,
        channel="nexus",
        success_url="https://a.test/ok",
        cancel_url="https://a.test/no",
    )
    assert seen["body"]["success_url"] == "https://a.test/ok"
    assert seen["body"]["months"] == 2


# --- fx-rate ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_fx_rate_hits_path_with_currency_and_amount():
    seen = {}
    payload = {
        "from": "USD", "to": "CNY", "rate": "7.30",
        "amount_usd": "10", "charge_amount": "73.00",
        "min_amount_usd": "0.69", "min_charge": "5.00",
    }
    data = await _client_with(_body_capture(seen, payload)).fx_rate("jwt", "CNY", amount=10)
    assert seen["path"] == "/v1/finance/recharge/fx-rate"
    assert seen["query"] == {"currency": "CNY", "amount": "10"}
    assert data["charge_amount"] == "73.00"


@pytest.mark.asyncio
async def test_fx_rate_omits_amount_when_not_given():
    """`amount` is optional upstream — omitted means "just the rate". Sending an
    empty value instead would be a different, and wrong, request."""
    seen = {}
    await _client_with(_body_capture(seen)).fx_rate("jwt", "CNY")
    assert seen["query"] == {"currency": "CNY"}


@pytest.mark.asyncio
async def test_fx_rate_sends_logintoken():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("loginToken")
        return httpx.Response(200, json={"rate": "7.30"})

    await _client_with(handler).fx_rate("jwt-xyz", "CNY")
    assert seen["auth"] == "Bearer jwt-xyz"  # same convention as every other call


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "reactivate"])
async def test_cancel_and_reactivate_put_the_channel_in_the_body(action):
    """Assert on the OUTBOUND body, not on a stub's kwargs.

    The route tests prove the route hands `channel` to the client; only this
    proves the client puts it in the request. Without it, deleting
    `json_body=_channel_field(channel)` from either method leaves the whole
    backend suite green (verified: 1252 passed) while a card subscription
    created on the nexus account may become impossible to cancel — the one
    upstream behaviour in this feature with no measurement behind it, and this
    line is the entire hedge against it.

    Field-scoped, not a whole-body equality: upstream requiring another field
    here later should not turn a normal extension into a red build.
    """
    seen: dict = {}
    await getattr(_client_with(_body_capture(seen)), action)("jwt", channel="nexus")
    assert seen["body"]["channel"] == "nexus"
