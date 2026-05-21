"""
@file_name: admin_invite.py
@author: NarraNexus
@date: 2026-05-14
@description: Staff-only invite-code management routes.

/codes    — list invite codes (optionally filtered by status) + cap summary
/promote  — waitlisted -> issued; returns the code so an operator can hand
            it to the user via whatever channel (NarraNexus no longer holds
            SMTP credentials — the website is the mailer now)
/revoke   — issued|waitlisted -> revoked (a used code cannot be revoked)

All require `role=staff` on the caller's JWT — same model as admin_quota.py.
Mounted at /api/admin/invite (under /api/admin, which auth.py already lists
in QUOTA_BYPASS_PREFIXES, so it is JWT-gated but skips the quota resolver).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from backend.auth import _is_cloud_mode
from backend.config import settings
from xyz_agent_context.repository import InviteCodeRepository
from xyz_agent_context.schema.invite_code_schema import InviteCode
from xyz_agent_context.utils.db_factory import get_db_client

router = APIRouter(prefix="/api/admin/invite", tags=["admin", "invite"])


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


def _code_to_dict(c: InviteCode) -> dict:
    return {
        "code": c.code,
        "email": c.email,
        "status": c.status,
        "source": c.source,
        # email_sent is no longer maintained by NarraNexus (mailer moved to
        # website). The column stays for forward-compat / future write-back.
        "email_sent": c.email_sent,
        "created_at": str(c.created_at) if c.created_at else None,
        "issued_at": str(c.issued_at) if c.issued_at else None,
        "used_at": str(c.used_at) if c.used_at else None,
        "used_by_user_id": c.used_by_user_id,
    }


class CodeAction(BaseModel):
    code: str = Field(..., min_length=1)


@router.get("/codes")
async def list_codes(request: Request, status: str | None = None) -> dict:
    _require_staff_or_raise(request)
    db = await get_db_client()
    repo = InviteCodeRepository(db)
    codes = await repo.list_all(status=status)
    active = await repo.count_active()
    return {
        "codes": [_code_to_dict(c) for c in codes],
        "count": len(codes),
        "active_count": active,
        "auto_issue_cap": settings.invite_auto_issue_cap,
        "capacity_reached": active >= settings.invite_auto_issue_cap,
    }


@router.post("/promote")
async def promote_code(request: Request, payload: CodeAction) -> dict:
    """Promote a waitlisted code to issued.

    Returns the code + email so an operator can deliver it manually
    (NarraNexus no longer holds SMTP credentials — that responsibility
    moved to the narranexus-website's mailer).
    """
    _require_staff_or_raise(request)
    db = await get_db_client()
    repo = InviteCodeRepository(db)

    promoted = await repo.promote(payload.code)
    if not promoted:
        raise HTTPException(
            status_code=404,
            detail="code not found or not in 'waitlisted' status",
        )

    row = await repo.get_by_code(payload.code)
    logger.info("admin: promoted invite code {}", payload.code)
    return {
        "success": True,
        "code": payload.code,
        "email": row.email if row else None,
        "status": "issued",
    }


@router.post("/revoke")
async def revoke_code(request: Request, payload: CodeAction) -> dict:
    """Revoke an issued or waitlisted code. A used code cannot be revoked."""
    _require_staff_or_raise(request)
    db = await get_db_client()
    repo = InviteCodeRepository(db)

    revoked = await repo.revoke(payload.code)
    if not revoked:
        raise HTTPException(
            status_code=404,
            detail="code not found, or already used/revoked",
        )
    logger.info("admin: revoked invite code {}", payload.code)
    return {"success": True, "code": payload.code, "status": "revoked"}
