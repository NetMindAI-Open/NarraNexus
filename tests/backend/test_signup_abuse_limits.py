"""
@file_name: test_signup_abuse_limits.py
@author: Bin Liang
@date: 2026-10-28
@description: Abuse limits on the two unauthenticated signup writes.

2026-10-28 incident: one IP self-registered accounts and could have used
POST /api/auth/signup/send-code as a mail cannon — the only limit was one
code per EMAIL per minute, so a fresh address per request was unlimited.
These tests pin the closures:

  1. per-client-IP sliding-window buckets on send-code (minute + hour) and
     on signup (hour), keyed by the shared ``client_ip`` helper — the N+1th
     request from one IP is 429 with Retry-After while another IP passes.
     The signup bucket only keeps a hit for an account actually created: a
     wrong code, an "already registered", an upstream 502 or a per-email 429
     hands the hit back, so failures cannot lock a shared office out. A
     looser per-IP ATTEMPT bucket in front of it is charged for every
     attempt and never refunded, so failures are still capped per IP,
  2. the per-email limits still apply on top,
  3. throwaway-mailbox domains (and their subdomains) are refused with a
     neutral 400 BEFORE any bucket is spent or any upstream call is made,
  4. the per-IP numbers are env-configurable with forgiving parsing,
  5. a blocked-domain account created directly at NetMind (the auth API URL
     is public) still logs in, but gets no free-tier wallet and no
     onboarding guide agent,
  6. POST /api/auth/netmind-login (unauthenticated, one upstream
     verify_token call per request) has a loose per-IP hour bucket.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.auth as auth_mod
from backend.integrations.netmind.netmind_auth_client import NetmindUser
from backend.integrations.netmind.netmind_register_client import (
    RegistrationError,
    RegistrationUpstreamError,
)
from backend.routes._rate_limiter import SlidingWindowRateLimiter, env_limit

GOOD_PW = "Aa1!aaaa"
PROXY_HOP = "172.18.0.5"  # the caddy container nginx appends


def _xff(ip: str) -> dict:
    # client -> caddy -> nginx -> backend: the edge-appended entry is 2nd
    # from the right (TRUSTED_PROXY_HOPS default).
    return {"X-Forwarded-For": f"{ip}, {PROXY_HOP}"}


# The SHIPPED limits, independent of whatever a developer's env sets.
CODE_PER_MINUTE = auth_mod.DEFAULT_SIGNUP_CODE_PER_IP_PER_MINUTE
CODE_PER_HOUR = auth_mod.DEFAULT_SIGNUP_CODE_PER_IP_PER_HOUR
SIGNUP_PER_HOUR = auth_mod.DEFAULT_SIGNUP_PER_IP_PER_HOUR
SIGNUP_ATTEMPTS_PER_HOUR = auth_mod.DEFAULT_SIGNUP_ATTEMPTS_PER_IP_PER_HOUR
NETMIND_LOGIN_PER_HOUR = auth_mod.DEFAULT_NETMIND_LOGIN_PER_IP_PER_HOUR


def _fresh(limit: int, window: float) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(limit=limit, window_sec=window)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth_mod, "is_power_login_enabled", lambda: True)
    monkeypatch.delenv("NARRANEXUS_BLOCKED_EMAIL_DOMAINS", raising=False)
    # Fresh buckets at the SHIPPED limits — module state must not leak.
    monkeypatch.setattr(auth_mod, "_signup_code_limiter", _fresh(1, 60.0))
    monkeypatch.setattr(auth_mod, "_signup_attempt_limiter", _fresh(10, 600.0))
    monkeypatch.setattr(
        auth_mod, "_signup_code_ip_minute_limiter", _fresh(CODE_PER_MINUTE, 60.0)
    )
    monkeypatch.setattr(
        auth_mod, "_signup_code_ip_hour_limiter", _fresh(CODE_PER_HOUR, 3600.0)
    )
    monkeypatch.setattr(
        auth_mod, "_signup_ip_limiter", _fresh(SIGNUP_PER_HOUR, 3600.0)
    )
    monkeypatch.setattr(
        auth_mod,
        "_signup_attempt_ip_limiter",
        _fresh(SIGNUP_ATTEMPTS_PER_HOUR, 3600.0),
    )
    monkeypatch.setattr(
        auth_mod,
        "_netmind_login_ip_limiter",
        _fresh(NETMIND_LOGIN_PER_HOUR, 3600.0),
    )
    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


@pytest.fixture
def upstream(monkeypatch):
    stub = AsyncMock()
    import backend.integrations.netmind.netmind_register_client as mod
    monkeypatch.setattr(mod.NetmindRegisterClient, "send_code", stub, raising=False)
    monkeypatch.setattr(mod.NetmindRegisterClient, "register", stub, raising=False)
    return stub


def _send_code(client, email, ip):
    return client.post(
        "/api/auth/signup/send-code", json={"email": email}, headers=_xff(ip)
    )


def _signup(client, email, ip):
    return client.post(
        "/api/auth/signup",
        json={"email": email, "password": GOOD_PW, "verify_code": "123456"},
        headers=_xff(ip),
    )


# ── shipped defaults ───────────────────────────────────────────────────────

def test_shipped_defaults():
    """The defaults handed to ``env_limit`` — never the env-resolved values, so
    a developer's own env cannot turn this red. Sized for an office / event
    NAT: a room of real people sharing one egress must fit."""
    assert auth_mod.DEFAULT_SIGNUP_CODE_PER_IP_PER_MINUTE == 10
    assert auth_mod.DEFAULT_SIGNUP_CODE_PER_IP_PER_HOUR == 60
    assert auth_mod.DEFAULT_SIGNUP_PER_IP_PER_HOUR == 20
    assert auth_mod.DEFAULT_SIGNUP_ATTEMPTS_PER_IP_PER_HOUR == 60
    assert auth_mod.DEFAULT_NETMIND_LOGIN_PER_IP_PER_HOUR == 120


def test_unset_env_resolves_to_the_shipped_defaults(monkeypatch):
    for name in ("NARRANEXUS_SIGNUP_CODE_PER_IP_PER_MINUTE",
                 "NARRANEXUS_SIGNUP_CODE_PER_IP_PER_HOUR",
                 "NARRANEXUS_SIGNUP_PER_IP_PER_HOUR",
                 "NARRANEXUS_SIGNUP_ATTEMPTS_PER_IP_PER_HOUR",
                 "NARRANEXUS_NETMIND_LOGIN_PER_IP_PER_HOUR"):
        monkeypatch.delenv(name, raising=False)
    assert env_limit("NARRANEXUS_SIGNUP_CODE_PER_IP_PER_MINUTE",
                     auth_mod.DEFAULT_SIGNUP_CODE_PER_IP_PER_MINUTE) == 10
    assert env_limit("NARRANEXUS_SIGNUP_ATTEMPTS_PER_IP_PER_HOUR",
                     auth_mod.DEFAULT_SIGNUP_ATTEMPTS_PER_IP_PER_HOUR) == 60
    assert env_limit("NARRANEXUS_NETMIND_LOGIN_PER_IP_PER_HOUR",
                     auth_mod.DEFAULT_NETMIND_LOGIN_PER_IP_PER_HOUR) == 120


# ── 1. per-IP buckets ──────────────────────────────────────────────────────

def test_send_code_per_ip_minute_bucket_rotating_emails(client, upstream):
    """A fresh email per request used to be unlimited: one IP could aim our
    mail channel at any number of mailboxes."""
    limit = CODE_PER_MINUTE
    for i in range(limit):
        assert _send_code(client, f"victim{i}@example.com", "51.0.0.7").status_code == 200
    r = _send_code(client, "victim-next@example.com", "51.0.0.7")
    assert r.status_code == 429
    retry_after = int(r.headers["Retry-After"])
    assert 1 <= retry_after <= 60
    # A different client IP has its own bucket.
    assert _send_code(client, "someone@example.com", "51.0.0.8").status_code == 200
    assert upstream.await_count == limit + 1


def test_send_code_per_ip_hour_bucket(client, upstream, monkeypatch):
    # Neutralise the minute bucket so the hour bucket is what is under test.
    monkeypatch.setattr(auth_mod, "_signup_code_ip_minute_limiter", _fresh(10_000, 60.0))
    limit = CODE_PER_HOUR
    for i in range(limit):
        assert _send_code(client, f"v{i}@example.com", "51.0.0.7").status_code == 200
    r = _send_code(client, "v-next@example.com", "51.0.0.7")
    assert r.status_code == 429
    assert 60 < int(r.headers["Retry-After"]) <= 3600
    assert _send_code(client, "other@example.com", "51.0.0.9").status_code == 200


def test_send_code_ip_is_the_edge_entry_not_the_forged_first_hop(client, upstream):
    """Rotating a forged leading XFF entry must not mint a new bucket."""
    limit = CODE_PER_MINUTE
    for i in range(limit):
        r = client.post(
            "/api/auth/signup/send-code",
            json={"email": f"f{i}@example.com"},
            headers={"X-Forwarded-For": f"6.6.6.{i}, 51.0.0.7, {PROXY_HOP}"},
        )
        assert r.status_code == 200
    r = client.post(
        "/api/auth/signup/send-code",
        json={"email": "f-next@example.com"},
        headers={"X-Forwarded-For": f"6.6.6.99, 51.0.0.7, {PROXY_HOP}"},
    )
    assert r.status_code == 429


def test_send_code_per_email_limit_still_applies(client, upstream):
    assert _send_code(client, "a@example.com", "51.0.0.7").status_code == 200
    r = _send_code(client, "a@example.com", "51.0.0.8")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_send_code_per_ip_rejection_allocates_no_email_keys(client, upstream):
    """Per-IP runs before the caller-keyed per-email bucket: a flood rejected
    by per-IP must not grow the per-email key space."""
    limit = CODE_PER_MINUTE
    for i in range(limit):
        _send_code(client, f"warm{i}@example.com", "51.0.0.7")
    keys_before = len(auth_mod._signup_code_limiter._deques)
    for i in range(5):
        assert _send_code(client, f"flood{i}@example.com", "51.0.0.7").status_code == 429
    assert len(auth_mod._signup_code_limiter._deques) == keys_before


def test_signup_per_ip_hour_bucket(client, upstream):
    limit = SIGNUP_PER_HOUR
    for i in range(limit):
        assert _signup(client, f"new{i}@example.com", "51.0.0.7").status_code == 200
    r = _signup(client, "new-next@example.com", "51.0.0.7")
    assert r.status_code == 429
    assert 1 <= int(r.headers["Retry-After"]) <= 3600
    assert _signup(client, "new-other@example.com", "51.0.0.8").status_code == 200
    assert upstream.await_count == limit + 1


def test_signup_per_email_limit_still_applies(client, upstream, monkeypatch):
    monkeypatch.setattr(auth_mod, "_signup_ip_limiter", _fresh(10_000, 3600.0))
    for _ in range(10):
        assert _signup(client, "same@example.com", "51.0.0.7").status_code == 200
    r = _signup(client, "same@example.com", "51.0.0.7")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def _assert_full_ip_budget_left(client, ip):
    """Exactly SIGNUP_PER_HOUR fresh signups from ``ip`` succeed, then 429 —
    i.e. nothing earlier was charged to this IP."""
    for i in range(SIGNUP_PER_HOUR):
        assert _signup(client, f"later{i}@example.com", ip).status_code == 200, i
    assert _signup(client, "later-next@example.com", ip).status_code == 429


@pytest.mark.parametrize("failure,status", [
    (RegistrationError("Invalid verification code"), 400),
    (RegistrationError("Email already registered"), 400),
    (RegistrationUpstreamError("NetMind 503"), 502),
])
def test_failed_signup_does_not_spend_the_ip_bucket(client, upstream, failure, status):
    """The per-IP signup bucket is a shared office's budget for CREATING
    accounts. A mistyped code, a re-register or a NetMind outage creates
    nothing, so it must hand its hit back — code brute force is the per-email
    bucket's job."""
    upstream.side_effect = failure
    for i in range(SIGNUP_PER_HOUR + 3):
        assert _signup(client, f"fail{i}@example.com", "51.0.0.7").status_code == status
    upstream.side_effect = None
    _assert_full_ip_budget_left(client, "51.0.0.7")


