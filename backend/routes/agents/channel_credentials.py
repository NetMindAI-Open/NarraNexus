"""
@file_name: channel_credentials.py
@author:
@date: 2026-08-10
@description: Backend HTTP twin of the ChannelCredentialStore seam (blueprint
P2, #2 "mcp zero db creds").

The credential endpoint is the ONLY route in the codebase that returns a raw
channel secret (each channel's own field — Discord ``bot_token``, Lark
``app_secret_encoded``, …), so all three endpoints here carry a DOUBLE gate:

  1. service gate — ``_require_service_caller`` requires
     ``request.state.nx_service_authed``, the flag ``auth_middleware`` sets ONLY
     after it VERIFIED the executor's broker-signed Ed25519 identity (fail-closed
     on forged/expired/mismatched). A plain user-session JWT, a local
     ``X-User-Id`` request, or any un-verified path is 403 — the panel masks
     credentials on purpose and the raw token must never leak back to a browser.
  2. owner gate — ``assert_owned`` on that proven identity (never a self-declared
     user id), so no cross-tenant read.

Endpoints (mounted under /api/agents by agents/core.py):
  GET /{agent_id}/channels/{channel}/credential  -> raw cred dict | {"bound": false}
  GET /{agent_id}/channels/name                  -> {"agent_name": str}
  GET /{agent_id}/channels/owner                 -> {"owner_user_id": str}

``channel`` is validated against ``SUPPORTED_CHANNELS`` (derived from the seam's
registry) BEFORE any lookup -> 404 otherwise, so this endpoint can never be used
to probe for channels that aren't wired.

Never logs the secret — the credential dataclasses' ``to_raw_dict()`` is the
only place the raw fields leave the manager, and this handler delegates to the
seam's DirectStore and passes the dict straight through, never touching fields.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request

# Delegate the actual db access to the seam's DirectStore so this HTTP twin and
# the in-process path are ONE implementation (same manager dispatch, same raw
# serialisation) — adding a channel is one registry line in channel_store.py,
# never an edit here. Backend importing the agent package is the allowed
# one-way hop (铁律 #21).
from narranexus.platform.module_system.data_access.channel_store import (
    SUPPORTED_CHANNELS,
    DirectStore as ChannelDirectStore,
)

# Ownership gate (backend/routes/_ownership.py): agent_id is attacker-
# controlled input — without the owner check, any nx-service caller could
# read ANY agent's channel secret (cross-tenant credential theft). Local mode
# (no JWT identity) does not enforce; see the helper's security-posture
# docstring before relying on it.
from backend.routes._ownership import assert_owned

router = APIRouter()


def _require_service_caller(request: Request) -> None:
    """Service-identity gate: this endpoint returns a RAW secret, so it is
    restricted to the executor→mcp service path. Gate on the flag
    ``auth_middleware`` sets ONLY after it actually VERIFIED the broker-signed
    Ed25519 identity (``request.state.nx_service_authed`` — backend/auth.py),
    NOT the raw ``Authorization`` prefix: a local ``X-User-Id`` request, a user
    session JWT, or any un-verified path could also carry an ``nx-agent:``
    prefix, and the panel already masks credentials on purpose — letting any of
    those pull the raw token back through the API would re-widen exactly that
    surface (an XSS could exfiltrate every bound bot token). ``assert_owned``
    then runs on the identity the middleware proved."""
    if not getattr(request.state, "nx_service_authed", False):
        raise HTTPException(
            status_code=403,
            detail="raw channel credentials are only served to the verified nx-service path",
        )


@router.get("/{agent_id}/channels/{channel}/credential")
async def get_channel_credential(request: Request, agent_id: str, channel: str) -> dict:
    """Raw credential for ``channel`` (service + owner gated), or
    ``{"bound": false}`` when the agent has no binding. Delegates to
    ChannelCredentialStore.DirectStore so this HTTP twin returns byte-identical
    payloads to the in-process seam a local caller would get."""
    await _gate(request, channel, agent_id)  # same double gate as the write endpoints
    raw = await ChannelDirectStore().get_credential(channel, agent_id)
    return raw if raw is not None else {"bound": False}


async def _gate(request: Request, channel: str, agent_id: str) -> None:
    """The shared double gate for the raw-credential resource: known channel,
    verified nx-service caller, agent owner."""
    if channel not in SUPPORTED_CHANNELS:
        raise HTTPException(status_code=404, detail=f"unknown channel: {channel}")
    _require_service_caller(request)
    await assert_owned(request, agent_id)


@router.patch("/{agent_id}/channels/{channel}/credential")
async def patch_channel_credential(
    request: Request, agent_id: str, channel: str, patch: dict = Body(...)
) -> dict:
    """Partial credential update — the WRITE side of the raw-secret resource
    (same double gate as the GET). Delegates to the seam's DirectStore so the
    deep-merge (nested dicts like lark's permission_state merged key-wise) is one
    implementation shared with the local path."""
    await _gate(request, channel, agent_id)
    return await ChannelDirectStore().patch_credential(channel, agent_id, patch)


@router.put("/{agent_id}/channels/{channel}/credential")
async def put_channel_credential(
    request: Request, agent_id: str, channel: str, raw: dict = Body(...)
) -> dict:
    """Full upsert of a raw credential dict (double-gated)."""
    await _gate(request, channel, agent_id)
    return await ChannelDirectStore().put_credential(channel, agent_id, raw)


@router.delete("/{agent_id}/channels/{channel}/credential")
async def delete_channel_credential(request: Request, agent_id: str, channel: str) -> dict:
    """Remove the binding (double-gated)."""
    await _gate(request, channel, agent_id)
    return await ChannelDirectStore().delete_credential(channel, agent_id)


@router.get("/{agent_id}/channels/name")
async def get_agent_channel_name(request: Request, agent_id: str) -> dict:
    """The agent's human-readable name (service + owner gated) — the HTTP twin
    of ChannelCredentialStore.DirectStore.get_agent_name, falling back to the
    id itself when the name is missing."""
    _require_service_caller(request)
    await assert_owned(request, agent_id)
    return {"agent_name": await ChannelDirectStore().get_agent_name(agent_id)}


@router.get("/{agent_id}/channels/owner")
async def get_agent_channel_owner(request: Request, agent_id: str) -> dict:
    """The agent's owner (created_by) user id (service + owner gated) — the HTTP
    twin of ChannelCredentialStore.DirectStore.get_agent_owner. NarraMessenger's
    media send + CLI workspace resolution need it. Since assert_owned already
    proved the caller owns the agent, this necessarily equals the caller's own
    identity, but it is served explicitly so the tool need not re-derive it."""
    _require_service_caller(request)
    await assert_owned(request, agent_id)
    return {"owner_user_id": await ChannelDirectStore().get_agent_owner(agent_id)}
