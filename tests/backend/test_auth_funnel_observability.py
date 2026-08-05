"""Auth-funnel observability: the 2026-08-01 post-mortem could not answer
"where did signups die" (signup 400x17 with no upstream detail recorded) or
"why couldn't fresh accounts log in" (browser->NetMind failures invisible to
the server). These tests pin the three closures:

  1. every upstream refusal is logged with the upstream's own msg —
     and never with the password or the verification code,
  2. a rejected NetMind login token carries upstream status/msg in the
     exception (never the token) so the route's 401 log can bucket them,
  3. the client-side blind spot has an ingest (/funnel-report): pre-auth by
     nature, log-only, rate-limited, log-forging-resistant.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger

import backend.routes.auth as auth_mod
from backend.integrations.netmind.netmind_auth_client import (
    NetmindAuthClient,
    NetmindAuthError,
)
from backend.integrations.netmind.netmind_register_client import (
    NetmindRegisterClient,
    RegistrationError,
)

GOOD_PW = "Aa1!aaaa"


@pytest.fixture
def log_lines():
    """Capture loguru output for the duration of a test."""
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    yield lines
    logger.remove(sink_id)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth_mod, "is_power_login_enabled", lambda: True)
    from backend.routes._rate_limiter import SlidingWindowRateLimiter
    monkeypatch.setattr(auth_mod, "_funnel_report_limiter",
                        SlidingWindowRateLimiter(limit=10, window_sec=60.0))
    monkeypatch.setattr(auth_mod, "_funnel_report_ip_limiter",
                        SlidingWindowRateLimiter(limit=30, window_sec=60.0))
    monkeypatch.setattr(auth_mod, "_funnel_report_global_limiter",
                        SlidingWindowRateLimiter(limit=120, window_sec=60.0))
    from time import monotonic as _mono
    monkeypatch.setattr(auth_mod, "_funnel_dropped",
                        {"count": 0, "last_log": _mono()})
    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


# ── 1. upstream refusals are logged, secrets are not ───────────────────────

def _register_client_with(handler) -> NetmindRegisterClient:
    return NetmindRegisterClient(
        base_url="https://userauth.test.invalid",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_upstream_refusal_is_logged_with_upstream_msg(log_lines):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"success": False, "msg": "verification code error"}
        )

    with pytest.raises(RegistrationError):
        await _register_client_with(handler).register(
            "a@b.com", GOOD_PW, "424242"
        )
    joined = "\n".join(log_lines)
    assert "[signup-funnel] upstream refusal" in joined
    assert "verification code error" in joined
    assert "a@b.com" in joined  # bucketing needs the email


@pytest.mark.asyncio
async def test_refusal_log_never_contains_password_or_code(log_lines):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "msg": "nope"})

    with pytest.raises(RegistrationError):
        await _register_client_with(handler).register(
            "a@b.com", GOOD_PW, "424242"
        )
    joined = "\n".join(log_lines)
    assert GOOD_PW not in joined
    assert "424242" not in joined


@pytest.mark.asyncio
async def test_upstream_5xx_logs_a_body_snippet(log_lines):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>bad gateway from CDN</html>")

    from backend.integrations.netmind.netmind_register_client import (
        RegistrationUpstreamError,
    )
    with pytest.raises(RegistrationUpstreamError):
        await _register_client_with(handler).send_code("a@b.com")
    joined = "\n".join(log_lines)
    assert "[signup-funnel] upstream 5xx" in joined
    assert "bad gateway" in joined


# ── 2. rejected login tokens carry upstream detail, never the token ────────

def _auth_client_with(handler) -> NetmindAuthClient:
    return NetmindAuthClient(
        base_url="https://userauth.test.invalid",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_rejected_token_error_carries_status_and_msg_not_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"success": False, "msg": "token expired"}
        )

    with pytest.raises(NetmindAuthError) as exc_info:
        await _auth_client_with(handler).verify_token("secret-jwt-value")
    message = str(exc_info.value)
    assert "token expired" in message
    assert "status=200" in message
    assert "secret-jwt-value" not in message


@pytest.mark.asyncio
async def test_rejected_token_via_4xx_carries_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    with pytest.raises(NetmindAuthError) as exc_info:
        await _auth_client_with(handler).verify_token("secret-jwt-value")
    assert "status=401" in str(exc_info.value)
    assert "secret-jwt-value" not in str(exc_info.value)


# ── 3. the client-side blind-spot ingest ───────────────────────────────────

def test_funnel_report_logs_the_stage_and_detail(client, log_lines):
    r = client.post("/api/auth/funnel-report", json={
        "stage": "netmind_email_login_failed",
        "email": "a@b.com",
        "detail": "password error",
    })
    assert r.status_code == 200
    joined = "\n".join(log_lines)
    assert "[login-funnel] client stage=netmind_email_login_failed" in joined
    assert "a@b.com" in joined
    assert "password error" in joined


def test_funnel_report_rejects_unknown_stages(client):
    r = client.post("/api/auth/funnel-report",
                    json={"stage": "made_up_stage", "detail": "x"})
    assert r.status_code == 400


def test_funnel_report_is_404_in_local_mode(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "is_power_login_enabled", lambda: False)
    r = client.post("/api/auth/funnel-report",
                    json={"stage": "netmind_email_login_failed"})
    assert r.status_code == 404


def test_funnel_report_rate_limit_accepts_silently_but_counts(client, monkeypatch, log_lines):
    from time import monotonic
    from backend.routes._rate_limiter import SlidingWindowRateLimiter
    monkeypatch.setattr(auth_mod, "_funnel_report_limiter",
                        SlidingWindowRateLimiter(limit=1, window_sec=60.0))
    # Pretend the last summary happened >60s ago so this drop flushes now.
    monkeypatch.setattr(auth_mod, "_funnel_dropped",
                        {"count": 0, "last_log": monotonic() - 61.0})
    body = {"stage": "netmind_email_login_failed", "email": "a@b.com", "detail": "x"}
    assert client.post("/api/auth/funnel-report", json=body).status_code == 200
    # Over the limit: still 200 (diagnostics must never add an error on top
    # of the failure the user is already looking at), no per-report line —
    # but the drop is COUNTED for ops (a NetMind-wide outage must not read
    # as "only N people affected").
    n_before = sum("[login-funnel] client" in ln for ln in log_lines)
    assert client.post("/api/auth/funnel-report", json=body).status_code == 200
    n_after = sum("[login-funnel] client" in ln for ln in log_lines)
    assert n_after == n_before
    assert any("[login-funnel] dropped 1 client report" in ln for ln in log_lines)


def test_funnel_report_drop_within_window_stays_quiet(client, monkeypatch, log_lines):
    """A fresh boot (last_log = now) must not flush a single drop as if it
    summarised a whole window."""
    from backend.routes._rate_limiter import SlidingWindowRateLimiter
    monkeypatch.setattr(auth_mod, "_funnel_report_limiter",
                        SlidingWindowRateLimiter(limit=1, window_sec=60.0))
    body = {"stage": "netmind_email_login_failed", "email": "a@b.com", "detail": "x"}
    client.post("/api/auth/funnel-report", json=body)
    client.post("/api/auth/funnel-report", json=body)  # dropped, counted
    assert not any("[login-funnel] dropped" in ln for ln in log_lines)
    assert auth_mod._funnel_dropped["count"] == 1


def test_ip_bucket_rejection_never_spends_the_global_budget(client, monkeypatch, log_lines):
    """The order IS the invariant: per-IP first, global last — a request
    the per-IP bucket rejects must not have touched the global budget, or
    one noisy client burns the whole minute for everyone."""
    from backend.routes._rate_limiter import SlidingWindowRateLimiter
    monkeypatch.setattr(auth_mod, "_funnel_report_ip_limiter",
                        SlidingWindowRateLimiter(limit=1, window_sec=60.0))
    monkeypatch.setattr(auth_mod, "_funnel_report_global_limiter",
                        SlidingWindowRateLimiter(limit=2, window_sec=60.0))
    body = {"stage": "netmind_email_login_failed", "detail": "x"}
    # Same socket peer -> same IP bucket. First passes, next two are
    # rejected by per-IP — global must still hold 1 unspent slot.
    for _ in range(3):
        assert client.post("/api/auth/funnel-report", json=body).status_code == 200
    assert sum("[login-funnel] client" in ln for ln in log_lines) == 1
    assert auth_mod._funnel_report_global_limiter.allow("global") is True


def test_ip_bucket_rejection_allocates_no_caller_chosen_keys(client, monkeypatch):
    """ORDER GUARD: per-IP must evaluate BEFORE the caller-keyed email
    bucket. A new key under limit>0 is always allowed-and-allocated by the
    limiter, so the trusted bucket short-circuiting in front is the email
    bucket's only key-space cap — put email first and this flood of five
    rotating emails allocates five keys and this test goes red. If a
    reorder turns it red, the reorder is the bug, not this assert."""
    from backend.routes._rate_limiter import SlidingWindowRateLimiter
    monkeypatch.setattr(auth_mod, "_funnel_report_ip_limiter",
                        SlidingWindowRateLimiter(limit=2, window_sec=60.0))
    for i in range(2):  # spend the IP budget
        client.post("/api/auth/funnel-report", json={
            "stage": "netmind_email_login_failed",
            "email": f"warm{i}@b.com", "detail": "x",
        })
    keys_before = len(auth_mod._funnel_report_limiter._deques)
    for i in range(5):  # flood with rotating emails, all rejected by per-IP
        client.post("/api/auth/funnel-report", json={
            "stage": "netmind_email_login_failed",
            "email": f"flood{i}@b.com", "detail": "x",
        })
    assert len(auth_mod._funnel_report_limiter._deques) == keys_before


def test_rejected_allow_allocates_no_key_at_the_limiter_itself():
    """The limiter's DEFENSIVE guarantee: reject != allocate. Honest about
    its scope — with limit>0 a NEW key is always allowed (0 < limit), so
    this is observable only when an existing key is over limit or under
    the limit<=0 degenerate config. It is NOT what caps caller-chosen key
    growth (the order guard above is; see _rate_limiter.allow's docstring)."""
    from backend.routes._rate_limiter import SlidingWindowRateLimiter
    lim = SlidingWindowRateLimiter(limit=1, window_sec=60.0)
    assert lim.allow("a") is True
    assert lim.allow("a") is False          # rejected, key existed already
    assert len(lim._deques) == 1
    zero = SlidingWindowRateLimiter(limit=0, window_sec=60.0)
    for i in range(50):                     # a rejected flood on fresh keys
        assert zero.allow(f"fresh{i}") is False
    assert len(zero._deques) == 0           # ...allocated nothing
    # And the restructure never changes a verdict: a brand-new key under a
    # sane limit still passes.
    assert lim.allow("b") is True


def test_trusted_proxy_hops_parsing_is_clamped_and_forgiving():
    """hops=0 would make parts[-0] == parts[0] (the caller-written entry)
    and disable the short-chain fallback; empty/garbage env values must
    not crash the module import."""
    parse = auth_mod._parse_trusted_proxy_hops
    assert parse(None) == 2      # unset
    assert parse("") == 2        # FUNNEL_TRUSTED_PROXY_HOPS= (empty)
    assert parse("0") == 1       # clamped: never index from the left
    assert parse("-3") == 1
    assert parse("abc") == 2     # garbage -> default, not ValueError
    assert parse("3") == 3


def test_client_ip_is_counted_from_the_right_of_xff():
    """Both proxies APPEND to X-Forwarded-For, so the first hop is
    caller-controlled; the trustworthy entry is Nth from the right."""
    class _Req:
        def __init__(self, xff):
            self.headers = {"x-forwarded-for": xff} if xff else {}
            self.client = type("C", (), {"host": "10.0.0.9"})()

    # forged, real (appended by caddy), caddy container (appended by nginx)
    assert auth_mod._funnel_client_ip(_Req("6.6.6.6, 51.0.0.7, 172.18.0.5")) == "51.0.0.7"
    # No forgery: real + caddy hop.
    assert auth_mod._funnel_client_ip(_Req("51.0.0.7, 172.18.0.5")) == "51.0.0.7"
    # Rotating the forged FIRST entry does not move the answer.
    assert auth_mod._funnel_client_ip(_Req("7.7.7.7, 51.0.0.7, 172.18.0.5")) == "51.0.0.7"
    # Shorter-than-expected chain (a directly forged single entry): fall
    # back to the socket peer, never trust caller text.
    assert auth_mod._funnel_client_ip(_Req("6.6.6.6")) == "10.0.0.9"
    assert auth_mod._funnel_client_ip(_Req(None)) == "10.0.0.9"


def test_funnel_report_email_rotation_cannot_dodge_the_ip_bucket(client, monkeypatch, log_lines):
    """The spent resource is the global log, so a caller-chosen key (email)
    must not be the only bucket."""
    from backend.routes._rate_limiter import SlidingWindowRateLimiter
    monkeypatch.setattr(auth_mod, "_funnel_report_ip_limiter",
                        SlidingWindowRateLimiter(limit=2, window_sec=60.0))
    for i in range(3):
        r = client.post("/api/auth/funnel-report", json={
            "stage": "netmind_email_login_failed",
            "email": f"rotate{i}@b.com",
            "detail": "x",
        })
        assert r.status_code == 200
    assert sum("[login-funnel] client" in ln for ln in log_lines) == 2


def test_funnel_report_strips_newlines_against_log_forging(client, log_lines):
    client.post("/api/auth/funnel-report", json={
        "stage": "netmind_oauth_failed",
        "detail": "line1\nFAKE-LOG-LINE",
    })
    hit = next(ln for ln in log_lines if "[login-funnel] client" in ln)
    assert "line1 FAKE-LOG-LINE" in hit


def test_funnel_report_is_exempt_from_auth():
    from backend.auth import AUTH_EXEMPT_PATHS

    assert "/api/auth/funnel-report" in AUTH_EXEMPT_PATHS