def test_per_email_429_does_not_spend_the_ip_bucket(client, upstream, monkeypatch):
    monkeypatch.setattr(auth_mod, "_signup_attempt_limiter", _fresh(0, 600.0))
    for _ in range(SIGNUP_PER_HOUR + 3):
        assert _signup(client, "same@example.com", "51.0.0.7").status_code == 429
    upstream.assert_not_awaited()
    monkeypatch.setattr(auth_mod, "_signup_attempt_limiter", _fresh(10_000, 600.0))
    _assert_full_ip_budget_left(client, "51.0.0.7")


def test_failed_signups_are_capped_by_the_attempt_bucket(client, upstream):
    """The creation bucket refunds every failure, so on its own it never
    stops a caller rotating emails with a junk code — each request would
    still cost us one upstream register call. The attempt bucket is charged
    for every attempt and never refunded."""
    upstream.side_effect = RegistrationError("Invalid verification code")
    for i in range(SIGNUP_ATTEMPTS_PER_HOUR):
        assert _signup(client, f"junk{i}@example.com", "51.0.0.7").status_code == 400, i
    r = _signup(client, "junk-next@example.com", "51.0.0.7")
    assert r.status_code == 429
    assert 60 < int(r.headers["Retry-After"]) <= 3600
    assert upstream.await_count == SIGNUP_ATTEMPTS_PER_HOUR
    # Another client IP has its own attempt bucket.
    assert _signup(client, "junk-other@example.com", "51.0.0.8").status_code == 400


