"""
@file_name: _ownership.py
@author:
@date: 2026-08-10
@description: Canonical agent-ownership verification for backend routes.

Consolidates the `_verify_agent_ownership` / `_require_agent_owner` copies
this refactor has absorbed so far (home_assistant, slack, lark, telegram,
discord, wechat, narramessenger — further same-shape copies in
agents/artifacts, agents/llm_config, agents/circuit_breaker and migrate.py
are tracked as follow-ups, each with semantics that drifted and need their
own judgement). Duplicated ownership logic is exactly how ownership
semantics drift apart — the same class of bug that
`AgentRepository.resolve_owner` already fixed on the agent side; this is its
backend-route counterpart.

Ownership = ``agents.created_by``.

SECURITY POSTURE — read before hanging anything sensitive off the first two
surfaces: when the backend runs without the auth middleware enforcing
identity (local mode: no ``request.state.user_id``), ``check_owned`` and
``assert_owned`` SKIP enforcement — every route using them is then
effectively unauthenticated, and any HTTP caller that can reach the backend
port can bind / unbind / test any bot. Do NOT add sensitive operations
behind those two assuming auth — they won't have any in local mode. All
IM-channel routes mirror this exact contract; keep them in lockstep.
The third surface, ``require_owned_workspace_agent``, is the deliberate
exception: it guards the filesystem, so it REQUIRES an identity (401) and
never skips.

Four surfaces because the callers return four shapes:
- ``check_owned`` -> error string | None  (channel routes wrap it in a
  ``{"success": False, "error": ...}`` payload)
- ``assert_owned`` -> raises HTTPException 404/403  (home_assistant-style
  routes)
- ``require_owned_workspace_agent`` -> returns the caller's user_id, raises
  a uniform 404 (routes that turn ``agent_id`` into a workspace directory:
  files / attachments / skills — the 2026-10-28 cross-tenant read)
- ``assert_visible`` -> raises on a non-owned, non-public agent  (read /
  drive surfaces: websocket, chat event-log — added 2026-10-28)

The first three map from one shared deny-reason decision, so no surface ever
parses another's prose to learn what happened.
The fourth, ``assert_visible``, answers a different question — may this
viewer SEE / drive the agent (owned OR public)? It takes an already-resolved
viewer id, not a request, so it has NO local-mode no-op (callers pass the
local user id), and it answers a uniform 404 for unknown and foreign agents
alike. It does not route through ``_deny_reason``: a failed lookup surfaces as
the db error itself (a non-HTTP exception — a 5xx / InternalError frame at the
caller), never as "not found".
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request

from backend.auth import resolve_current_user_id
from narranexus.platform.repository import AgentRepository
from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils.workspace_paths import safe_id_segment

# Deny reasons — the ONE decision both public surfaces map from. A failed
# LOOKUP is not a reason either surface renders as prose: _deny_reason raises
# 503 directly, so a db outage produces a 5xx on BOTH surfaces (the channel
# routes would otherwise wrap the string in a 200 payload — exactly the
# "no 5xx metric to alarm on" failure the split exists to prevent).
_DENY_UNKNOWN = "unknown"      # agent does not exist -> 404-class
_DENY_NOT_OWNER = "not_owner"  # exists, not yours -> 403-class


def _caller_user_id(request: Request) -> Optional[str]:
    """The authenticated caller, or None in local mode."""
    uid = getattr(request.state, "user_id", None)
    return uid or None


async def _deny_reason(
    request: Request, agent_id: str, *, user_id: Optional[str] = None
) -> Optional[str]:
    """Why the caller may NOT touch ``agent_id`` — or None to allow.

    Local mode (no user_id) allows: see the module docstring's security
    posture. A failed owner LOOKUP is its own reason (PR #258 review #4):
    ``resolve_owner`` distinguishes ``None`` (db failure) from ``""``
    (unknown agent), and reporting an infrastructure failure as "not found"
    would make a db outage look like a batch of users' agents vanishing,
    with no 5xx metric to alarm on.
    """
    # A caller that already resolved (and REQUIRED) the identity passes it in,
    # which makes the local-mode pass-through below unreachable for it.
    user_id = user_id or _caller_user_id(request)
    if not user_id:
        return None  # local mode — no auth enforcement
    db = await get_db_client()
    owner = await AgentRepository(db).resolve_owner(agent_id)
    if owner is None:
        raise HTTPException(
            status_code=503,
            detail="Ownership check unavailable (database error) — try again.",
        )
    if not owner:
        # NOTE an existing row with NULL/empty created_by also lands here
        # (404), where the pre-consolidation copies answered 403 — both deny,
        # and "an agent nobody owns" is closer to not-found than to
        # somebody-else's; intentional.
        return _DENY_UNKNOWN
    if owner != user_id:
        return _DENY_NOT_OWNER
    return None


async def check_owned(request: Request, agent_id: str) -> Optional[str]:
    """Return an error string when the caller does not own ``agent_id``.

    Returns None when the caller owns it OR when running in local mode (no
    user_id → no enforcement, as every copy did). Unknown owners deny
    (fail-closed) rather than leak; a FAILED ownership lookup raises 503
    from the shared decision — channel routes would otherwise wrap the
    string in a 200 payload and a db outage would emit zero 5xx.
    """
    reason = await _deny_reason(request, agent_id)
    if reason is None:
        return None
    if reason == _DENY_UNKNOWN:
        return f"Agent {agent_id} not found."
    return "Permission denied: you do not own this agent."


async def assert_owned(request: Request, agent_id: str) -> None:
    """Raise HTTPException when the caller does not own ``agent_id``.

    Local mode → no-op. 404 unknown agent, 403 non-owner; a failed lookup
    raises 503 from the shared decision (an infrastructure fault surfaces as
    a server error, not as the resource's absence).
    """
    reason = await _deny_reason(request, agent_id)
    if reason is None:
        return
    if reason == _DENY_UNKNOWN:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found.")
    raise HTTPException(
        status_code=403, detail="Permission denied: you do not own this agent."
    )


async def require_owned_workspace_agent(request: Request, agent_id: str) -> str:
    """The THIRD surface, for routes that turn ``agent_id`` into a directory.

    Born from the 2026-10-28 cross-tenant read: ``/api/agents/../files``
    spliced the URL segment into ``{base}/{user_id}/{agent_id}`` and served
    every tenant's files. Differences from the two surfaces above, each
    deliberate:

    - identity is REQUIRED (401 via ``resolve_current_user_id``) — there is
      no local-mode pass-through, because the thing being protected is the
      filesystem, not a bot binding;
    - the segment must be path-safe (``safe_id_segment``) BEFORE any lookup,
      so ``..`` / ``%2e%2e`` / ``a/b`` never reach a ``Path`` join;
    - unknown and not-owned both answer 404 — a filesystem-facing route must
      not tell a caller which other users' agent ids exist;
    - a failed lookup still raises 503 from ``_deny_reason``.

    Returns the caller's user_id so the route builds the path from the same
    identity that was checked.
    """
    user_id = await resolve_current_user_id(request)
    try:
        safe_id_segment(agent_id, label="agent_id")
    except ValueError:
        raise HTTPException(status_code=404, detail="agent not found")
    reason = await _deny_reason(request, agent_id, user_id=user_id)
    if reason is not None:
        raise HTTPException(status_code=404, detail="agent not found")
    return user_id
async def assert_visible(viewer_id: str, agent_id: str) -> dict:
    """Ensure ``viewer_id`` may see ``agent_id`` (owned OR public); return its row.

    Unknown and foreign-private agents both raise the SAME 404, so agent ids
    cannot be enumerated. No local-mode bypass — the caller supplies the
    viewer (``/ws/agent/run`` passes the anchored local user id too). A db
    failure propagates as-is (not an HTTPException), so it can never be read
    as the agent's absence. Published on the plugin web seam under the
    dashboard name ``assert_agent_visible`` (``contracts.web.WebHost``).
    """
    # Resolved at call time (not the module-level get_db_client) so callers'
    # tests that patch db_factory.get_db_client keep reaching this read.
    from narranexus.platform.utils.db.db_factory import get_db_client as _get_db

    db = await _get_db()
    rows = await db.execute(
        "SELECT agent_id, agent_name, created_by, is_public "
        "FROM agents WHERE agent_id=%s LIMIT 1",
        (agent_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="agent not found")
    row = rows[0]
    if row["created_by"] != viewer_id and not row.get("is_public"):
        raise HTTPException(status_code=404, detail="agent not found")  # don't leak existence
    return row
