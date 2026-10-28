"""
@file_name: test_channel_webhook_middleware.py
@author: Bin Liang
@date: 2026-09-07
@description: The channel-webhook auth exemption wired through the REAL auth_middleware, plus the webhook's negatives (bad secret, rate limits, unsafe id).

``backend/auth.py``'s ``_is_channel_webhook_path`` is the single most sensitive
line in the generic channel API: since batch 4c the webhook is the ONLY
session-less entry point. It was covered by a pure-function string test only,
and the route file's own app fixture mounts a hand-rolled identity middleware —
so both directions were unguarded. Delete the exemption and every inbound
webhook 401s in cloud with a green suite; widen the regex and a slice of
``/api/channels/*`` serves unauthenticated, also green.

Cloud mode is forced explicitly: in local mode the middleware admits any caller
carrying ``X-User-Id``, which would make "reached the handler" prove nothing.
"""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes._ownership as own
from backend import auth as auth_mod
from backend.auth import auth_middleware, create_token
from backend.routes._rate_limiter import SlidingWindowRateLimiter
from backend.routes.channels import generic as generic_mod
from narranexus.contracts.channel import ChannelDescriptor, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.channel import credential_codec

CHANNEL = "wh_mw"
DESC = ChannelDescriptor(
    name=CHANNEL,
    display_name="WH-MW",
    transport="webhook",
    credential_schema=CredentialSchema(fields=(CredentialField("api_token", "secret"),), supports_test=False),
    has_test=False,
)
JWT = {"Authorization": f"Bearer {create_token(user_id='u1', role='user')}"}


@pytest.fixture
def cloud_client(db_client, monkeypatch, tmp_path: Path):
    credential_codec.use_key_dir(tmp_path / "keys")

    async def _db():
        return db_client

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(generic_mod, "_db", _db)

    async def _resolve(self, agent_id):
        return "u1"

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)
    # The exemption only means something in cloud mode; local admits everyone.
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)
    # The cloud auth provider decodes the session JWT through the kernel's
    # WEB_HOST service, normally exposed by importing backend.main. Publish it
    # here so this file does not depend on another test having done that.
    from backend.plugin_sdk_host import install_web_host

    install_web_host()
    import narranexus.platform.module_system  # noqa: F401

    dispose = KERNEL_REGISTRIES.registry_for("ingress.channels").register_contribution(
        Contribution(CHANNEL, lambda: DESC), owner="acme.whmw"
    )
    app = FastAPI()
    app.middleware("http")(auth_middleware)  # the REAL one, not a stand-in
    app.include_router(generic_mod.router, prefix="/api/channels")
    yield TestClient(app)
    dispose.dispose()
    credential_codec.use_key_dir(None)


@pytest.fixture
def fresh_limiters(monkeypatch):
    """Both limiters are in-process globals shared across tests; give each test
    its own so a neighbour's traffic never decides this one's verdict."""
    monkeypatch.setattr(generic_mod, "_webhook_limiter", SlidingWindowRateLimiter(limit=120, window_sec=60.0))
    monkeypatch.setattr(generic_mod, "_webhook_ip_limiter", SlidingWindowRateLimiter(limit=600, window_sec=60.0))


def _bind(c: TestClient, agent_id: str = "a1") -> str:
    r = c.post(f"/api/channels/{CHANNEL}/bind", json={"agent_id": agent_id, "fields": {"api_token": "t"}}, headers=JWT)
    body = r.json()
    assert body["success"], body
    return body["data"]["webhook_secret"]


def test_webhook_is_the_only_session_less_path_on_the_channel_api(cloud_client, fresh_limiters):
    c = cloud_client
    secret = _bind(c)

    # 1. The exemption: an UNAUTHENTICATED POST with a valid token reaches the handler.
    r = c.post(f"/api/channels/{CHANNEL}/webhook/a1", json={"id": 1}, headers={"X-Webhook-Token": secret})
    assert r.status_code == 200 and r.json() == {"success": True, "queued": True}

    # 2. Its siblings on the same app are NOT exempt.
    assert c.post(f"/api/channels/{CHANNEL}/bind", json={"agent_id": "a1", "fields": {}}).status_code == 401
    assert c.get(f"/api/channels/{CHANNEL}/credential", params={"agent_id": "a1"}).status_code == 401

    # 3. The exemption is the EXACT shape only — one more segment and auth is back.
    assert c.post(f"/api/channels/{CHANNEL}/webhook/a1/extra", json={}).status_code == 401


