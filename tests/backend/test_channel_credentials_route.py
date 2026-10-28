"""
@file_name: test_channel_credentials_route.py
@author:
@date: 2026-08-10
@description: Route-level proof for the raw-channel-credential endpoint
(blueprint P2, #2 PR-A). This is the ONLY endpoint that returns a raw channel
secret, so its two gates BOTH matter and are pinned here:

  1. service-caller gate — only an nx-agent (nx-service) bearer may reach it;
     a plain owner session must NOT be able to pull the raw token back through
     the API (the panel masks it on purpose).
  2. owner gate — the proven identity must own the agent (cross-tenant theft).

The seam's own Direct↔Http parity lives in tests/module/test_channel_store.py;
this file pins the HTTP twin's auth behaviour a TestClient can see.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes._ownership as own
from backend.routes.agents.channel_credentials import router as cc_router
from narranexus.platform.module_system.data_access.channel_store import DirectStore as ChannelDirectStore

_SERVICE_BEARER = "Bearer nx-agent:agent_mine~~~~u1~~~~sometoken"
# The raw dict the seam's DirectStore.get_credential would return for a bound
# discord agent (DiscordCredential.to_raw_dict — includes the secret).
_RAW = {
    "agent_id": "agent_mine",
    "bot_token": "RAW-BOT-TOKEN-secret",
    "bot_user_id": "bot123",
    "owner_user_id": "owner9",
}


@pytest.fixture
def client(monkeypatch):
    async def _db():
        return object()

    monkeypatch.setattr(own, "get_db_client", _db)

    async def _resolve(self, agent_id):
        # agent_mine -> u1 owns it; agent_theirs -> u2; anything else missing
        return {"agent_mine": "u1", "agent_theirs": "u2"}.get(agent_id, "")

    monkeypatch.setattr(own.AgentRepository, "resolve_owner", _resolve)

    # The route delegates db access to the seam's DirectStore — mock it there
    # (this test pins the route's AUTH gating, not the db path, which
    # test_channel_store.py's parity suite already covers).
    async def _get_cred(self, channel, agent_id):
        return dict(_RAW) if (channel == "discord" and agent_id == "agent_mine") else None

    async def _get_name(self, agent_id):
        return "Scout"

    async def _get_owner(self, agent_id):
        return "u1"

    async def _patch(self, channel, agent_id, patch):
        return {"success": True, "op": "patch"}

    async def _put(self, channel, agent_id, raw):
        return {"success": True, "op": "put"}

    async def _delete(self, channel, agent_id):
        return {"success": True, "data": {"deleted": True}}

    monkeypatch.setattr(ChannelDirectStore, "get_credential", _get_cred)
    monkeypatch.setattr(ChannelDirectStore, "get_agent_name", _get_name)
    monkeypatch.setattr(ChannelDirectStore, "get_agent_owner", _get_owner)
    monkeypatch.setattr(ChannelDirectStore, "patch_credential", _patch)
    monkeypatch.setattr(ChannelDirectStore, "put_credential", _put)
    monkeypatch.setattr(ChannelDirectStore, "delete_credential", _delete)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        # Stand in for auth_middleware: the real one sets user_id AND, ONLY when
        # it actually VERIFIED the broker-signed nx-agent bearer, the
        # nx_service_authed flag. The flag is DECOUPLED from the raw prefix here
        # (via x-test-unverify) precisely so a test can prove the endpoint gates
        # on the verified flag, not the prefix: an nx-agent bearer that the
        # middleware did NOT verify (local mode / forgery) leaves the flag False.
        request.state.user_id = request.headers.get("x-test-user") or None
        auth = request.headers.get("authorization") or ""
        request.state.nx_service_authed = (
            auth.startswith("Bearer nx-agent:") and not request.headers.get("x-test-unverify")
        )
        return await call_next(request)

    app.include_router(cc_router, prefix="/api/agents")
    return TestClient(app)


def _url(agent="agent_mine", channel="discord"):
    return f"/api/agents/{agent}/channels/{channel}/credential"


def test_service_owner_gets_raw_credential(client):
    r = client.get(_url(), headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1"})
    assert r.status_code == 200
    body = r.json()
    assert body["bot_token"] == "RAW-BOT-TOKEN-secret"  # the raw secret is served
    assert body["agent_id"] == "agent_mine"


def test_non_service_caller_is_forbidden_even_as_owner(client):
    # A logged-in USER session (no nx-agent bearer) owns the agent, yet must
    # NOT get the raw token — the security fix this endpoint exists to enforce.
    r = client.get(_url(), headers={"authorization": "Bearer user-jwt-abc", "x-test-user": "u1"})
    assert r.status_code == 403
    assert "nx-service" in r.json()["detail"]


def test_no_bearer_is_forbidden(client):
    r = client.get(_url(), headers={"x-test-user": "u1"})
    assert r.status_code == 403


def test_gate_reads_the_verified_flag_not_the_header_prefix(client):
    # The whole point of the security fix: an nx-agent bearer whose signature the
    # middleware did NOT verify (local mode trusts X-User-Id and never enters the
    # nx-service branch; or an outright forgery) must be 403 even though the raw
    # Authorization prefix "looks like" a service caller. This is the ONE test
    # that would FAIL against the reverted _is_nx_service_bearer(header) gate
    # (which passes on the prefix alone) — it locks in "the gate reads
    # request.state.nx_service_authed", not "there is some gate".
    r = client.get(
        _url(),
        headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1", "x-test-unverify": "1"},
    )
    assert r.status_code == 403


def test_service_non_owner_is_forbidden(client):
    r = client.get(
        _url(agent="agent_theirs"),
        headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1"},
    )
    assert r.status_code == 403


def test_unknown_agent_is_not_found(client):
    r = client.get(
        _url(agent="agent_ghost"),
        headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1"},
    )
    assert r.status_code == 404


def test_unknown_channel_is_not_found_before_any_lookup(client):
    # "signal" is not in SUPPORTED_CHANNELS — 404 before any owner check or db.
    r = client.get(
        _url(channel="signal"),
        headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1"},
    )
    assert r.status_code == 404
    assert "unknown channel" in r.json()["detail"]


def test_unbound_agent_returns_bound_false(client, monkeypatch):
    async def _none(self, channel, agent_id):
        return None

    monkeypatch.setattr(ChannelDirectStore, "get_credential", _none)
    r = client.get(_url(), headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1"})
    assert r.status_code == 200
    assert r.json() == {"bound": False}


# ---------------------------------------------------------------------------
# /channels/name and /channels/owner carry the SAME double gate — pin them too
# (pre-review: previously only /credential was TestClient-covered).
# ---------------------------------------------------------------------------


def _name_url(agent="agent_mine"):
    return f"/api/agents/{agent}/channels/name"


def _owner_url(agent="agent_mine"):
    return f"/api/agents/{agent}/channels/owner"


def test_name_service_owner_ok(client):
    r = client.get(_name_url(), headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1"})
    assert r.status_code == 200
    assert r.json()["agent_name"] == "Scout"


def test_name_non_service_caller_is_forbidden(client):
    r = client.get(_name_url(), headers={"authorization": "Bearer user-jwt-abc", "x-test-user": "u1"})
    assert r.status_code == 403


def test_name_service_non_owner_is_forbidden(client):
    r = client.get(_name_url(agent="agent_theirs"), headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1"})
    assert r.status_code == 403


def test_owner_service_owner_ok(client):
    r = client.get(_owner_url(), headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1"})
    assert r.status_code == 200
    assert r.json()["owner_user_id"] == "u1"


def test_owner_non_service_caller_is_forbidden(client):
    r = client.get(_owner_url(), headers={"authorization": "Bearer user-jwt-abc", "x-test-user": "u1"})
    assert r.status_code == 403


def test_owner_service_non_owner_is_forbidden(client):
    r = client.get(_owner_url(agent="agent_theirs"), headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# credential-mutation primitives (PATCH/PUT/DELETE) — same double gate as GET
# ---------------------------------------------------------------------------


def test_patch_service_owner_ok(client):
    r = client.patch(_url(), json={"permission_state": {"k": "v"}},
                     headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1"})
    assert r.status_code == 200 and r.json()["op"] == "patch"


def test_put_service_owner_ok(client):
    r = client.put(_url(), json={"app_id": "x"},
                   headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1"})
    assert r.status_code == 200 and r.json()["op"] == "put"


def test_delete_service_owner_ok(client):
    r = client.delete(_url(), headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1"})
    assert r.status_code == 200 and r.json()["data"]["deleted"] is True


def test_write_primitives_reject_non_service_caller(client):
    for m, kw in [("patch", {"json": {"a": 1}}), ("put", {"json": {"a": 1}}), ("delete", {})]:
        r = getattr(client, m)(_url(), headers={"authorization": "Bearer user-jwt", "x-test-user": "u1"}, **kw)
        assert r.status_code == 403, m


def test_write_primitives_reject_non_owner(client):
    for m, kw in [("patch", {"json": {"a": 1}}), ("put", {"json": {"a": 1}}), ("delete", {})]:
        r = getattr(client, m)(_url(agent="agent_theirs"),
                               headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1"}, **kw)
        assert r.status_code == 403, m


def test_write_primitives_unknown_channel_404(client):
    r = client.patch(_url(channel="signal"), json={"a": 1},
                     headers={"authorization": _SERVICE_BEARER, "x-test-user": "u1"})
    assert r.status_code == 404
