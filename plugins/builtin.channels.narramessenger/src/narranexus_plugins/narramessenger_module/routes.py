"""
@file_name: routes.py
@date: 2026-06-18
@description: Backend API routes for NarraMessenger binding + status.

Endpoints:
  POST   /api/narramessenger/prewarm         — NarraMessenger-called executor prewarm
  GET    /api/narramessenger/prewarm/status  — readiness probe for the prewarm

Binding (the pasted bind link → ``_narramessenger_service.do_bind``), the
credential view and unbind are the generic ``/api/channels/narramessenger/…``
routes since batch 4d.3. The prewarm pair is machine-to-machine: the NarraMessenger
backend calls it with the per-agent bearer_token when a voice call starts
ringing, so the owner's executor container is warm by the time the call
connects.
"""

from __future__ import annotations

import asyncio
import hmac
import itertools

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from loguru import logger

from narranexus.platform.agent_framework.loop.broker_client import (
    ExecutorEnsureResult,
    broker_url,
    ensure_executor,
    executor_healthy,
    wait_until_ready,
)
from narranexus.platform.agent_runtime.executor_reaper import no_live_recorded_run_for
from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredentialManager,
)
from narranexus.platform.repository.agent_repository import AgentRepository


# One canonical owner check (backend/routes/_ownership.py); module-level
# alias keeps the historical local name at its ~per-route call sites. No
# import cycle: this subpackage never gets imported back from _ownership.
from narranexus.sdk.web import check_agent_owner as _verify_agent_ownership

router = APIRouter()

_SAFE_ID_PATTERN = r"^[a-zA-Z0-9_\-]+$"


async def _get_db():
    from narranexus.platform.utils.db.db_factory import get_db_client
    return await get_db_client()


# Identifier bounds, shared by the POST body, the status query and the
# pre-body bearer dependency. 64 == the nexus_profile_id column (VARCHAR(64)):
# anything longer can never resolve.
_MXID_MAX_LENGTH = 255
_PROFILE_ID_MAX_LENGTH = 64


class PrewarmRequest(BaseModel):
    """Exactly one of the two agent identifiers is required. agent_profile_id
    resolves only for rows bound after profileId persistence landed (older
    bindings need a rebind)."""
    agent_matrix_user_id: str = Field(default="", max_length=_MXID_MAX_LENGTH)
    agent_profile_id: str = Field(default="", max_length=_PROFILE_ID_MAX_LENGTH)
    rtc_session_id: str = Field(default="", max_length=128)  # log correlation only


# In-process prewarm ledger: user_id -> {"status", "executor_url", "gen", "task"}.
# Single-host by design today (binding rule #20): the seam to move this out is
# the broker itself — on backend restart, readiness simply re-reports False and
# the next prewarm call re-ensures (idempotent at the broker).
#
# The entry doubles as the STRONG reference to the in-flight warmer task (the
# event loop only keeps weak refs — asyncio docs): an entry is only ever
# replaced by a newer generation, which holds its own task ref; the superseded
# task keeps running harmlessly and its generation-guarded writes no-op.
_PREWARM_STATE: dict[str, dict] = {}

# Monotonic generation per warmer task: every ledger write inside _do_prewarm
# is guarded by "my gen is still the current entry's gen", so a stale task can
# never clobber a newer entry (e.g. a stale failure-pop landing after a
# newer request already started warming).
_PREWARM_GEN = itertools.count(1)


