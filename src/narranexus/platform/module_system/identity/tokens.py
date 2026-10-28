"""
@file_name: tokens.py
@author:
@date: 2026-08-10
@description: Ed25519 identity tokens — the cryptographic core of MCP caller auth.

Blueprint Q1/Q2 (MCP security architecture v2): callers of the shared module
MCP servers carry a short-lived EdDSA JWT. The PRIVATE key lives only with the
issuer — cloud: the executor broker (deploy repo, signs at ensure() time);
local: this very process via LocalEphemeralIssuer — while verifiers (the
module MCP servers, and backend's service-trust path) hold only the PUBLIC
key. Asymmetric on purpose: a compromised verifier container cannot mint
identities.

The token binds ``sub=user_id`` + ``exp`` ONLY. It deliberately does NOT name
an agent_id: agent ownership is resolved per tool call (OwnerScopedPolicy in
identity/mcp_auth.py), so one token covers all of a user's agents, and the
cloud issuer — the broker, which knows nothing but user_id — can sign it.

TTL: a token is minted per run at dispatch time and never refreshed mid-run,
while a run itself is unbounded (iron rule #14). The default TTL must
therefore outlive the longest plausible run; 72h is the starting value and
the audit-phase logs decide the final one. A stolen token is only ever worth
its OWN user's identity (cross-user theft requires compromising app/mcp,
which is game over regardless), so the long replay window buys no attacker
anything the executor didn't already have. NOTE the Q6 widening, though:
since backend/auth.py's nx-agent service path trusts the same token, it is
not just an MCP-plane credential — it acts as a user-level backend API
credential (role "user", no elevation) for its full TTL, with no revocation
list (Q2: revocation = executor teardown + expiry). Whoever finalizes the
TTL after the audit window must weigh BOTH planes.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from loguru import logger

ISSUER_BROKER = "narranexus-broker"
ISSUER_LOCAL = "narranexus-local"

# Ed25519 only. NEVER widen this to a list that includes HS* — accepting an
# HMAC algorithm alongside an asymmetric one is the classic algorithm-confusion
# hole (the public key doubles as the HMAC secret and anyone can forge).
_ALGORITHM = "EdDSA"

# Marks OUR tokens: a structurally valid JWT signed by the right key but
# minted for some other purpose must never be accepted as a caller identity.
_KIND_CLAIM = "nx"
_KIND_IDENTITY = "identity"

DEFAULT_TTL_SECONDS = 72 * 3600
TTL_ENV = "NX_IDENTITY_TOKEN_TTL_SECONDS"

# Verifier-side public key location. The env points at the mounted file in
# cloud (deploy repo mounts it into mcp + backend); the default is where
# LocalEphemeralIssuer publishes it for the local two-process (backend + mcp)
# topology.
PUBLIC_KEY_FILE_ENV = "NX_IDENTITY_PUBLIC_KEY_FILE"
KEY_DIR_ENV = "NX_IDENTITY_KEY_DIR"
_PUBLIC_KEY_FILENAME = "identity_ed25519.pub"


class IdentityTokenError(Exception):
    """The token is not a valid NarraNexus identity (bad signature / expired /
    wrong shape). One exception type on purpose: callers gate on valid-or-not;
    the reason is for logs, never for flow control."""


@dataclass(frozen=True)
class VerifiedIdentity:
    """The proven caller: the one fact downstream policy may trust."""

    user_id: str
    issuer: str
    expires_at: int


def token_ttl_seconds() -> int:
    raw = os.environ.get(TTL_ENV, "").strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"[identity] bad {TTL_ENV}={raw!r}; using default TTL")
        return DEFAULT_TTL_SECONDS


def sign_identity_token(
    user_id: str,
    private_key_pem: Union[str, bytes],
    *,
    issuer: str,
    ttl_seconds: Optional[int] = None,
) -> str:
    """Mint one identity token. Callers hold the private key; see module doc
    for who those callers are (broker in cloud, LocalEphemeralIssuer locally)."""
    import jwt

    now = int(time.time())
    ttl = token_ttl_seconds() if ttl_seconds is None else ttl_seconds
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + ttl,
        "iss": issuer,
        _KIND_CLAIM: _KIND_IDENTITY,
    }
    return jwt.encode(payload, private_key_pem, algorithm=_ALGORITHM)


def verify_identity_token(
    token: str, public_key_pem: Union[str, bytes]
) -> VerifiedIdentity:
    """Verify signature + expiry + shape; raise IdentityTokenError otherwise."""
    import jwt

    try:
        payload = jwt.decode(
            token,
            public_key_pem,
            algorithms=[_ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError as e:
        raise IdentityTokenError(
            f"invalid identity token: {type(e).__name__}: {e}"
        ) from e
    if payload.get(_KIND_CLAIM) != _KIND_IDENTITY:
        raise IdentityTokenError("not a NarraNexus identity token (nx claim)")
    return VerifiedIdentity(
        user_id=str(payload["sub"]),
        issuer=str(payload.get("iss", "")),
        expires_at=int(payload["exp"]),
    )


# ---------------------------------------------------------------------------
# Verifier-side public key loading
# ---------------------------------------------------------------------------


def identity_key_dir() -> Path:
    raw = os.environ.get(KEY_DIR_ENV, "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".narranexus" / "identity"


def public_key_path() -> Path:
    raw = os.environ.get(PUBLIC_KEY_FILE_ENV, "").strip()
    if raw:
        return Path(raw)
    return identity_key_dir() / _PUBLIC_KEY_FILENAME


# (path, mtime, pem) of the last successful read. mtime-keyed so a locally
# re-generated key (new agent-runtime process re-publishing) is picked up
# without restarting the mcp process, while steady state costs one stat().
_pubkey_cache: tuple[str, float, bytes] | None = None


def load_public_key_pem() -> Optional[bytes]:
    """The identity public key PEM, or None when not provisioned.

    None is a legitimate deployment state (key not generated/mounted yet) —
    verifiers must degrade per their own policy (mcp_auth fails open with a
    loud warning; backend's service path fails closed), so this loader never
    raises.
    """
    global _pubkey_cache
    path = public_key_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _pubkey_cache = None
        return None
    cached = _pubkey_cache
    if cached is not None and cached[0] == str(path) and cached[1] == mtime:
        return cached[2]
    try:
        pem = path.read_bytes()
    except OSError as e:
        logger.debug(f"[identity] cannot read public key {path}: {e}")
        _pubkey_cache = None
        return None
    _pubkey_cache = (str(path), mtime, pem)
    return pem


# ---------------------------------------------------------------------------
# Local issuer (blueprint Q1: local mode signs in the agent-runtime process)
# ---------------------------------------------------------------------------


class LocalEphemeralIssuer:
    """Per-process Ed25519 issuer for local mode.

    Generates an in-memory keypair on first use and publishes ONLY the public
    key (atomically: tmp + os.replace) to the shared identity dir, so the
    separate local mcp process can verify. The private key never touches disk
    — a fresh process means a fresh key and a fresh publication, which is the
    ephemerality the blueprint asked for.

    Instantiating this class does nothing to the filesystem; keygen happens on
    the first ``token_for`` call. The caller (step_3) only invokes it when
    NX_MCP_AUTH_MODE != off, which is what keeps a default local run
    byte-identical (iron rule #7).
    """

    def __init__(self) -> None:
        self._private_pem: Optional[bytes] = None
        # user_id -> (token, expires_at). Re-sign when 3/4 of the TTL is gone
        # so a cached token handed to a new run still has plenty of life.
        self._tokens: dict[str, tuple[str, int]] = {}

    def _ensure_keypair(self) -> bytes:
        if self._private_pem is not None:
            return self._private_pem
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        priv = ed25519.Ed25519PrivateKey.generate()
        self._private_pem = priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        pub_pem = priv.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        key_dir = identity_key_dir()
        key_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = key_dir / _PUBLIC_KEY_FILENAME
        tmp = target.with_suffix(".pub.tmp")
        tmp.write_bytes(pub_pem)
        os.replace(tmp, target)
        logger.info(f"[identity] local issuer published public key to {target}")
        return self._private_pem

    def token_for(self, user_id: str) -> str:
        now = int(time.time())
        cached = self._tokens.get(user_id)
        if cached is not None:
            token, expires_at = cached
            if expires_at - now > token_ttl_seconds() // 4:
                return token
        private_pem = self._ensure_keypair()
        token = sign_identity_token(user_id, private_pem, issuer=ISSUER_LOCAL)
        self._tokens[user_id] = (token, now + token_ttl_seconds())
        return token


_local_issuer: LocalEphemeralIssuer | None = None


def get_local_issuer() -> LocalEphemeralIssuer:
    """Process-wide issuer singleton (one keypair, one publication)."""
    global _local_issuer
    if _local_issuer is None:
        _local_issuer = LocalEphemeralIssuer()
    return _local_issuer


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "ISSUER_BROKER",
    "ISSUER_LOCAL",
    "IdentityTokenError",
    "KEY_DIR_ENV",
    "LocalEphemeralIssuer",
    "PUBLIC_KEY_FILE_ENV",
    "TTL_ENV",
    "VerifiedIdentity",
    "get_local_issuer",
    "identity_key_dir",
    "load_public_key_pem",
    "public_key_path",
    "sign_identity_token",
    "token_ttl_seconds",
    "verify_identity_token",
]