def test_successful_signups_also_spend_the_attempt_bucket(client, upstream, monkeypatch):
    monkeypatch.setattr(auth_mod, "_signup_attempt_ip_limiter", _fresh(3, 3600.0))
    monkeypatch.setattr(auth_mod, "_signup_ip_limiter", _fresh(10_000, 3600.0))
    upstream.side_effect = RegistrationError("Invalid verification code")
    assert _signup(client, "a@example.com", "51.0.0.7").status_code == 400
    upstream.side_effect = None
    assert _signup(client, "b@example.com", "51.0.0.7").status_code == 200
    assert _signup(client, "c@example.com", "51.0.0.7").status_code == 200
    assert _signup(client, "d@example.com", "51.0.0.7").status_code == 429


def test_attempt_bucket_rejection_spends_no_creation_hit_and_no_email_key(
    client, upstream, monkeypatch
):
    """The attempt bucket runs BEFORE the creation bucket: a request it
    refuses must neither reserve a creation hit (that reservation would never
    be refunded — the refund lives in a try block not yet entered) nor grow
    the caller-keyed per-email key space."""
    monkeypatch.setattr(auth_mod, "_signup_attempt_ip_limiter", _fresh(0, 3600.0))
    for i in range(SIGNUP_PER_HOUR + 3):
        assert _signup(client, f"flood{i}@example.com", "51.0.0.7").status_code == 429
    upstream.assert_not_awaited()
    assert len(auth_mod._signup_attempt_limiter._deques) == 0
    monkeypatch.setattr(
        auth_mod, "_signup_attempt_ip_limiter", _fresh(10_000, 3600.0)
    )
    _assert_full_ip_budget_left(client, "51.0.0.7")


