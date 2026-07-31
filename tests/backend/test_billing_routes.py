"""
@file_name: test_billing_routes.py
@author: NarraNexus
@date: 2026-07-02
@description: Route tests for backend/routes/billing.py (NetMind billing proxy).

Mirrors tests/backend/test_provider_oauth_gating.py: a FastAPI TestClient with a
fake auth middleware, the deployment mode forced via env, and the billing client
stubbed so no real network happens.

Gating is on the "power" axis (post-dual-mode-login refactor), NOT deployment
mode: /plans gates on is_power_login_enabled(); user-scoped endpoints gate on
is_power_account(user_id) — stubbed here (default: caller IS a Power account).
Verifies power gating, token requirement, and error mapping (auth -> 401,
upstream -> 502).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.billing as billing_mod
from backend.integrations.netmind.netmind_billing_client import (
    BillingAuthError,
    BillingBusinessError,
    BillingForbiddenError,
    BillingNotFoundError,
    BillingUpstreamError,
)

USER = {"X-User-Id": "user_test"}
_ME_FREE = {"plan_id": "free", "subscription": None}


@pytest.fixture
def make_client(monkeypatch):
    """Build a TestClient with fake auth middleware + forced deployment mode."""

    def _make(*, cloud: bool):
        monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud" if cloud else "local")
        app = FastAPI()

        @app.middleware("http")
        async def fake_auth(request: Request, call_next):
            request.state.user_id = request.headers.get("X-User-Id") or None
            return await call_next(request)

        app.include_router(billing_mod.router, prefix="/api/billing")
        return TestClient(app, raise_server_exceptions=False)

    return _make


def _stub_client(monkeypatch, *, plans=None, me=None, fee=None, records=None, action=None,
                 recharge=None, recharge_status=None, raise_exc=None):
    """Install a fake billing client. Returns a dict recording the kwargs the
    route passed to the write methods, so tests can assert on the redirect
    targets the route resolved (not just on the response)."""
    seen: dict = {}

    class _Stub:
        async def get_plans(self):
            if raise_exc:
                raise raise_exc
            return plans if plans is not None else {"plans": []}

        async def get_subscription(self, token):
            if raise_exc:
                raise raise_exc
            return me if me is not None else _ME_FREE

        async def get_fee_info(self, token):
            if raise_exc:
                raise raise_exc
            return fee if fee is not None else {"eligible": True, "metrics": {}}

        async def get_records(self, token, direction=None):
            if raise_exc:
                raise raise_exc
            return records if records is not None else {"data": [], "has_next": False}

        async def subscribe(self, token, **kwargs):
            seen["subscribe"] = kwargs
            if raise_exc:
                raise raise_exc
            return action if action is not None else {"session_id": "cs", "checkout_url": "https://x"}

        async def cancel(self, token):
            if raise_exc:
                raise raise_exc
            return action if action is not None else {"status": "auto_renew_off"}

        async def reactivate(self, token):
            if raise_exc:
                raise raise_exc
            return action if action is not None else {"status": "auto_renew_on"}

        async def recharge(self, token, amount, currency="USD", **kwargs):
            seen["recharge"] = kwargs
            if raise_exc:
                raise raise_exc
            return recharge if recharge is not None else {
                "success": True,
                "data": {
                    "session_id": "cs_r",
                    "checkout_url": "https://checkout.stripe.com/c/pay/cs_r",
                    "status": "pending",
                },
            }

        async def recharge_status(self, token, session_id):
            if raise_exc:
                raise raise_exc
            return recharge_status if recharge_status is not None else {
                "success": True, "data": {"status": "succeeded"},
            }

    monkeypatch.setattr(billing_mod, "_client", lambda: _Stub())
    # Default: the caller IS a Power account, so user-scoped endpoints are
    # reachable. Individual tests override via _stub_power(..., is_power=False).
    _stub_power(monkeypatch, is_power=True)
    return seen


def _stub_power(monkeypatch, *, is_power=True):
    async def _is_power(user_id):
        return is_power

    monkeypatch.setattr(billing_mod, "is_power_account", _is_power)


# --- power-login gating (deployment axis: /plans) ---------------------------

def test_plans_404_when_power_login_disabled(make_client, monkeypatch):
    # local install with no NARRANEXUS_ENABLE_POWER_LOGIN opt-in
    _stub_client(monkeypatch)
    client = make_client(cloud=False)
    assert client.get("/api/billing/plans").status_code == 404


def test_plans_ok_in_local_when_power_login_enabled(make_client, monkeypatch):
    _stub_client(monkeypatch, plans={"plans": [{"plan_id": "pro"}]})
    monkeypatch.setenv("NARRANEXUS_ENABLE_POWER_LOGIN", "true")
    client = make_client(cloud=False)  # local deployment, Power login opted in
    assert client.get("/api/billing/plans").status_code == 200


# --- power-account gating (per-user axis: user-scoped endpoints) ------------

def test_subscription_404_for_local_username_user(make_client, monkeypatch):
    # Local deployment + a pure-local username user (not a Power account) → 404.
    # (In cloud mode billing stays reachable for every authed user — see the
    # is_cloud_mode() short-circuit in _require_power_account.)
    _stub_client(monkeypatch)
    _stub_power(monkeypatch, is_power=False)
    client = make_client(cloud=False)
    r = client.get("/api/billing/subscription", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 404


def test_subscription_ok_for_power_user_in_local_mode(make_client, monkeypatch):
    # The core dual-mode win: a Power account works on a LOCAL deployment.
    _stub_client(monkeypatch, me={"plan_id": "pro", "subscription": {"status": "ACTIVE"}})
    monkeypatch.setenv("NARRANEXUS_ENABLE_POWER_LOGIN", "true")
    client = make_client(cloud=False)
    r = client.get("/api/billing/subscription", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 200
    assert r.json()["data"]["subscription"]["status"] == "ACTIVE"


def test_subscription_reachable_in_cloud_even_if_not_individual(make_client, monkeypatch):
    # No cloud regression: in cloud mode billing stays reachable for any authed
    # user (is_cloud_mode() short-circuit), even a row that isn't "individual".
    _stub_client(monkeypatch)
    _stub_power(monkeypatch, is_power=False)
    client = make_client(cloud=True)
    r = client.get("/api/billing/subscription", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 200


# --- plans (public) ---------------------------------------------------------

def test_plans_ok(make_client, monkeypatch):
    _stub_client(monkeypatch, plans={"plans": [{"plan_id": "pro"}]})
    client = make_client(cloud=True)
    r = client.get("/api/billing/plans")
    assert r.status_code == 200
    assert r.json()["data"]["plans"][0]["plan_id"] == "pro"


def test_plans_upstream_502(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingUpstreamError("down"))
    client = make_client(cloud=True)
    assert client.get("/api/billing/plans").status_code == 502


# --- subscription (loginToken) ---------------------------------------------

def test_subscription_ok(make_client, monkeypatch):
    _stub_client(monkeypatch, me={"plan_id": "pro", "subscription": {"status": "ACTIVE"}})
    client = make_client(cloud=True)
    r = client.get("/api/billing/subscription", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 200
    assert r.json()["data"]["subscription"]["status"] == "ACTIVE"


def test_subscription_missing_netmind_token_401(make_client, monkeypatch):
    _stub_client(monkeypatch)
    client = make_client(cloud=True)
    # local identity present, but no X-Netmind-Token
    r = client.get("/api/billing/subscription", headers=USER)
    assert r.status_code == 401


def test_subscription_bad_token_maps_to_401(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingAuthError("bad"))
    client = make_client(cloud=True)
    r = client.get("/api/billing/subscription", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 401


def test_subscription_upstream_maps_to_502(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingUpstreamError("down"))
    client = make_client(cloud=True)
    r = client.get("/api/billing/subscription", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 502


def test_subscription_unauthenticated_local_identity_401(make_client, monkeypatch):
    _stub_client(monkeypatch)
    client = make_client(cloud=True)
    # No X-User-Id -> resolve_current_user_id raises 401 before token check
    r = client.get("/api/billing/subscription", headers={"X-Netmind-Token": "jwt"})
    assert r.status_code == 401


# --- Phase 2: fee-info (balance) -------------------------------------------

def test_fee_info_ok(make_client, monkeypatch):
    _stub_client(monkeypatch, fee={"eligible": True, "metrics": {"free_credit": "5.00"}})
    client = make_client(cloud=True)
    r = client.get("/api/billing/fee-info", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 200
    assert r.json()["data"]["metrics"]["free_credit"] == "5.00"


def test_fee_info_auth_error_401(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingAuthError("bad"))
    client = make_client(cloud=True)
    r = client.get("/api/billing/fee-info", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 401


def test_fee_info_missing_token_401(make_client, monkeypatch):
    _stub_client(monkeypatch)
    client = make_client(cloud=True)
    assert client.get("/api/billing/fee-info", headers=USER).status_code == 401


def test_fee_info_404_for_local_username_user(make_client, monkeypatch):
    _stub_client(monkeypatch)
    _stub_power(monkeypatch, is_power=False)
    client = make_client(cloud=False)
    r = client.get("/api/billing/fee-info", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 404


# --- Phase 2 enhancement: records (activity) -------------------------------

def test_records_ok(make_client, monkeypatch):
    _stub_client(monkeypatch, records={
        "data": [{"record_id": "r1", "direction": "expense", "amount": "0.10"}],
        "has_next": True,
    })
    client = make_client(cloud=True)
    r = client.get("/api/billing/records", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 200
    body = r.json()
    assert body["data"][0]["record_id"] == "r1"
    assert body["has_next"] is True


def test_records_auth_error_401(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingAuthError("bad"))
    client = make_client(cloud=True)
    r = client.get("/api/billing/records", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 401


def test_records_missing_token_401(make_client, monkeypatch):
    _stub_client(monkeypatch)
    client = make_client(cloud=True)
    assert client.get("/api/billing/records", headers=USER).status_code == 401


# --- Phase 3: subscribe / cancel / reactivate ------------------------------

H = {**USER, "X-Netmind-Token": "jwt"}


def test_subscribe_ok_returns_checkout(make_client, monkeypatch):
    url = "https://checkout.stripe.com/c/pay/cs_1"
    _stub_client(monkeypatch, action={"session_id": "cs_1", "checkout_url": url})
    client = make_client(cloud=True)
    r = client.post("/api/billing/subscribe", headers=H)
    assert r.status_code == 200
    assert r.json()["data"]["checkout_url"] == url


def test_subscribe_rejects_non_stripe_checkout_url(make_client, monkeypatch):
    # A compromised/MITM'd upstream returning an attacker URL must be rejected
    # (openExternal would otherwise open it on the user's machine).
    _stub_client(monkeypatch, action={"session_id": "cs", "checkout_url": "https://evil.example/x"})
    client = make_client(cloud=True)
    assert client.post("/api/billing/subscribe", headers=H).status_code == 502


def test_subscribe_rejects_non_https_checkout_url(make_client, monkeypatch):
    _stub_client(monkeypatch, action={"checkout_url": "http://checkout.stripe.com/x"})
    client = make_client(cloud=True)
    assert client.post("/api/billing/subscribe", headers=H).status_code == 502


def test_plans_business_4xx_maps_to_502(make_client, monkeypatch):
    # Regression: read routes must catch BillingBusinessError (shared _request
    # raises it for any non-auth 4xx) and 502, not let it 500.
    _stub_client(monkeypatch, raise_exc=BillingBusinessError("weird", 422))
    client = make_client(cloud=True)
    assert client.get("/api/billing/plans").status_code == 502


def test_subscription_business_4xx_maps_to_502(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingBusinessError("weird", 422))
    client = make_client(cloud=True)
    r = client.get("/api/billing/subscription", headers={**USER, "X-Netmind-Token": "jwt"})
    assert r.status_code == 502


def test_cancel_ok(make_client, monkeypatch):
    _stub_client(monkeypatch, action={"status": "auto_renew_off"})
    client = make_client(cloud=True)
    r = client.post("/api/billing/cancel", headers=H)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "auto_renew_off"


def test_reactivate_ok(make_client, monkeypatch):
    _stub_client(monkeypatch, action={"status": "auto_renew_on"})
    client = make_client(cloud=True)
    r = client.post("/api/billing/reactivate", headers=H)
    assert r.status_code == 200


def test_subscribe_business_error_maps_to_400(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingBusinessError("Already subscribed to Pro.", 400))
    client = make_client(cloud=True)
    r = client.post("/api/billing/subscribe", headers=H)
    assert r.status_code == 400
    assert r.json()["detail"] == "Already subscribed to Pro."


def test_cancel_business_error_maps_to_400(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingBusinessError("No active Pro subscription.", 400))
    client = make_client(cloud=True)
    r = client.post("/api/billing/cancel", headers=H)
    assert r.status_code == 400
    assert "No active Pro subscription." in r.json()["detail"]


def test_subscribe_auth_error_maps_to_401(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingAuthError("bad"))
    client = make_client(cloud=True)
    assert client.post("/api/billing/subscribe", headers=H).status_code == 401


def test_subscribe_upstream_maps_to_502(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingUpstreamError("down"))
    client = make_client(cloud=True)
    assert client.post("/api/billing/subscribe", headers=H).status_code == 502


def test_subscribe_404_for_local_username_user(make_client, monkeypatch):
    _stub_client(monkeypatch)
    _stub_power(monkeypatch, is_power=False)
    client = make_client(cloud=False)
    assert client.post("/api/billing/subscribe", headers=H).status_code == 404


def test_subscribe_missing_netmind_token_401(make_client, monkeypatch):
    _stub_client(monkeypatch)
    client = make_client(cloud=True)
    assert client.post("/api/billing/subscribe", headers=USER).status_code == 401


# --- Phase 4: recharge / top-up --------------------------------------------

def test_recharge_ok_returns_checkout(make_client, monkeypatch):
    _stub_client(monkeypatch)
    client = make_client(cloud=True)
    r = client.post("/api/billing/recharge", headers=H, json={"amount": 10})
    assert r.status_code == 200
    assert r.json()["data"]["checkout_url"].startswith("https://checkout.stripe.com/")
    assert r.json()["data"]["session_id"] == "cs_r"


def test_recharge_rejects_non_stripe_checkout_url(make_client, monkeypatch):
    _stub_client(monkeypatch, recharge={"data": {"checkout_url": "https://evil.example/x", "session_id": "cs"}})
    client = make_client(cloud=True)
    assert client.post("/api/billing/recharge", headers=H, json={"amount": 10}).status_code == 502


def test_recharge_rejects_zero_amount(make_client, monkeypatch):
    # amount <= 0 fails Pydantic validation before any upstream call -> 422.
    _stub_client(monkeypatch)
    client = make_client(cloud=True)
    assert client.post("/api/billing/recharge", headers=H, json={"amount": 0}).status_code == 422


def test_recharge_business_error_maps_to_400(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingBusinessError("Amount too small.", 400))
    client = make_client(cloud=True)
    r = client.post("/api/billing/recharge", headers=H, json={"amount": 1})
    assert r.status_code == 400
    assert r.json()["detail"] == "Amount too small."


def test_recharge_auth_error_maps_to_401(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingAuthError("bad"))
    client = make_client(cloud=True)
    assert client.post("/api/billing/recharge", headers=H, json={"amount": 10}).status_code == 401


def test_recharge_missing_token_401(make_client, monkeypatch):
    _stub_client(monkeypatch)
    client = make_client(cloud=True)
    assert client.post("/api/billing/recharge", headers=USER, json={"amount": 10}).status_code == 401


def test_recharge_404_for_local_username_user(make_client, monkeypatch):
    _stub_client(monkeypatch)
    _stub_power(monkeypatch, is_power=False)
    client = make_client(cloud=False)
    assert client.post("/api/billing/recharge", headers=H, json={"amount": 10}).status_code == 404


def test_recharge_status_ok(make_client, monkeypatch):
    _stub_client(monkeypatch, recharge_status={"data": {"status": "succeeded"}})
    client = make_client(cloud=True)
    r = client.get("/api/billing/recharge/cs_test_abc123", headers=H)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "succeeded"


def test_recharge_status_rejects_malformed_session_id(make_client, monkeypatch):
    # A session_id that isn't a `cs_...` token must 404 BEFORE any upstream call
    # (blocks `..`/`?`/`#` path smuggling into the outbound NetMind URL).
    called = {"hit": False}

    def _boom(*a, **k):
        called["hit"] = True
        raise AssertionError("upstream must not be called for a malformed id")

    _stub_client(monkeypatch)
    monkeypatch.setattr(billing_mod, "_client", lambda: type("C", (), {"recharge_status": _boom})())
    client = make_client(cloud=True)
    r = client.get("/api/billing/recharge/notatoken", headers=H)
    assert r.status_code == 404
    assert called["hit"] is False


def test_recharge_status_forbidden_maps_to_403(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingForbiddenError("not yours"))
    client = make_client(cloud=True)
    assert client.get("/api/billing/recharge/cs_x", headers=H).status_code == 403


def test_recharge_status_not_found_maps_to_404(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingNotFoundError("unknown"))
    client = make_client(cloud=True)
    assert client.get("/api/billing/recharge/cs_missing", headers=H).status_code == 404


def test_recharge_status_auth_error_maps_to_401(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingAuthError("bad"))
    client = make_client(cloud=True)
    assert client.get("/api/billing/recharge/cs_x", headers=H).status_code == 401


def test_recharge_status_upstream_maps_to_502(make_client, monkeypatch):
    _stub_client(monkeypatch, raise_exc=BillingUpstreamError("down"))
    client = make_client(cloud=True)
    assert client.get("/api/billing/recharge/cs_x", headers=H).status_code == 502


# --- post-payment return targets (2026-07-30) -------------------------------
#
# Stripe redirects the payer to the success_url/cancel_url stored on the
# Checkout Session, and NetMind — not us — creates that session. So the only
# lever we have is the pair of fields we hand upstream. These tests pin BOTH
# directions: the URLs we build when a public origin is configured, and the
# silent degrade to today's behavior when one isn't (never break a payment
# over a cosmetic redirect).


# The shared stub's default checkout_url ("https://x") trips the Stripe-host
# guard; these tests care about the OUTBOUND body, so they need a checkout_url
# that survives the inbound guard.
_STRIPE_ACTION = {"session_id": "cs_1", "checkout_url": "https://checkout.stripe.com/c/pay/cs_1"}


def _set_origin(monkeypatch, value: str) -> None:
    monkeypatch.setattr(billing_mod.settings, "public_base_url", value, raising=False)


def test_subscribe_sends_return_urls_when_origin_configured(make_client, monkeypatch):
    seen = _stub_client(monkeypatch, action=_STRIPE_ACTION)
    _set_origin(monkeypatch, "https://agent.narra.nexus")
    client = make_client(cloud=True)
    assert client.post("/api/billing/subscribe", headers=H).status_code == 200
    assert seen["subscribe"] == {
        "success_url":
            "https://agent.narra.nexus/app/settings?tab=account&status=success&flow=subscription",
        "cancel_url":
            "https://agent.narra.nexus/app/settings?tab=account&status=cancelled&flow=subscription",
    }


def test_recharge_sends_return_urls_when_origin_configured(make_client, monkeypatch):
    seen = _stub_client(monkeypatch, action=_STRIPE_ACTION)
    _set_origin(monkeypatch, "https://agent.narra.nexus")
    client = make_client(cloud=True)
    r = client.post("/api/billing/recharge", headers=H, json={"amount": 10})
    assert r.status_code == 200
    # flow=topup, so the returning tab can say "credits added" rather than
    # guessing which payment just completed.
    assert seen["recharge"] == {
        "success_url":
            "https://agent.narra.nexus/app/settings?tab=account&status=success&flow=topup",
        "cancel_url":
            "https://agent.narra.nexus/app/settings?tab=account&status=cancelled&flow=topup",
    }


def test_origin_trailing_slash_and_path_are_normalised(make_client, monkeypatch):
    # PUBLIC_BASE_URL is documented as scheme://host but operators paste paths
    # and trailing slashes; only the origin may survive, or the redirect lands
    # on a 404 the user reads as "payment broke".
    seen = _stub_client(monkeypatch, action=_STRIPE_ACTION)
    _set_origin(monkeypatch, "https://agent.narra.nexus/some/base/")
    client = make_client(cloud=True)
    client.post("/api/billing/subscribe", headers=H)
    assert seen["subscribe"]["success_url"].startswith(
        "https://agent.narra.nexus/app/settings?"
    )


def test_no_return_urls_when_origin_unset(make_client, monkeypatch):
    # Self-hosted / desktop default: no configured public origin, so we send
    # nothing and keep NetMind's own result page — identical to pre-fix behavior.
    seen = _stub_client(monkeypatch, action=_STRIPE_ACTION)
    _set_origin(monkeypatch, "")
    client = make_client(cloud=True)
    assert client.post("/api/billing/subscribe", headers=H).status_code == 200
    assert seen["subscribe"] == {}


@pytest.mark.parametrize("origin", [
    "http://agent.narra.nexus",       # plain http (see _return_urls on why)
    "http://localhost:8000",          # the common self-hosted value
    "not-a-url",                      # operator typo
    "ftp://agent.narra.nexus",        # non-web scheme
    "https://",                       # scheme but no host
    "https://agent.narra.nexus:99999",  # port out of range -> urlparse().port raises
    "https://agent.narra.nexus:abc",    # non-numeric port -> same
    # urlparse() ITSELF raises on these two — the parse step, not the port:
    "https://agent.narra.nexus：8443",  # full-width colon (IME slip) -> NFKC reject
    "https://[2001:db8::1:8443",           # unbalanced IPv6 bracket
    # These parse fine and pass the host screen, but would reach Stripe malformed:
    "https://exa mple.com",             # pasted-in space
    "https://agent.narra.nexus​",  # trailing zero-width space
])
def test_unusable_origin_degrades_instead_of_breaking_payment(
    make_client, monkeypatch, origin
):
    """An origin Stripe would reject must never reach the upstream body.

    Proven live on dev 2026-07-30: an illegal success_url makes NetMind answer
    500 "Failed to create Stripe checkout session" — i.e. passing junk here
    doesn't degrade the redirect, it destroys the user's ability to pay at all.
    """
    seen = _stub_client(monkeypatch, action=_STRIPE_ACTION)
    _set_origin(monkeypatch, origin)
    client = make_client(cloud=True)
    assert client.post("/api/billing/subscribe", headers=H).status_code == 200
    assert seen["subscribe"] == {}
    r = client.post("/api/billing/recharge", headers=H, json={"amount": 10})
    assert r.status_code == 200
    assert seen["recharge"] == {}


def test_cancel_and_reactivate_take_no_return_urls(make_client, monkeypatch):
    # Neither opens a Stripe checkout, so a redirect target is meaningless
    # there; the shared write harness must not leak the kwargs into them.
    seen = _stub_client(monkeypatch, action=_STRIPE_ACTION)
    _set_origin(monkeypatch, "https://agent.narra.nexus")
    client = make_client(cloud=True)
    assert client.post("/api/billing/cancel", headers=H).status_code == 200
    assert client.post("/api/billing/reactivate", headers=H).status_code == 200
    assert "subscribe" not in seen


def test_origin_keeps_the_port_but_drops_any_userinfo(make_client, monkeypatch):
    """The return URL is stored on a Stripe session by NetMind — two third
    parties. A base URL carrying basic-auth credentials must not put them there.
    A non-default port, by contrast, is part of the real origin and must survive.
    """
    seen = _stub_client(monkeypatch, action=_STRIPE_ACTION)
    _set_origin(monkeypatch, "https://deploy:s3cret@agent.narra.nexus:8443")
    client = make_client(cloud=True)
    client.post("/api/billing/subscribe", headers=H)
    success = seen["subscribe"]["success_url"]
    assert success.startswith("https://agent.narra.nexus:8443/app/settings?")
    assert "s3cret" not in success
    assert "deploy" not in success


@pytest.mark.parametrize("origin", [
    "https://localhost:5173",        # `bash run.sh` in a browser
    "https://127.0.0.1:8000",
    "https://192.168.1.50",          # LAN host with a private cert
    "https://10.0.0.5:8443",
    "https://[::1]:8000",
    "https://my-nas",                # single-label name
    "https://printer.local",         # mDNS
])
def test_private_or_loopback_origin_degrades(make_client, monkeypatch, origin):
    """A non-public origin must never reach the upstream body.

    Measured on dev 2026-07-30: the upstream EDGE answers an HTML 403 for a
    loopback/private host on ANY scheme. Our client maps 403 to
    BillingAuthError, which this route reports as 401 "NetMind token invalid or
    expired" — so sending one would break the user's checkout AND blame their
    login for it. Degrading keeps checkout working (just without the redirect).
    """
    seen = _stub_client(monkeypatch, action=_STRIPE_ACTION)
    _set_origin(monkeypatch, origin)
    client = make_client(cloud=True)
    assert client.post("/api/billing/subscribe", headers=H).status_code == 200
    assert seen["subscribe"] == {}
    assert client.post(
        "/api/billing/recharge", headers=H, json={"amount": 10}
    ).status_code == 200
    assert seen["recharge"] == {}


def test_ipv6_literal_origin_keeps_its_brackets(make_client, monkeypatch):
    """Rebuilding from `.hostname` would drop the brackets and yield
    `https://2001:4860:4860::8888:8443` — a malformed URL the upstream would hand
    to Stripe, breaking checkout. netloc is the only form that survives this.
    """
    seen = _stub_client(monkeypatch, action=_STRIPE_ACTION)
    _set_origin(monkeypatch, "https://[2001:4860:4860::8888]:8443")
    client = make_client(cloud=True)
    client.post("/api/billing/subscribe", headers=H)
    assert seen["subscribe"]["success_url"].startswith(
        "https://[2001:4860:4860::8888]:8443/app/settings?"
    )
