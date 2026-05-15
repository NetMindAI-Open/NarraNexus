"""
@file_name: agents_artifacts.py
@author: Bin Liang
@date: 2026-05-08
@description: JWT-authed REST endpoints for agent-emitted Artifact tabs (pointer model).

Endpoints:
- GET    /{agent_id}/artifacts                       list (scope=session|pinned, session_id?)
- POST   /{agent_id}/artifacts/register              manual register: register a workspace file as an artifact
- GET    /{agent_id}/artifacts/{aid}                 metadata
- GET    /{agent_id}/artifacts/{aid}/view-token      mint a short-TTL HMAC token for the public raw route
- PATCH  /{agent_id}/artifacts/{aid}                 { pinned?, title? }
- DELETE /{agent_id}/artifacts/{aid}                 remove DB row (workspace files are NOT touched)

The raw-content route lives in `artifacts_public.py` under `/api/public/artifacts/raw/{token}/`
(JWT-bypassed; the HMAC token IS the auth). That keeps multi-file HTML
artifacts loadable via iframe `src=` in cloud mode.

Deletion is registry-only by design (2026-05-14-r3): removing an artifact
tab never deletes the agent's workspace files. The user can clean those up
via the workspace section in the config panel.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.module.common_tools_module._common_tools_impl import artifact_runner
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema import Artifact
from xyz_agent_context.utils.db_factory import get_db_client

from backend.routes import _artifact_token


router = APIRouter()


async def _verify_agent_ownership(request: Request, agent_id: str) -> None:
    """Verify that the JWT user owns the agent. Raises HTTPException(403) on failure.

    In local mode (no JWT / no request.state.user_id), ownership is not enforced.
    In cloud mode, the agent's created_by must match the JWT user_id.
    """
    if not hasattr(request.state, "user_id") or not request.state.user_id:
        return  # Local mode — no auth enforcement
    user_id = request.state.user_id
    db = await get_db_client()
    agent = await db.get_one("agents", {"agent_id": agent_id})
    if not agent:
        raise HTTPException(404, "agent not found")
    if agent.get("created_by") != user_id:
        raise HTTPException(403, "permission denied: you do not own this agent")


async def _resolve_agent_user_id(agent_id: str) -> str:
    """Look up an agent's owner user_id (the workspace owner).

    Used by the manual-register endpoint to resolve the workspace path
    `{base}/{agent_id}_{user_id}/` exactly the way the agent loop and MCP tool
    do (the agent_runtime overrides ctx.user_id with agent.created_by).
    """
    db = await get_db_client()
    agent = await db.get_one("agents", {"agent_id": agent_id})
    if not agent or not agent.get("created_by"):
        raise HTTPException(404, "agent not found")
    return str(agent["created_by"])


class PatchArtifact(BaseModel):
    pinned: Optional[bool] = None
    title: Optional[str] = None


class RegisterRequest(BaseModel):
    file_path: str = Field(..., description="Workspace-relative or absolute path to the entry file")
    kind: str
    title: str
    description: Optional[str] = None
    target_artifact_id: Optional[str] = None


class ViewTokenResponse(BaseModel):
    token: str
    raw_url: str
    expires_at: int


# ── list / register ──────────────────────────────────────────────────────────


@router.get("/{agent_id}/artifacts", response_model=List[Artifact])
async def list_artifacts(
    request: Request,
    agent_id: str,
    scope: Literal["session", "pinned"] = Query("session"),
    session_id: Optional[str] = Query(None),
):
    """
    List artifacts for an agent.

    Args:
        scope: 'session' (default) returns non-pinned artifacts for the given
               session_id; 'pinned' returns all pinned artifacts for the agent.
        session_id: Required when scope='session'.
    """
    await _verify_agent_ownership(request, agent_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)
    if scope == "pinned":
        return await repo.list_pinned(agent_id)
    if not session_id:
        raise HTTPException(400, "session_id is required when scope=session")
    return await repo.list_by_session(agent_id, session_id)


@router.post("/{agent_id}/artifacts/register", response_model=Artifact)
async def register_artifact(request: Request, agent_id: str, body: RegisterRequest):
    """
    Manually register a workspace file as an artifact.

    Powers the "register as artifact" action in the workspace tree viewer.
    Delegates to the same `artifact_runner.register_artifact` the MCP tool
    uses, so validation, quota, and path-confinement rules are identical.

    `file_path` may be absolute or workspace-relative; it must be inside the
    agent's workspace AND in a subdirectory (not directly in the workspace
    root). The entry file's directory becomes the artifact root.
    """
    await _verify_agent_ownership(request, agent_id)
    user_id = await _resolve_agent_user_id(agent_id)

    db = await get_db_client()
    repo = ArtifactRepository(db)
    try:
        result = await artifact_runner.register_artifact(
            repo=repo,
            agent_id=agent_id,
            user_id=user_id,
            session_id=None,                  # manual registrations are always agent-scoped
            kind=body.kind,                   # type: ignore[arg-type]
            entry_path=body.file_path,
            title=body.title,
            description=body.description,
            target_artifact_id=body.target_artifact_id,
        )
    except artifact_runner.ArtifactError as e:
        raise HTTPException(status_code=e.code, detail=str(e))

    art = await repo.get_by_id(result.artifact_id)
    if art is None:
        # Should be impossible — the runner just wrote the row.
        raise HTTPException(500, "artifact disappeared after registration")
    return art


# ── detail / view-token / patch / delete ─────────────────────────────────────


@router.get("/{agent_id}/artifacts/{artifact_id}/view-token", response_model=ViewTokenResponse)
async def mint_view_token(request: Request, agent_id: str, artifact_id: str):
    """
    Mint a short-TTL HMAC view token for an artifact's raw content.

    The frontend calls this once before loading the iframe `src` for HTML
    artifacts (and uses the same flow for non-HTML kinds for code symmetry —
    no JWT header needed on the raw fetch).

    The returned `raw_url` is the directory-style URL `/raw/{token}/`; the
    entry file is served at that URL, sibling assets at `/raw/{token}/{name}`.
    """
    await _verify_agent_ownership(request, agent_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)
    art = await repo.get_by_id(artifact_id)
    if art is None or art.agent_id != agent_id:
        raise HTTPException(404, "artifact not found")

    token = _artifact_token.mint(agent_id=agent_id, artifact_id=artifact_id)
    # Decode exp without re-verifying — the payload is the b64url part before '.'.
    import base64
    import json as _json
    payload_b64 = token.split(".", 1)[0]
    pad = "=" * (-len(payload_b64) % 4)
    payload = _json.loads(base64.urlsafe_b64decode(payload_b64 + pad).decode("utf-8"))
    raw_url = f"/api/public/artifacts/raw/{token}/"
    return ViewTokenResponse(token=token, raw_url=raw_url, expires_at=int(payload["exp"]))


@router.get("/{agent_id}/artifacts/{artifact_id}", response_model=Artifact)
async def get_artifact(request: Request, agent_id: str, artifact_id: str):
    """Get artifact metadata."""
    await _verify_agent_ownership(request, agent_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)
    art = await repo.get_by_id(artifact_id)
    if art is None or art.agent_id != agent_id:
        raise HTTPException(404, "artifact not found")
    return art


@router.patch("/{agent_id}/artifacts/{artifact_id}", response_model=Artifact)
async def patch_artifact(request: Request, agent_id: str, artifact_id: str, body: PatchArtifact):
    """
    Update artifact metadata (pinned flag and/or title).

    Pinning an artifact clears its session_id, making it agent-scoped.
    Unpinning an agent-created artifact (no session to restore) is rejected
    with 400 — the caller should DELETE instead.
    """
    await _verify_agent_ownership(request, agent_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)
    existing = await repo.get_by_id(artifact_id)
    if existing is None or existing.agent_id != agent_id:
        raise HTTPException(404, "artifact not found")

    if body.pinned is not None:
        if body.pinned is False and existing.original_session_id is None and existing.pinned:
            # Unpinning would restore a NULL session_id — the artifact would
            # become invisible (not in any session list, not in pinned list).
            # This is the case for agent-created artifacts. Force DELETE.
            raise HTTPException(
                400,
                "this artifact is agent-scoped (no session to restore); "
                "use DELETE to remove it instead of unpinning",
            )
        await repo.set_pinned(artifact_id, pinned=body.pinned)
    if body.title is not None:
        await db.update("instance_artifacts", {"artifact_id": artifact_id}, {"title": body.title[:200]})

    return await repo.get_by_id(artifact_id)


@router.delete("/{agent_id}/artifacts/{artifact_id}")
async def delete_artifact(request: Request, agent_id: str, artifact_id: str):
    """Delete an artifact's DB row.

    Registry-only: the agent's workspace files are NEVER touched. The user
    cleans those up via the workspace section in the config panel when they
    want to free disk. Keeping this surgical removed a class of footguns
    (workspace-root rmtree, shared-directory collisions) — see the 2026-05-14
    architecture decision recorded in the artifact-pointer-model spec.
    """
    await _verify_agent_ownership(request, agent_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)
    existing = await repo.get_by_id(artifact_id)
    if existing is None or existing.agent_id != agent_id:
        raise HTTPException(404, "artifact not found")

    await repo.delete(artifact_id)
    logger.info(f"Artifact registry row deleted: {artifact_id}")
    return {"deleted": artifact_id}
