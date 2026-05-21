"""
@file_name: users_artifacts.py
@author: Bin Liang
@date: 2026-05-09
@description: User-scoped artifact endpoints — cross-agent list + bulk delete.

Backs the Settings → Artifacts management UI. Distinct from
agents_artifacts.py (agent-scoped routes) because the management UI needs to
see the full user-owned set across every agent the user owns, and let them
delete in bulk.

Endpoints:
- GET    /{user_id}/artifacts                 list every artifact owned by user
- DELETE /{user_id}/artifacts                 bulk delete registry rows only

Registry-only delete (2026-05-14-r3): workspace files are never removed by
this endpoint. The user cleans those up via the agent's workspace section.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema import Artifact
from xyz_agent_context.utils.db_factory import get_db_client


router = APIRouter()


async def _verify_user_self(request: Request, user_id: str) -> None:
    """In cloud mode, the JWT user must match the path user_id.

    Local mode (no JWT / no request.state.user_id) skips enforcement.
    Cloud mode mismatch returns 403 to avoid leaking which user_ids exist.
    """
    if not hasattr(request.state, "user_id") or not request.state.user_id:
        return
    if request.state.user_id != user_id:
        raise HTTPException(403, "not authorized for this user")


class BulkDeleteRequest(BaseModel):
    artifact_ids: List[str] = Field(default_factory=list, max_length=200)


class BulkDeleteResponse(BaseModel):
    deleted: int
    skipped_not_owned: List[str] = Field(default_factory=list)


@router.get("/{user_id}/artifacts", response_model=List[Artifact])
async def list_user_artifacts(user_id: str, request: Request):
    """List every artifact owned by the user, ordered by recency."""
    await _verify_user_self(request, user_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)
    return await repo.list_by_user(user_id)


@router.delete("/{user_id}/artifacts", response_model=BulkDeleteResponse)
async def bulk_delete_artifacts(
    user_id: str,
    request: Request,
    body: BulkDeleteRequest,
):
    """Bulk-delete artifact registry rows. Each ID is verified to belong to
    `user_id`; unowned IDs are returned in `skipped_not_owned` (never silently
    deleted). Workspace files are NOT touched — the user cleans those up via
    the agent's workspace section."""
    await _verify_user_self(request, user_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)

    if not body.artifact_ids:
        return BulkDeleteResponse(deleted=0)

    ids_to_delete: List[str] = []
    skipped: List[str] = []
    for aid in body.artifact_ids:
        art = await repo.get_by_id(aid)
        if art is None or art.user_id != user_id:
            skipped.append(aid)
            continue
        ids_to_delete.append(aid)

    deleted = await repo.bulk_delete(ids_to_delete)
    return BulkDeleteResponse(deleted=deleted, skipped_not_owned=skipped)
