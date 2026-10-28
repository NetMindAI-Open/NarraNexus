"""
@file_name: factory.py
@author:
@date: 2026-08-10
@description: Composition root for AgentDataStore (blueprint P0).

Picks the transport the SAME way db_factory picks a db backend and broker_client
gates on BROKER_URL: keyed on ``NARRANEXUS_BACKEND_URL``.
  - unset  -> DirectStore  (local db access — the current behaviour everywhere
              until P2 flips cloud over; keeps `bash run.sh` / DMG unchanged and
              is a no-op no-behaviour-change import for existing cloud too)
  - set    -> HttpStore    (call the backend API; mcp holds no db creds)

The switch is a single env var so there is no scattered `if is_cloud` in tools
(rule #9/#20). ``identity_headers`` forwards the caller identity to the backend
on the Http path (populated from the live MCP request via
``current_identity_headers``); DirectStore ignores it.

Deployment-order contract: setting NARRANEXUS_BACKEND_URL is only valid AFTER
the identity chain is provisioned (broker signing key + NX_IDENTITY_PUBLIC_KEY_FILE
on backend) — backend's nx-agent service path fails CLOSED, so an HttpStore
call without a verifiable identity token is a 401 that the store surfaces as
an in-band "Error: ..." string. Flip the env last.
"""
from __future__ import annotations

import os
from typing import Optional

from loguru import logger

from narranexus.platform.module_system.data_access.store import (
    AgentDataStore,
    DirectStore,
    HttpStore,
)
from narranexus.platform.module_system.data_access.channel_store import (
    ChannelCredentialStore,
    DirectStore as ChannelDirectStore,
    HttpStore as ChannelHttpStore,
)


def current_identity_headers() -> dict:
    """The caller-identity headers to forward to the backend on the Http path.

    Reuses the same header set the executor→mcp hop carries, read from the live
    MCP request context; empty when there is no ambient request (e.g. tests /
    the Direct path). Kept here so the store layer never imports request
    internals directly.
    """
    try:
        from narranexus.platform.module_system._mcp_identity import _ambient_headers
    except ImportError as e:  # pragma: no cover — packaging-order edge only
        logger.debug(f"[data-access] identity module unavailable: {e}")
        return {}

    raw = _ambient_headers() or {}
    # Forward ONLY the NarraNexus identity headers + the borrowed bearer —
    # never cookies, x-forwarded-*, or anything else the transport attached.
    keep = {}
    for k, v in raw.items():
        lk = k.lower()
        if lk.startswith("x-narranexus-") or lk == "authorization":
            keep[k] = v
    return keep


def get_agent_data_store(identity_headers: Optional[dict] = None) -> AgentDataStore:
    backend_url = os.environ.get("NARRANEXUS_BACKEND_URL", "").strip()
    if backend_url:
        return HttpStore(
            backend_url,
            identity_headers=identity_headers
            if identity_headers is not None
            else current_identity_headers(),
        )
    return DirectStore()


def get_channel_credential_store(
    identity_headers: Optional[dict] = None,
) -> ChannelCredentialStore:
    """Composition root for ChannelCredentialStore — same env gate, same
    identity-header forwarding as ``get_agent_data_store`` (channel_store.py's
    module docstring explains why this is a separate Protocol rather than a
    method added to AgentDataStore)."""
    backend_url = os.environ.get("NARRANEXUS_BACKEND_URL", "").strip()
    if backend_url:
        return ChannelHttpStore(
            backend_url,
            identity_headers=identity_headers
            if identity_headers is not None
            else current_identity_headers(),
        )
    return ChannelDirectStore()