def test_signup_blocked_domain_does_not_spend_the_ip_bucket(client, upstream):
    for _ in range(SIGNUP_PER_HOUR + 3):
        assert _signup(client, "x@mailinator.com", "51.0.0.7").status_code == 400
    upstream.assert_not_awaited()
    _assert_full_ip_budget_left(client, "51.0.0.7")


def test_weak_password_does_not_spend_the_ip_bucket(client, upstream):
    """Policy rejects spend nothing upstream, so they must not lock a real
    user's network out of signup for an hour."""
    for _ in range(SIGNUP_PER_HOUR + 2):
        r = client.post(
            "/api/auth/signup",
            json={"email": "a@example.com", "password": "weak", "verify_code": "1"},
            headers=_xff("51.0.0.7"),
        )
        assert r.status_code == 400
    assert _signup(client, "a@example.com", "51.0.0.7").status_code == 200


# ── 3. disposable-email rejection ──────────────────────────────────────────

@pytest.mark.parametrize("email", [
    "x@mailinator.com",
    "x@MAILINATOR.com",
    "x@eu.mailinator.com",      # subdomain of a listed domain
    "x@yopmail.com",
    "x@foo.cc.cd",
])
def test_send_code_refuses_disposable_domains(client, upstream, email):
    r = _send_code(client, email, "51.0.0.7")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "disposable" not in detail.lower()  # neutral wording
    upstream.assert_not_awaited()


