"""
@file_name: test_channel_webhook_route.py
@author: Bin Liang
@date: 2026-09-04
@description: POST /api/channels/{channel}/webhook/{agent_id}: bind issues the secret once, a valid token/HMAC queues the event, wrong token 401, unbound agent 404, non-webhook channel 404; the path is auth-exempt by exact shape only.
"""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
from backend.auth import _is_channel_webhook_path
from backend.routes.channels import generic as generic_mod
from narranexus.contracts.channel import ChannelDescriptor, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.channel import credential_codec
from narranexus.platform.channel.webhook_inbox import WebhookInbox

DESC = ChannelDescriptor(name="wh_route", display_name="WH", transport="webhook", credential_schema=CredentialSchema(fields=(CredentialField("api_token", "secret"),), supports_test=False), has_test=False)
H = {"X-User-Id": "u1"}


@pytest.fixture
def client(db_client, monkeypatch, tmp_path: Path):
    credential_codec.use_key_dir(tmp_path / "keys")

    async def _db():
        return db_client

    monkeypatch.setattr(own, "get_db_client", _db)
    monkeypatch.setattr(generic_mod, "_db", _db)

    async def _resolve(self, agent_id):
        return "u1"

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)
    import narranexus.platform.module_system  # noqa: F401

    dispose = KERNEL_REGISTRIES.registry_for("ingress.channels").register_contribution(Contribution("wh_route", lambda: DESC), owner="acme.wh")
    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id")
        return await call_next(request)

    app.include_router(generic_mod.router, prefix="/api/channels")
    yield TestClient(app), db_client
    dispose.dispose()
    credential_codec.use_key_dir(None)


def test_auth_exemption_is_exact():
    assert _is_channel_webhook_path("/api/channels/wh_route/webhook/a1")
    assert not _is_channel_webhook_path("/api/channels/wh_route/bind")
    assert not _is_channel_webhook_path("/api/channels/wh_route/webhook/a1/extra")


def test_webhook_flow(client):
    c, db = client
    bind = c.post("/api/channels/wh_route/bind", json={"agent_id": "a1", "fields": {"api_token": "t"}}, headers=H).json()
    assert bind["success"] and bind["data"]["webhook_path"] == "/api/channels/wh_route/webhook/a1"
    secret = bind["data"]["webhook_secret"]
    assert secret and "webhook_secret" not in c.get("/api/channels/wh_route/credential", params={"agent_id": "a1"}, headers=H).json()["data"]
    # a re-bind keeps the same secret and does not show it again
    again = c.post("/api/channels/wh_route/bind", json={"agent_id": "a1", "fields": {"api_token": "t2"}}, headers=H).json()
    assert "webhook_secret" not in again["data"]
    # no session, wrong token → 401; right token → queued
    assert c.post("/api/channels/wh_route/webhook/a1", json={"id": 1}, headers={"X-Webhook-Token": "nope"}).status_code == 401
    r = c.post("/api/channels/wh_route/webhook/a1", json={"id": 1, "text": "hi"}, headers={"X-Webhook-Token": secret})
    assert r.status_code == 200 and r.json() == {"success": True, "queued": True}
    body = b'{"id": 2}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert c.post("/api/channels/wh_route/webhook/a1", content=body, headers={"X-Webhook-Signature": f"sha256={sig}", "Content-Type": "application/json"}).status_code == 200
    assert c.post("/api/channels/wh_route/webhook/a1", content=b"not json", headers={"X-Webhook-Token": secret}).status_code == 400
    # an unknown binding answers the same 401 as a bad secret: no agent-enumeration oracle
    assert c.post("/api/channels/wh_route/webhook/a9", json={}, headers={"X-Webhook-Token": secret}).status_code == 401
    # the secret is never accepted from the query string (proxies log the request line)
    assert c.post(f"/api/channels/wh_route/webhook/a1?token={secret}", json={"id": 3}).status_code == 401
    assert c.post("/api/channels/lark/webhook/a1", json={}, headers={"X-Webhook-Token": secret}).status_code == 404  # socket channel

    import asyncio

    events = asyncio.run(WebhookInbox(db).pull("wh_route", "a1"))
    assert [e.payload["id"] for e in events] == [1, 2]
