"""
@file_name: quota.py
@author: Bin Liang
@date: 2026-04-16
@description: User-facing quota query endpoint.

Three explicit response shapes so the frontend does not have to infer
"is the feature on":
  - {enabled: false}                          — local mode / env not set
  - {enabled: true, status: "uninitialized"}  — cloud, user has no row yet
  - {enabled: true, status: "active"|..., …}  — full budget breakdown
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.auth import _is_cloud_mode


router = APIRouter(prefix="/api/quota", tags=["quota"])


@router.get("/me")
async def get_my_quota(request: Request) -> dict:
    # Local mode: feature is strictly off; do not consult any service.
    if not _is_cloud_mode():
        return {"enabled": False}

    sys_svc = getattr(request.app.state, "system_provider", None)
    quota_svc = getattr(request.app.state, "quota_service", None)
    if sys_svc is None or quota_svc is None:
        # Services not wired (should only happen pre-lifespan in tests).
        return {"enabled": False}

    if not sys_svc.is_enabled():
        return {"enabled": False}

    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    q = await quota_svc.get(user_id)
    if q is None:
        return {"enabled": True, "status": "uninitialized"}

    return {
        "enabled": True,
        "status": q.status.value,
        "remaining_input_tokens": q.remaining_input,
        "remaining_output_tokens": q.remaining_output,
        "initial_input_tokens": q.initial_input_tokens,
        "initial_output_tokens": q.initial_output_tokens,
        "granted_input_tokens": q.granted_input_tokens,
        "granted_output_tokens": q.granted_output_tokens,
        "used_input_tokens": q.used_input_tokens,
        "used_output_tokens": q.used_output_tokens,
    }
