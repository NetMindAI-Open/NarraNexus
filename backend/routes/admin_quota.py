"""
@file_name: admin_quota.py
@author: Bin Liang
@date: 2026-04-16
@description: Staff-only quota management routes.

/grant — upserts: if the target user has no quota row yet, creates one
         with initial=0 then credits the grant amount (exhausted users
         are automatically flipped to active when the credit lifts
         remaining > 0).
/init  — uses SYSTEM_DEFAULT_QUOTA_* env defaults. Idempotent;
         returning the existing row if one already exists.

Both require `role=staff` on the caller's JWT.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.auth import _is_cloud_mode


router = APIRouter(prefix="/api/admin/quota", tags=["admin", "quota"])


class GrantRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    input_tokens: int = Field(..., ge=0)
    output_tokens: int = Field(..., ge=0)
    note: str | None = None


class InitRequest(BaseModel):
    user_id: str = Field(..., min_length=1)


def _require_staff_or_raise(request: Request) -> str:
    if not _is_cloud_mode():
        raise HTTPException(
            status_code=503,
            detail="admin endpoints are only available in cloud mode",
        )
    role = getattr(request.state, "role", None)
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    if role != "staff":
        raise HTTPException(status_code=403, detail="staff role required")
    return user_id


def _quota_to_dict(q) -> dict:
    return {
        "user_id": q.user_id,
        "status": q.status.value,
        "initial_input_tokens": q.initial_input_tokens,
        "initial_output_tokens": q.initial_output_tokens,
        "granted_input_tokens": q.granted_input_tokens,
        "granted_output_tokens": q.granted_output_tokens,
        "used_input_tokens": q.used_input_tokens,
        "used_output_tokens": q.used_output_tokens,
        "remaining_input_tokens": q.remaining_input,
        "remaining_output_tokens": q.remaining_output,
    }


async def _resolve_user_id_or_404(request: Request, target_user_id: str) -> None:
    """Confirm the target exists in `users`. We reuse the existing UserRepository
    available on app.state (populated by lifespan)."""
    user_repo = getattr(request.app.state, "user_repository", None)
    if user_repo is None:
        raise HTTPException(status_code=503, detail="user repository not wired")
    user = await user_repo.get_user(target_user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")


@router.post("/grant")
async def grant(request: Request, payload: GrantRequest) -> dict:
    _require_staff_or_raise(request)
    await _resolve_user_id_or_404(request, payload.user_id)

    quota_svc = getattr(request.app.state, "quota_service", None)
    if quota_svc is None:
        raise HTTPException(status_code=503, detail="quota service not wired")

    q = await quota_svc.grant(
        payload.user_id, payload.input_tokens, payload.output_tokens
    )
    return _quota_to_dict(q)


@router.post("/init")
async def init(request: Request, payload: InitRequest) -> dict:
    _require_staff_or_raise(request)
    await _resolve_user_id_or_404(request, payload.user_id)

    sys_svc = getattr(request.app.state, "system_provider", None)
    quota_svc = getattr(request.app.state, "quota_service", None)
    if sys_svc is None or quota_svc is None:
        raise HTTPException(status_code=503, detail="quota services not wired")
    if not sys_svc.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="system-default quota feature is disabled",
        )

    q = await quota_svc.init_for_user(payload.user_id)
    if q is None:
        # init_for_user swallowed an exception; the user saw an HTTP error.
        raise HTTPException(status_code=500, detail="quota init failed")
    return _quota_to_dict(q)
