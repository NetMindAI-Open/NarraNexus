"""
@file_name: __init__.py
@author:
@date: 2026-08-10
@description: Caller identity AUTH for module MCP servers (blueprint P1).

Two halves, split by question:
- ``tokens``   — "is this identity cryptographically proven?" (Ed25519 JWT
                 sign/verify, key material handling, the local issuer)
- ``mcp_auth`` — "what do we do about it?" (NX_MCP_AUTH_MODE gating, the ASGI
                 middleware on every module MCP server, OwnerScopedPolicy)

The sibling ``module/_mcp_identity.py`` stays the fail-open "who does the
caller SAY they are" channel; this package adds the proof on top of the same
header contract.
"""
from narranexus.platform.module_system.identity.tokens import (
    ISSUER_BROKER,
    ISSUER_LOCAL,
    IdentityTokenError,
    LocalEphemeralIssuer,
    VerifiedIdentity,
    get_local_issuer,
    load_public_key_pem,
    sign_identity_token,
    verify_identity_token,
)

__all__ = [
    "ISSUER_BROKER",
    "ISSUER_LOCAL",
    "IdentityTokenError",
    "LocalEphemeralIssuer",
    "VerifiedIdentity",
    "get_local_issuer",
    "load_public_key_pem",
    "sign_identity_token",
    "verify_identity_token",
]
