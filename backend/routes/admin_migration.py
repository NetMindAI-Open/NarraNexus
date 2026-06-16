"""
@file_name: admin_migration.py
@author: NarraNexus
@date: 2026-06-12
@description: Admin-only single-user identity migration.

POST /api/admin/migrate-identity rekeys one legacy user_id to a NetMind
userSystemCode by wrapping the shared identity_migration kernel (same code the
offline batch script uses — 铁律 8). Intended to be called per user by a batch
script during a stop-the-world migration window, and reused for ad-hoc rebinds.

High-risk: it can move ANY user's data onto ANY hex, so it is gated on the
platform `admin_secret_key` via an X-Admin-Secret header — never a user JWT,
never open. The migration itself assumes the target user is not active (the
run happens with the stack stopped), so there is no concurrency handling.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from loguru import logger
from pydantic import BaseModel

from xyz_agent_context.settings import settings
from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.services.identity_migration import execute_migration

router = APIRouter(prefix="/api/admin", tags=["admin"])


class MigrateIdentityRequest(BaseModel):
    from_user_id: str
    to_power_hex: str
    power_email: Optional[str] = None
    power_display_name: Optional[str] = None


class MigrateIdentityResponse(BaseModel):
    status: str
    merged: bool
    users_migrated: int
    rows_updated: int
    dirs_renamed: int


def _require_admin_secret(provided: str) -> None:
    """Gate the endpoint on the platform admin secret.

    No configured secret in cloud-grade deployments is a misconfiguration, not
    an open door — refuse rather than allow. A wrong / missing header is 403.
    """
    expected = (settings.admin_secret_key or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="admin secret not configured")
    if not provided or provided.strip() != expected:
        raise HTTPException(status_code=403, detail="invalid admin secret")


@router.post("/migrate-identity", response_model=MigrateIdentityResponse)
async def migrate_identity(
    request: MigrateIdentityRequest,
    x_admin_secret: str = Header(default=""),
) -> MigrateIdentityResponse:
    _require_admin_secret(x_admin_secret)

    if len(request.to_power_hex) != 32:
        raise HTTPException(
            status_code=400,
            detail="to_power_hex must be a 32-char NetMind userSystemCode",
        )

    db = await get_db_client()
    stats = await execute_migration(
        db,
        {request.from_user_id: request.to_power_hex},
        base_working_path=settings.base_working_path,
    )

    # Sync the human display fields onto the target row (NetMind login also
    # mirrors these on next login; doing it here keeps the row correct
    # immediately after an offline batch migration).
    if request.power_email or request.power_display_name:
        sets, params = [], []
        if request.power_display_name:
            sets.append("display_name = %s")
            params.append(request.power_display_name)
        if request.power_email:
            sets.append("email = %s")
            params.append(request.power_email)
        params.append(request.to_power_hex)
        await db.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE BINARY user_id = %s",
            params=tuple(params),
            fetch=False,
        )

    logger.info(
        "migrate-identity %s -> %s: %s",
        request.from_user_id, request.to_power_hex, stats,
    )
    return MigrateIdentityResponse(
        status="ok",
        merged=stats["users_merged"] > 0,
        users_migrated=stats["users_migrated"],
        rows_updated=stats["rows_updated"],
        dirs_renamed=stats["dirs_renamed"],
    )