def test_webhook_secret_negatives_all_answer_401(cloud_client, fresh_limiters):
    c = cloud_client
    secret = _bind(c)
    body = b'{"id": 1}'
    good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    # A signature for the wrong body, a bogus digest, and a bare (unprefixed)
    # hex string are each refused — the `sha256=` prefix is not decoration.
    for headers in (
        {"X-Webhook-Signature": "sha256=deadbeef"},
        {"X-Webhook-Signature": good},  # right digest, no `sha256=` prefix
        {"X-Webhook-Signature": f"sha256={good}", "Content-Type": "application/json"},
        {"X-Webhook-Token": "abc"},
    ):
        r = c.post(f"/api/channels/{CHANNEL}/webhook/a1", content=b'{"id": 2}', headers=headers)
        assert r.status_code == 401, headers

    # Control: the same request with the correct signature is accepted, so the
    # 401s above are the signature check and not a broken fixture.
    ok = c.post(
        f"/api/channels/{CHANNEL}/webhook/a1",
        content=body,
        headers={"X-Webhook-Signature": f"sha256={good}", "Content-Type": "application/json"},
    )
    assert ok.status_code == 200


def test_unsafe_agent_id_never_reaches_the_handler(cloud_client, fresh_limiters):
    c = cloud_client
    _bind(c)
    # Path traversal / dotted ids are exempt by SHAPE but rejected by the
    # route's own pattern, so an unsafe id is a 422, never a lookup.
    assert c.post(f"/api/channels/{CHANNEL}/webhook/a..1", json={}).status_code == 422
    assert c.post(f"/api/channels/{CHANNEL}/webhook/a$1", json={}).status_code == 422


def test_per_binding_limiter_429s_before_the_db_is_read(cloud_client, monkeypatch):
    monkeypatch.setattr(generic_mod, "_webhook_limiter", SlidingWindowRateLimiter(limit=1, window_sec=60.0))
    monkeypatch.setattr(generic_mod, "_webhook_ip_limiter", SlidingWindowRateLimiter(limit=600, window_sec=60.0))
    c = cloud_client
    secret = _bind(c)
    assert c.post(f"/api/channels/{CHANNEL}/webhook/a1", json={"id": 1}, headers={"X-Webhook-Token": secret}).status_code == 200
    assert c.post(f"/api/channels/{CHANNEL}/webhook/a1", json={"id": 2}, headers={"X-Webhook-Token": secret}).status_code == 429


def test_ip_limiter_buckets_by_forwarded_client_not_by_socket_peer(cloud_client, monkeypatch):
    """The IP limiter must key on the proxy-hop client, not ``request.client``.

    Uvicorn runs without --proxy-headers behind the deploy stack's nginx, so
    the socket peer is one address for EVERY cloud request: keyed on it, a
    single abuser's 600/min 429s every agent's inbound webhooks at once.
    Two different forwarded clients must therefore get independent buckets.
    """
    monkeypatch.setattr(generic_mod, "_webhook_limiter", SlidingWindowRateLimiter(limit=120, window_sec=60.0))
    monkeypatch.setattr(generic_mod, "_webhook_ip_limiter", SlidingWindowRateLimiter(limit=1, window_sec=60.0))
    c = cloud_client
    secret = _bind(c)
    tok = {"X-Webhook-Token": secret}
    # Same socket peer throughout (TestClient); only the forwarded chain differs.
    # _TRUSTED_PROXY_HOPS=2 → the caller is the 2nd entry from the right.
    first = {**tok, "X-Forwarded-For": "1.1.1.1, edge"}
    second = {**tok, "X-Forwarded-For": "2.2.2.2, edge"}
    assert c.post(f"/api/channels/{CHANNEL}/webhook/a1", json={"id": 1}, headers=first).status_code == 200
    # Independent bucket: a different caller is not spending the first one's budget.
    assert c.post(f"/api/channels/{CHANNEL}/webhook/a1", json={"id": 2}, headers=second).status_code == 200
    # Same bucket as the first request → over the limit.
    assert c.post(f"/api/channels/{CHANNEL}/webhook/a1", json={"id": 3}, headers=first).status_code == 429
