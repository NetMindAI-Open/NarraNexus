"""
@file_name: test_tokens.py
@author:
@date: 2026-08-10
@description: Tests for module/identity/tokens.py — Ed25519 identity tokens.

Covers the crypto contract (sign/verify round trip, forgery and algorithm
confusion rejected, expiry honored), the verifier's key-loading behaviour
(env-pointed file, mtime-based reload, missing file degrades to None), and
the local ephemeral issuer (in-memory private key, public key published to
the shared identity dir, per-user token caching).
"""
from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from narranexus.platform.module_system.identity.tokens import (
    ISSUER_LOCAL,
    IdentityTokenError,
    LocalEphemeralIssuer,
    VerifiedIdentity,
    load_public_key_pem,
    sign_identity_token,
    verify_identity_token,
)


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


# ---------------------------------------------------------------------------
# sign / verify
# ---------------------------------------------------------------------------


def test_round_trip():
    priv, pub = _keypair()
    tok = sign_identity_token("usr_1", priv, issuer=ISSUER_LOCAL)
    got = verify_identity_token(tok, pub)
    assert isinstance(got, VerifiedIdentity)
    assert got.user_id == "usr_1"
    assert got.issuer == ISSUER_LOCAL
    assert got.expires_at > int(time.time())


def test_token_is_bearer_field_safe():
    # Rides the nx-agent bearer as positional field 7 — must never contain
    # the field separator "~" (or whitespace).
    priv, _ = _keypair()
    tok = sign_identity_token("usr_1", priv, issuer=ISSUER_LOCAL)
    assert "~" not in tok
    assert " " not in tok


def test_wrong_key_rejected():
    priv, _ = _keypair()
    _, other_pub = _keypair()
    tok = sign_identity_token("usr_1", priv, issuer=ISSUER_LOCAL)
    with pytest.raises(IdentityTokenError):
        verify_identity_token(tok, other_pub)


def test_garbage_rejected():
    _, pub = _keypair()
    with pytest.raises(IdentityTokenError):
        verify_identity_token("not-a-jwt", pub)


def test_hs256_forgery_rejected():
    # Classic algorithm-confusion attack: an HS256 token HMAC'd with the
    # PUBLIC key as the secret. PyJWT refuses to *encode* such a token, but an
    # attacker doesn't use PyJWT — hand-roll the exact bytes and assert the
    # verifier dies on the pinned EdDSA algorithms list.
    import base64
    import hashlib
    import hmac
    import json

    def b64url(data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    _, pub = _keypair()
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64url(
        json.dumps(
            {"sub": "usr_1", "exp": int(time.time()) + 60, "nx": "identity"}
        ).encode()
    )
    signing_input = header + b"." + payload
    sig = b64url(hmac.new(pub, signing_input, hashlib.sha256).digest())
    forged = (signing_input + b"." + sig).decode()
    with pytest.raises(IdentityTokenError):
        verify_identity_token(forged, pub)


def test_expired_rejected():
    priv, pub = _keypair()
    tok = sign_identity_token("usr_1", priv, issuer=ISSUER_LOCAL, ttl_seconds=-10)
    with pytest.raises(IdentityTokenError):
        verify_identity_token(tok, pub)


def test_missing_sub_rejected():
    import jwt as pyjwt

    priv, pub = _keypair()
    tok = pyjwt.encode(
        {"exp": int(time.time()) + 60, "nx": "identity"}, priv, algorithm="EdDSA"
    )
    with pytest.raises(IdentityTokenError):
        verify_identity_token(tok, pub)


def test_non_identity_kind_rejected():
    # A structurally valid JWT signed by the right key but not marked as OUR
    # identity kind must not be accepted as one.
    import jwt as pyjwt

    priv, pub = _keypair()
    tok = pyjwt.encode(
        {"sub": "usr_1", "exp": int(time.time()) + 60}, priv, algorithm="EdDSA"
    )
    with pytest.raises(IdentityTokenError):
        verify_identity_token(tok, pub)


# ---------------------------------------------------------------------------
# public key loading
# ---------------------------------------------------------------------------


def test_pubkey_file_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("NX_IDENTITY_PUBLIC_KEY_FILE", str(tmp_path / "nope.pub"))
    assert load_public_key_pem() is None


def test_pubkey_file_loads_and_reloads_on_change(tmp_path, monkeypatch):
    _, pub1 = _keypair()
    _, pub2 = _keypair()
    key_file = tmp_path / "identity_ed25519.pub"
    key_file.write_bytes(pub1)
    monkeypatch.setenv("NX_IDENTITY_PUBLIC_KEY_FILE", str(key_file))

    assert load_public_key_pem() == pub1

    # mtime-based reload: rewrite with a different key and a bumped mtime.
    key_file.write_bytes(pub2)
    future = time.time() + 10
    import os

    os.utime(key_file, (future, future))
    assert load_public_key_pem() == pub2


# ---------------------------------------------------------------------------
# local ephemeral issuer
# ---------------------------------------------------------------------------


def test_local_issuer_publishes_pubkey_and_signs(tmp_path, monkeypatch):
    monkeypatch.setenv("NX_IDENTITY_KEY_DIR", str(tmp_path))
    issuer = LocalEphemeralIssuer()
    tok = issuer.token_for("usr_local")

    pub_file = tmp_path / "identity_ed25519.pub"
    assert pub_file.exists()

    got = verify_identity_token(tok, pub_file.read_bytes())
    assert got.user_id == "usr_local"
    assert got.issuer == ISSUER_LOCAL


def test_local_issuer_caches_per_user(tmp_path, monkeypatch):
    monkeypatch.setenv("NX_IDENTITY_KEY_DIR", str(tmp_path))
    issuer = LocalEphemeralIssuer()
    assert issuer.token_for("usr_a") == issuer.token_for("usr_a")
    assert issuer.token_for("usr_a") != issuer.token_for("usr_b")


def test_local_issuer_private_key_never_written(tmp_path, monkeypatch):
    monkeypatch.setenv("NX_IDENTITY_KEY_DIR", str(tmp_path))
    issuer = LocalEphemeralIssuer()
    issuer.token_for("usr_a")
    names = [p.name for p in tmp_path.iterdir()]
    assert names == ["identity_ed25519.pub"]
