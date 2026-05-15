"""
@file_name: test_url_signer.py
@description: HMAC-signed URL token mint/verify roundtrip + tamper / expiry guards
"""
from __future__ import annotations

import time

import pytest

from xyz_agent_context.agent_framework.transcription import url_signer
from xyz_agent_context.agent_framework.transcription.url_signer import (
    TokenExpired,
    TokenInvalid,
    mint,
    public_url_for,
    verify,
)


@pytest.fixture
def secret(monkeypatch):
    """Force a deterministic signing secret for every test in this file."""
    monkeypatch.setattr(url_signer.settings, "transcription_hmac_secret", "test-secret-32B")
    monkeypatch.setattr(url_signer.settings, "admin_secret_key", "fallback-not-used")
    return "test-secret-32B"


def test_mint_then_verify_roundtrips(secret):
    token = mint(
        file_id="att_a1b2c3d4",
        agent_id="agent-1",
        user_id="user-1",
        variant="original",
    )
    claims = verify(token)
    assert claims.file_id == "att_a1b2c3d4"
    assert claims.agent_id == "agent-1"
    assert claims.user_id == "user-1"
    assert claims.variant == "original"
    assert claims.exp > int(time.time())


def test_verify_rejects_tampered_payload(secret):
    token = mint(
        file_id="att_a1b2c3d4", agent_id="a", user_id="u", variant="original",
    )
    payload_b64, digest_b64 = token.split(".", 1)
    # Replace the payload with a different one (same length to keep base64 valid)
    bad_payload = "X" * len(payload_b64)
    bad_token = f"{bad_payload}.{digest_b64}"
    with pytest.raises(TokenInvalid):
        verify(bad_token)


def test_verify_rejects_tampered_digest(secret):
    token = mint(
        file_id="att_a1b2c3d4", agent_id="a", user_id="u", variant="original",
    )
    payload_b64, digest_b64 = token.split(".", 1)
    bad_token = f"{payload_b64}.{'A' * len(digest_b64)}"
    with pytest.raises(TokenInvalid):
        verify(bad_token)


def test_verify_rejects_missing_separator(secret):
    with pytest.raises(TokenInvalid):
        verify("nosepratorhere")


def test_verify_rejects_bad_base64(secret):
    with pytest.raises(TokenInvalid):
        verify("not!valid!b64.also!not!b64")


def test_verify_rejects_unknown_variant(secret):
    """If we ever introduce a new variant in mint() we shouldn't be able
    to verify a token with an unrecognised value."""
    # Forge a token with variant="hostile" but a valid signature.
    import base64, hashlib, hmac, json
    payload = {
        "file_id": "att_a1b2c3d4",
        "agent_id": "a",
        "user_id": "u",
        "variant": "hostile",
        "exp": int(time.time()) + 60,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    digest = hmac.new(b"test-secret-32B", payload_bytes, hashlib.sha256).digest()
    pb = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    db = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    token = f"{pb}.{db}"
    with pytest.raises(TokenInvalid):
        verify(token)


def test_verify_expired_token_raises_expired(secret):
    token = mint(
        file_id="att_a1b2c3d4",
        agent_id="a",
        user_id="u",
        variant="mp3",
        ttl_seconds=-10,  # already expired
    )
    with pytest.raises(TokenExpired):
        verify(token)


def test_public_url_for_uses_settings_base(secret, monkeypatch):
    monkeypatch.setattr(url_signer.settings, "public_base_url", "https://my.host:8443/")
    token = mint(file_id="att_a1b2c3d4", agent_id="a", user_id="u", variant="original")
    url = public_url_for(token)
    assert url is not None
    assert url.startswith("https://my.host:8443/api/public/transcription/audio/")
    assert url.endswith(token)


def test_public_url_for_returns_none_when_unset(secret, monkeypatch):
    monkeypatch.setattr(url_signer.settings, "public_base_url", "")
    token = mint(file_id="att_a1b2c3d4", agent_id="a", user_id="u", variant="original")
    assert public_url_for(token) is None


def test_secret_falls_back_to_admin_key_in_local_mode(monkeypatch):
    monkeypatch.setattr(url_signer.settings, "transcription_hmac_secret", "")
    monkeypatch.setattr(url_signer.settings, "admin_secret_key", "admin-fallback")
    monkeypatch.setattr(
        "xyz_agent_context.utils.deployment_mode.is_cloud_mode",
        lambda: False,
    )
    token = mint(file_id="att_a1b2c3d4", agent_id="a", user_id="u", variant="mp3")
    claims = verify(token)
    assert claims.file_id == "att_a1b2c3d4"


def test_secret_refuses_derivation_in_cloud_mode(monkeypatch):
    monkeypatch.setattr(url_signer.settings, "transcription_hmac_secret", "")
    monkeypatch.setattr(url_signer.settings, "admin_secret_key", "admin-key")
    monkeypatch.setattr(
        "xyz_agent_context.utils.deployment_mode.is_cloud_mode",
        lambda: True,
    )
    with pytest.raises(RuntimeError, match="TRANSCRIPTION_HMAC_SECRET"):
        mint(file_id="att_a1b2c3d4", agent_id="a", user_id="u", variant="original")
