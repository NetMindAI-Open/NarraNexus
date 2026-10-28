"""
@file_name: routes_profile.py
@author:
@date: 2026-08-10
@description: Agent profile UPDATE endpoint for the MCP data-access seam
(agent-scoped, owner-gated).

Byte-parity Http twin of the update_agent_profile MCP tool so the HttpStore path
of AgentDataStore can run the rename transaction (name/description + the
identity-note correction + same-owner clash note + discovery refresh) without db
credentials in the mcp container. Both this route and the seam's DirectStore call
the SAME shared update_agent_profile_from_args, so the two paths are byte-
identical.

The tool returns a DYNAMIC status string (which fields changed, plus any
same-owner name-clash note), not a fixed constant — so unlike the awareness
route (which reconstructs its constant string from a structured response), this
endpoint returns the tool's string verbatim in a ``{"message": <str>}`` envelope
that HttpStore unwraps. Owner-gated via ``assert_owned`` like the other seam
routes; the handler always answers 200 for handled outcomes.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel

from narranexus.sdk.web import require_agent_owner
from narranexus_plugins.awareness_module import update_agent_profile_from_args
from narranexus.platform.utils.db.db_factory import get_db_client

router = APIRouter()


class ProfileUpdateBody(BaseModel):
    """Body for POST .../profile/update — the update_agent_profile tool's args
    (both optional; only passed ones change).

    Deliberately NO ``Field(max_length=AGENT_TEXT_MAX_LENGTH)`` here: the length
    cap is enforced in the shared update_agent_profile_from_args, which returns
    a readable "Error: … too long" STRING (HTTP 200). A route-level Field would
    422 BEFORE the shared fn runs, so HttpStore would see "…rejected (422)"
    while DirectStore returns the fn's string — breaking the byte-parity this
    seam exists for. Enforcing in the one shared fn keeps both paths identical,
    and an over-long value is still rejected (never written) on both."""
    new_name: Optional[str] = None
    new_description: Optional[str] = None


@router.post("/{agent_id}/profile/update")
async def update_profile(agent_id: str, body: ProfileUpdateBody, request: Request) -> dict:
    """Set the agent's display name and/or one-line peer description — twin of
    the ``update_agent_profile`` MCP tool. Returns ``{"message": <tool string>}``
    (byte-parity with the seam's DirectStore, which returns the same string)."""
    await require_agent_owner(request, agent_id)
    try:
        db = await get_db_client()
    except Exception as e:  # noqa: BLE001 — surface as the tool's own error string
        logger.warning(f"profile update failed: {e}")
        return {"message": f"Error: {e}"}
    message = await update_agent_profile_from_args(
        db, agent_id, new_name=body.new_name, new_description=body.new_description,
    )
    return {"message": message}


# ---- plugin contribution (batch 3c.4): this twin router of the data-access
# seam is provided by builtin.awareness through backend.routes (mounted under /api/agents
# by backend.plugins_host, no longer included by routes/agents/core.py), so the
# HTTP twin disappears together with the capability when the plugin is disabled.
from narranexus.contracts.route import RouterSpec  # noqa: E402
from narranexus.kernel.plugins.registry import Contribution  # noqa: E402

ROUTES = (Contribution("agents_profile", lambda: RouterSpec(router, "/api/agents", tags=("Agents",))),)
