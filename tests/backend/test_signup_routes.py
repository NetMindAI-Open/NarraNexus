"""Self-serve signup: the two routes that replaced the netmind.ai redirect.

What these guard, in order of how badly it would hurt to get wrong:
  1. the password and the code never leave via a log or an error body,
  2. the send-code endpoint is rate-limited (it sends mail on our behalf),
  3. an upstream refusal the USER can fix reads differently from an outage.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.auth as auth_mod
from backend.integrations.netmind.netmind_register_client import (
    RegistrationError,
    RegistrationUpstreamError,
    password_policy_error,
)

GOOD_PW = "Aa1!aaaa"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth_mod, "is_power_login_enabled", lambda: True)
    # Fresh limiters per test — module-level state would leak across cases.
    from backend.routes._rate_limiter import SlidingWindowRateLimiter
    monkeypatch.setattr(auth_mod, "_signup_code_limiter",
                        SlidingWindowRateLimiter(limit=1, window_sec=60.0))
    monkeypatch.setattr(auth_mod, "_signup_attempt_limiter",
                        SlidingWindowRateLimiter(limit=10, window_sec=600.0))
    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


def _stub(monkeypatch, **kwargs):
    stub = AsyncMock(**kwargs)
    import backend.integrations.netmind.netmind_register_client as mod
    monkeypatch.setattr(mod.NetmindRegisterClient, "send_code", stub, raising=False)
    monkeypatch.setattr(mod.NetmindRegisterClient, "register", stub, raising=False)
    return stub


# ── password policy ────────────────────────────────────────────────────────

@pytest.mark.parametrize("pw,ok", [
    ("Aa1!aaaa", True),
    ("Aa1!aaaaaaaaaaaa", True),      # 16, the upper bound
    ("Aa1!aaa", False),              # 7, just under
    ("Aa1!aaaaaaaaaaaaa", False),    # 17, just over
    ("aa1!aaaa", False),             # no uppercase
    ("AA1!AAAA", False),             # no lowercase
    ("Aaa!aaaa", False),             # no digit
    ("Aa1aaaaa", False),             # no special
])
def test_password_policy(pw, ok):
    assert (password_policy_error(pw) is None) is ok


def test_weak_password_is_rejected_before_any_upstream_call(client, monkeypatch):
    stub = _stub(monkeypatch)
    r = client.post("/api/auth/signup",
                    json={"email": "a@b.com", "password": "weak", "verify_code": "123456"})
    assert r.status_code == 400
    stub.assert_not_awaited()


# ── secret discipline ──────────────────────────────────────────────────────

def test_no_error_response_ever_echoes_the_password_or_code(client, monkeypatch):
    _stub(monkeypatch, side_effect=RegistrationError("verification code error"))
    r = client.post("/api/auth/signup", json={
        "email": "a@b.com", "password": GOOD_PW, "verify_code": "424242",
    })
    body = r.text
    assert r.status_code == 400
    assert GOOD_PW not in body
    assert "424242" not in body


def test_upstream_outage_response_does_not_leak_secrets_either(client, monkeypatch):
    _stub(monkeypatch, side_effect=RegistrationUpstreamError("connect timeout"))
    r = client.post("/api/auth/signup", json={
        "email": "a@b.com", "password": GOOD_PW, "verify_code": "424242",
    })
    assert r.status_code == 502
    assert GOOD_PW not in r.text and "424242" not in r.text


# ── rate limiting ──────────────────────────────────────────────────────────

def test_send_code_is_rate_limited_per_email(client, monkeypatch):
    """It sends mail on our behalf — an unlimited endpoint is an email bomb
    aimed at someone else's mailbox and our sender reputation."""
    stub = _stub(monkeypatch)
    assert client.post("/api/auth/signup/send-code", json={"email": "a@b.com"}).status_code == 200
    assert client.post("/api/auth/signup/send-code", json={"email": "a@b.com"}).status_code == 429
    # A different address is unaffected — the limit is per mailbox, not global.
    assert client.post("/api/auth/signup/send-code", json={"email": "c@d.com"}).status_code == 200
    assert stub.await_count == 2


def test_email_is_normalised_so_case_cannot_dodge_the_limit(client, monkeypatch):
    _stub(monkeypatch)
    assert client.post("/api/auth/signup/send-code", json={"email": "a@b.com"}).status_code == 200
    assert client.post("/api/auth/signup/send-code", json={"email": "A@B.com"}).status_code == 429


# ── error mapping ──────────────────────────────────────────────────────────

def test_user_fixable_refusal_is_400_with_the_upstream_message(client, monkeypatch):
    _stub(monkeypatch, side_effect=RegistrationError("email already registered"))
    r = client.post("/api/auth/signup", json={
        "email": "a@b.com", "password": GOOD_PW, "verify_code": "123456",
    })
    assert r.status_code == 400
    assert "already registered" in r.json()["detail"]


def test_happy_path_returns_success_and_no_session(client, monkeypatch):
    """Success means the account exists — nothing more. A route that also
    accepts an unverified email must not mint a session."""
    _stub(monkeypatch)
    r = client.post("/api/auth/signup", json={
        "email": "a@b.com", "password": GOOD_PW, "verify_code": "123456",
    })
    assert r.status_code == 200
    assert r.json() == {"success": True}
    assert "token" not in r.text


def test_local_mode_has_no_signup(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "is_power_login_enabled", lambda: False)
    assert client.post("/api/auth/signup/send-code",
                       json={"email": "a@b.com"}).status_code == 404


# ── reachability ───────────────────────────────────────────────────────────

def test_signup_routes_are_exempt_from_auth():
    """A signup route that needs a token is a signup route nobody can use.

    Caught on the first dev deploy, where both endpoints 401'd — the middleware
    allowlist is a separate file from the routes, so adding one does not add the
    other.
    """
    from backend.auth import AUTH_EXEMPT_PATHS

    assert "/api/auth/signup" in AUTH_EXEMPT_PATHS
    assert "/api/auth/signup/send-code" in AUTH_EXEMPT_PATHS
