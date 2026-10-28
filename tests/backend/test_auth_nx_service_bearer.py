"""
@file_name: test_auth_nx_service_bearer.py
@author:
@date: 2026-08-10
@description: The nx-agent service-identity path in auth_middleware
(blueprint Q6).

The mcp container's HttpStore forwards the executor→mcp identity headers
verbatim; the Authorization bearer is the nx-agent positional record whose
identity_token field (last in BEARER_FIELDS) is a broker/local-signed Ed25519 JWT. The middleware must verify it
with the identity PUBLIC key and trust its sub as the effective user — and,
unlike the mcp middleware, NEVER fail open: an nx-agent bearer reaching
backend is always a service call and must prove itself (no key provisioned =
still 401).
"""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend import auth as auth_mod
from backend.auth import auth_middleware, create_token
from narranexus.platform.module_system._mcp_identity import agent_id_headers
from narranexus.platform.module_system.identity.tokens import (
    ISSUER_BROKER,
    sign_identity_token,
)

AGENT = "agent_39b2b72b823b"


def _keypair() -> tuple[bytes, bytes]:
    priv = ed25519.Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


def _provision(tmp_path, monkeypatch) -> bytes:
    priv_pem, pub_pem = _keypair()
    pub_file = tmp_path / "identity_ed25519.pub"
    pub_file.write_bytes(pub_pem)
    monkeypatch.setenv("NX_IDENTITY_PUBLIC_KEY_FILE", str(pub_file))
    return priv_pem


def _build_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(auth_middleware)

    @app.get("/api/agents/{agent_id}/awareness")
    async def get_awareness(agent_id: str, request: Request):
        return {
            "user_id": getattr(request.state, "user_id", None),
            "nx_service_authed": getattr(request.state, "nx_service_authed", False),
        }

    @app.put("/api/agents/{agent_id}/awareness")
    async def put_awareness(agent_id: str, request: Request):
        return {"user_id": getattr(request.state, "user_id", None)}

    return app


@pytest.fixture
def cloud_client(monkeypatch):
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)
    return TestClient(_build_app())


def _service_headers(priv_pem: bytes, user_id: str = "usr_1", declared: str | None = None) -> dict:
    token = sign_identity_token(user_id, priv_pem, issuer=ISSUER_BROKER)
    return agent_id_headers(
        AGENT, user_id=declared if declared is not None else user_id,
        identity_token=token,
    )


def test_valid_service_bearer_sets_identity(tmp_path, monkeypatch, cloud_client):
    priv = _provision(tmp_path, monkeypatch)
    r = cloud_client.get(
        f"/api/agents/{AGENT}/awareness", headers=_service_headers(priv)
    )
    assert r.status_code == 200
    assert r.json() == {"user_id": "usr_1", "nx_service_authed": True}


def test_write_route_works_and_skips_quota_resolver(tmp_path, monkeypatch, cloud_client):
    # Data-plane service calls are repository operations, not LLM spends —
    # the provider/quota resolver must not 402 an out-of-quota user's
    # awareness update. The resolver would blow up on our fake app anyway;
    # reaching the handler at all proves the early return.
    priv = _provision(tmp_path, monkeypatch)
    r = cloud_client.put(
        f"/api/agents/{AGENT}/awareness",
        headers=_service_headers(priv),
        json={"awareness": "x"},
    )
    assert r.status_code == 200
    assert r.json()["user_id"] == "usr_1"


def test_forged_token_is_401(tmp_path, monkeypatch, cloud_client):
    _provision(tmp_path, monkeypatch)
    other_priv, _ = _keypair()
    r = cloud_client.get(
        f"/api/agents/{AGENT}/awareness", headers=_service_headers(other_priv)
    )
    assert r.status_code == 401


def test_nx_bearer_without_token_is_401(tmp_path, monkeypatch, cloud_client):
    # No fail-open on backend: an nx-agent bearer is always a service call.
    _provision(tmp_path, monkeypatch)
    r = cloud_client.get(
        f"/api/agents/{AGENT}/awareness", headers=agent_id_headers(AGENT, user_id="usr_1")
    )
    assert r.status_code == 401


def test_no_public_key_is_401_not_open(tmp_path, monkeypatch, cloud_client):
    priv, _ = _keypair()
    monkeypatch.setenv("NX_IDENTITY_PUBLIC_KEY_FILE", str(tmp_path / "missing.pub"))
    r = cloud_client.get(
        f"/api/agents/{AGENT}/awareness", headers=_service_headers(priv)
    )
    assert r.status_code == 401


def test_declared_user_mismatch_is_401(tmp_path, monkeypatch, cloud_client):
    priv = _provision(tmp_path, monkeypatch)
    r = cloud_client.get(
        f"/api/agents/{AGENT}/awareness",
        headers=_service_headers(priv, user_id="usr_real", declared="usr_other"),
    )
    assert r.status_code == 401


def test_normal_user_jwt_path_unchanged(tmp_path, monkeypatch, cloud_client):
    _provision(tmp_path, monkeypatch)
    token = create_token("usr_jwt", "user")
    r = cloud_client.get(
        f"/api/agents/{AGENT}/awareness",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["user_id"] == "usr_jwt"
    assert r.json()["nx_service_authed"] is False


# --- 2026-10-28: a sandbox token dies with the account ------------------------

def test_service_bearer_of_a_banned_user_is_refused(tmp_path, monkeypatch, cloud_client):
    """The service branch used to return BEFORE the account-state gate, so a
    banned user's executor token kept working for its whole TTL. Neuter the
    gate in the service branch and this goes green for the banned user."""
    priv = _provision(tmp_path, monkeypatch)

    async def _banned(_uid):
        return "banned"
    monkeypatch.setattr(auth_mod, "_account_state", _banned)
    r = cloud_client.get(f"/api/agents/{AGENT}/awareness", headers=_service_headers(priv))
    assert r.status_code == 403, r.text
    assert r.json().get("code") == "account_suspended"

    async def _active(_uid):
        return "active"
    monkeypatch.setattr(auth_mod, "_account_state", _active)
    r = cloud_client.get(f"/api/agents/{AGENT}/awareness", headers=_service_headers(priv))
    assert r.status_code == 200