def test_signup_refuses_disposable_domains(client, upstream):
    r = _signup(client, "x@guerrillamail.com", "51.0.0.7")
    assert r.status_code == 400
    upstream.assert_not_awaited()


def test_blocked_domain_spends_no_bucket(client, upstream):
    for _ in range(CODE_PER_MINUTE + 3):
        assert _send_code(client, "x@mailinator.com", "51.0.0.7").status_code == 400
    assert _send_code(client, "real@example.com", "51.0.0.7").status_code == 200


def test_normal_domain_and_lookalike_pass(client, upstream):
    assert _send_code(client, "a@gmail.com", "51.0.0.7").status_code == 200
    # Suffix match is on label boundaries: notmailinator.com is not listed.
    assert _send_code(client, "a@notmailinator.com", "51.0.0.8").status_code == 200


def test_env_extends_the_blocklist(client, upstream, monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BLOCKED_EMAIL_DOMAINS", " Burner-Mail.io , @other-burner.org ")
    assert _send_code(client, "a@burner-mail.io", "51.0.0.7").status_code == 400
    assert _send_code(client, "a@sub.other-burner.org", "51.0.0.7").status_code == 400
    assert _send_code(client, "a@example.com", "51.0.0.7").status_code == 200


# ── 4. env parsing ─────────────────────────────────────────────────────────

def test_env_limit_parsing(monkeypatch):
    name = "NARRANEXUS_TEST_LIMIT"
    monkeypatch.delenv(name, raising=False)
    assert env_limit(name, 5) == 5
    for raw, want in (("", 5), ("abc", 5), ("0", 5), ("-2", 5), ("12", 12), (" 7 ", 7)):
        monkeypatch.setenv(name, raw)
        assert env_limit(name, 5) == want, raw


def test_retry_after_counts_down_from_the_oldest_hit(monkeypatch):
    import backend.routes._rate_limiter as rl

    now = [1000.0]
    monkeypatch.setattr(rl, "monotonic", lambda: now[0])
    lim = SlidingWindowRateLimiter(limit=2, window_sec=60.0)
    assert lim.allow("k") and lim.allow("k")
    now[0] += 20.5
    assert lim.allow("k") is False
    assert lim.retry_after("k") == 40  # ceil(60 - 20.5)
    assert SlidingWindowRateLimiter(limit=0, window_sec=60.0).retry_after("x") == 60


def test_refund_hands_back_one_granted_hit():
    lim = SlidingWindowRateLimiter(limit=2, window_sec=60.0)
    assert lim.allow("k") and lim.allow("k")
    assert lim.allow("k") is False
    lim.refund("k")
    assert lim.allow("k") is True
    assert lim.allow("k") is False
    lim.refund("never-seen")  # no state: a no-op, never an error


# ── 5. login-time provisioning (NetMind-direct registration bypass) ────────

_LOGIN_CODE = "b" * 32


@pytest.mark.parametrize("email,eligible", [
    ("bulk@mailinator.com", False),
    ("bulk@eu.yopmail.com", False),   # subdomain of a listed domain
    ("bulk@example.com", True),
])
def test_login_withholds_free_credit_from_blocked_domains(
    db_client, monkeypatch, email, eligible
):
    """send-code / signup refuse throwaway domains, but NetMind's own signup
    is public (its auth URL ships in /config.js), and the free wallet and the
    onboarding guide agent are provisioned at LOGIN. The blocked user still
    gets in — they just get no free credit."""
    import backend.integrations.free_tier.provisioner as free_mod
    import backend.integrations.netmind.netmind_provisioner as nm_mod
    import backend.onboarding.provisioning as ob_pkg
    from narranexus.platform.utils.db import db_factory

    async def _db():
        return db_client

    monkeypatch.setattr(auth_mod, "get_db_client", _db)
    monkeypatch.setattr(auth_mod, "is_power_login_enabled", lambda: True)

    class _Verifier:
        async def verify_token(self, token):
            return NetmindUser(user_system_code=_LOGIN_CODE, email=email, nickname="B")

    monkeypatch.setattr(auth_mod, "_get_netmind_auth_client", lambda: _Verifier())

    # The REAL free-tier provisioner runs; only its wallet service and its
    # dedup read are stubbed, so "no wallet call" is observed at the wallet.
    wallet = MagicMock()
    wallet.provision = AsyncMock(side_effect=RuntimeError("wallet reached"))
    monkeypatch.setattr(free_mod, "is_free_tier_enabled", lambda: True)
    monkeypatch.setattr(
        free_mod.WalletClient, "from_settings", classmethod(lambda cls: wallet)
    )
    dedup_db = MagicMock()
    dedup_db.get_one = AsyncMock(return_value=None)
    monkeypatch.setattr(db_factory, "get_db_client", AsyncMock(return_value=dedup_db))
    free_mod._locks.clear()

    own_card: list[str] = []

    async def _netmind(user_id, token, **_kw):
        own_card.append(user_id)

    monkeypatch.setattr(nm_mod, "ensure_netmind_provider", _netmind)

    monkeypatch.setenv("NARRANEXUS_ONBOARDING_GUIDE_AGENT", "1")
    guide: list[str] = []

    async def _guide(db, user_id, *, is_new_user=False):
        guide.append(user_id)

    monkeypatch.setattr(ob_pkg, "ensure_guide_agent", _guide)

    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")
    with TestClient(app) as client:  # keeps the loop alive for the tasks
        resp = client.post("/api/auth/netmind-login", json={"netmind_token": "t"})

    assert resp.status_code == 200
    assert resp.json()["user_id"] == _LOGIN_CODE
    assert wallet.provision.await_count == (1 if eligible else 0)
    assert guide == ([_LOGIN_CODE] if eligible else [])
    # The frontend coachmark must not promise a guide that will never come.
    assert resp.json()["guide_agent_provisioning"] is eligible
    # Their OWN NetMind card is theirs to use either way.
    assert own_card == [_LOGIN_CODE]


# ── 6. netmind-login per-IP bucket ─────────────────────────────────────────

class _RejectingVerifier:
    """A NetMind auth client that refuses every token, counting the calls."""

    def __init__(self):
        self.calls = 0

    async def verify_token(self, token):
        from backend.integrations.netmind.netmind_auth_client import (
            NetmindAuthError,
        )

        self.calls += 1
        raise NetmindAuthError("401 token invalid")


def _netmind_login(client, ip):
    return client.post(
        "/api/auth/netmind-login", json={"netmind_token": "junk"}, headers=_xff(ip)
    )


def test_netmind_login_per_ip_hour_bucket(client, monkeypatch):
    """Unauthenticated, and every request costs one upstream verify_token
    call — an invalid token included. The bucket is charged for every
    request (never refunded) and checked before the upstream call."""
    verifier = _RejectingVerifier()
    monkeypatch.setattr(auth_mod, "_get_netmind_auth_client", lambda: verifier)
    for i in range(NETMIND_LOGIN_PER_HOUR):
        assert _netmind_login(client, "51.0.0.7").status_code == 401, i
    r = _netmind_login(client, "51.0.0.7")
    assert r.status_code == 429
    assert 60 < int(r.headers["Retry-After"]) <= 3600
    assert verifier.calls == NETMIND_LOGIN_PER_HOUR
    # Another client IP has its own bucket.
    assert _netmind_login(client, "51.0.0.8").status_code == 401
    assert verifier.calls == NETMIND_LOGIN_PER_HOUR + 1


def test_netmind_login_bucket_not_spent_when_power_login_is_off(client, monkeypatch):
    """Local mode without Power login answers 404 before the bucket, like
    the signup routes: nothing is charged."""
    verifier = _RejectingVerifier()
    monkeypatch.setattr(auth_mod, "_get_netmind_auth_client", lambda: verifier)
    monkeypatch.setattr(auth_mod, "_netmind_login_ip_limiter", _fresh(1, 3600.0))
    monkeypatch.setattr(auth_mod, "is_power_login_enabled", lambda: False)
    for _ in range(3):
        assert _netmind_login(client, "51.0.0.7").status_code == 404
    monkeypatch.setattr(auth_mod, "is_power_login_enabled", lambda: True)
    assert _netmind_login(client, "51.0.0.7").status_code == 401
    assert _netmind_login(client, "51.0.0.7").status_code == 429
    assert verifier.calls == 1