async def _resolve_prewarm_target(
    request: Request, body_mxid: str, body_profile: str
) -> str:
    """Shared auth + resolution for both prewarm endpoints; returns the owner.

    Raises HTTPException on failure. Auth is the per-agent bearer_token
    (constant-time compare) — NOT a user JWT; both paths sit in
    AUTH_EXEMPT_PATHS and self-credential here, same pattern as
    /api/admin/runtime/status. The bearer's PRESENCE is checked first, so a
    caller with no credential learns nothing about the identifiers.
    """
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer")
    if bool(body_mxid) == bool(body_profile):  # neither or both
        raise HTTPException(
            status_code=422,
            detail="exactly one of agent_matrix_user_id / agent_profile_id required",
        )
    if len(body_mxid) > _MXID_MAX_LENGTH or len(body_profile) > _PROFILE_ID_MAX_LENGTH:
        # Can never resolve; answer like an unknown binding without a db read.
        raise HTTPException(status_code=404, detail="agent binding not found")
    db = await _get_db()
    mgr = NarramessengerCredentialManager(db)
    cred = (
        await mgr.get_by_matrix_user_id(body_mxid)
        if body_mxid
        else await mgr.get_by_profile_id(body_profile)
    )
    if cred is None or not cred.enabled:
        raise HTTPException(status_code=404, detail="agent binding not found")
    if not cred.bearer_token or not hmac.compare_digest(
        token.encode(), cred.bearer_token.encode()
    ):
        raise HTTPException(status_code=403, detail="bearer mismatch")
    owner = await AgentRepository(db).resolve_owner(cred.agent_id)
    if owner is None:
        raise HTTPException(status_code=503, detail="owner lookup failed")
    if not owner:
        raise HTTPException(status_code=404, detail="agent has no owner")
    return owner


def _as_identifier(value: Any) -> str:
    return value if isinstance(value, str) else ""


async def require_prewarm_owner(request: Request) -> str:
    """``Depends`` form of the prewarm auth; returns the agent owner's user_id.

    The bearer is per-agent, so verifying it needs the agent identifier. As a
    dependency this runs BEFORE FastAPI validates the body / query, so it reads
    the two identifiers leniently itself (query string for GET, JSON object for
    POST; anything else counts as absent) instead of through PrewarmRequest.
    A missing or wrong bearer therefore answers 401 / 403 and never a 422 that
    spells out the body schema (2026-10-28 review M-3). Only an authenticated
    caller ever reaches the model's own validation.
    """
    if request.method == "GET":
        source: Any = request.query_params
    else:
        try:
            source = await request.json()
        except ValueError:
            source = None
        if not isinstance(source, dict):
            source = {}
    return await _resolve_prewarm_target(
        request,
        _as_identifier(source.get("agent_matrix_user_id")),
        _as_identifier(source.get("agent_profile_id")),
    )


def _owns_ledger_entry(user_id: str, gen: int) -> bool:
    """True iff the current ledger entry is this warmer's own generation."""
    return _PREWARM_STATE.get(user_id, {}).get("gen") == gen


async def _do_prewarm(user_id: str, gen: int) -> None:
    """Background warmer — fire-and-forget, so it catches EVERYTHING (an
    unawaited task must never carry a live exception).

    Every ledger write is generation-guarded: if a newer prewarm replaced
    our entry while we awaited, our late result must not clobber it. The
    ready write mutates the entry in place so the entry keeps holding this
    task's strong reference; failure (like broker-vanished) pops the entry
    entirely — the next POST re-warms either way.
    """
    try:
        # Prewarm is the RIGHT place to roll a stale executor image: it is
        # not a run, and its whole purpose is to move cold-start out of the
        # user's turn. Leaving the default False here would defer the
        # replacement to this user's next turn — so the first interaction
        # after every image rebuild would warm the OLD image, then pay a full
        # stop + await-gone + run + wait_until_ready inline, with the prewarm
        # wasted.
        #
        # No run to exclude: this is not one.
        #
        # Residual risk, stated rather than argued away: the verdict covers
        # RECORDED RUNS only. A live office-watch session runs inside this
        # same container and is invisible to it, so a replacement authorised
        # here can take that session down. "Prewarm itself holds nothing on
        # the container" would be the wrong argument — the authorisation is
        # about the container, not about this caller.
        #
        # Accepted for now because the idle reaper already destroys such a
        # container on its 20-minute TTL (office-watch does not refresh the
        # admission ledger either), so this adds a trigger moment rather than
        # a new class of failure. The fix is one liveness rule that sees
        # non-run holders too: have watch/ensure record a lease row that
        # live_run_elsewhere reads, so all three consumers inherit it. That
        # belongs in its own change, not here.
        result = await ensure_executor(
            user_id, allow_stale_replace=await no_live_recorded_run_for(user_id)
        )
        if result is None:  # broker vanished between guard and call
            if _owns_ledger_entry(user_id, gen):
                _PREWARM_STATE.pop(user_id, None)
            return
        await wait_until_ready(result.url)
        if _owns_ledger_entry(user_id, gen):
            state = _PREWARM_STATE[user_id]
            state["status"] = "ready"
            state["executor_url"] = result.url
        logger.info(f"[prewarm] executor ready user={user_id} cold={result.cold_started}")
    except Exception as e:  # noqa: BLE001
        # A failed entry carries no information the next POST could use —
        # it re-warms regardless — so drop the entry instead of parking a
        # "failed" status in the ledger.
        if _owns_ledger_entry(user_id, gen):
            _PREWARM_STATE.pop(user_id, None)
        logger.warning(f"[prewarm] failed user={user_id}: {type(e).__name__}: {e}")


@router.post("/prewarm")
async def prewarm(body: PrewarmRequest, owner: str = Depends(require_prewarm_owner)):
    """NarraMessenger calls this when a call starts ringing (F28): warm the
    owner's executor while the UI shows "connecting". 202 always on success
    paths; prewarm failure must never block the call itself. Auth runs in
    ``require_prewarm_owner`` before ``body`` is validated."""
    state = _PREWARM_STATE.get(owner)
    # timeout=1.0: the caller is mid-ring — a wedged container must not cost
    # them the full 5s default before we fall through to re-warming.
    if (
        state
        and state["status"] == "ready"
        and await executor_healthy(state["executor_url"], timeout=1.0)
    ):
        return JSONResponse(status_code=202, content={"status": "already_warm"})
    if (
        state
        and state["status"] == "warming"
        and state["task"] is not None
        and not state["task"].done()
    ):
        # In-flight dedup: a call rings once but the partner may POST several
        # times — piling extra ensure_executor calls onto the broker helps
        # nobody. The live task will flip this entry to ready, or pop it
        # on failure.
        return JSONResponse(status_code=202, content={"status": "warming"})
    if broker_url() is None:  # local/desktop: nothing to warm — never an error
        return JSONResponse(status_code=202, content={"status": "skipped"})
    gen = next(_PREWARM_GEN)
    entry: dict[str, Any] = {
        "status": "warming", "executor_url": "", "gen": gen, "task": None,
    }
    _PREWARM_STATE[owner] = entry
    # Store the entry BEFORE creating the task, then patch the task ref in.
    # No await separates create_task from the patch, so neither the new task
    # nor another request can ever observe entry["task"] is None — while the
    # reverse order (task first, entry after) would let a fast task's ledger
    # write be clobbered by our own late entry store.
    entry["task"] = asyncio.create_task(_do_prewarm(owner, gen))
    logger.info(f"[prewarm] requested user={owner} rtc_session={body.rtc_session_id or '-'}")
    return JSONResponse(status_code=202, content={"status": "warming"})


@router.get("/prewarm/status")
async def prewarm_status(
    agent_matrix_user_id: str = Query(default="", max_length=_MXID_MAX_LENGTH),
    agent_profile_id: str = Query(default="", max_length=_PROFILE_ID_MAX_LENGTH),
    owner: str = Depends(require_prewarm_owner),
):
    """Optional readiness probe so the caller can size the "connecting" UI.

    Query constraints mirror the POST body's (PrewarmRequest): same fields,
    same bounds. Auth (``require_prewarm_owner``) runs before they are checked.
    """
    state = _PREWARM_STATE.get(owner)
    if state and state.get("executor_url"):
        # timeout=1.0 like the POST's already-warm probe: the partner polls
        # this mid-ring — a wedged container must not stall the poll for the
        # 5s default when "not ready yet" is a perfectly good answer.
        return {"ready": await executor_healthy(state["executor_url"], timeout=1.0)}
    return {"ready": False}


# ---- plugin contribution (batch 3c.5): this router belongs to builtin.channels.narramessenger and is
# mounted by backend.plugins_host from backend.routes (no longer included by
# backend/main.py), so its paths 404 together with the plugin when disabled.
from narranexus.contracts.route import RouterSpec  # noqa: E402
from narranexus.kernel.plugins.registry import Contribution  # noqa: E402

ROUTES = (Contribution("channel_narramessenger", lambda: RouterSpec(router, "/api/narramessenger", tags=('NarraMessenger',))),)
