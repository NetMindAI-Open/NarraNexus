"""
@file_name: warn.py
@author: Bin Liang
@date: 2026-08-19
@description: Admin-only sensitive-operation user warning.

The progressive-response layer before hard enforcement: when the private
monitor decides a soft (unproven) signal warrants a nudge, it POSTs here to
push ONE generic warning to the user. Gated on the platform ``admin_secret_key``
via ``X-Admin-Secret`` — the SAME lock as suspend/reinstate; an executor or an
agent has no such secret.

Security shape (do not weaken):
  * The user-facing wording is a FIXED generic constant (``SENSITIVE_OP_WARNING``)
    written here regardless of any input, so no rule / threshold / evidence can
    leak to the user. The caller's ``category`` is OPAQUE and goes ONLY into the
    ban_audit trail, never into the user notification.
  * Best-effort dedup within ``_DEDUP_WINDOW_SEC``: a recent ``abuse_warning``
    for the same user short-circuits to ``already=True`` without a second row.
    This collapses a SEQUENTIAL retry (the caller re-POSTs after seeing/ not
    seeing a response). It is a read-then-write with no unique constraint behind
    it, so it is NOT atomic: two truly concurrent POSTs can both pass the check
    and both insert. That is an accepted cost here — the worst case is one
    duplicate generic warning notification, which is harmless (unlike a
    duplicate enforcement row); we do not pay for a unique index to prevent it.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

# Re-exported so the admin secret can be overridden per-test via ``mod.settings``
# (this module's namespace); the shared ``require_admin_secret`` helper reads the
# same ``settings`` singleton object.
from narranexus.platform.settings import settings  # noqa: F401
from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils.db.schema_registry import varchar_width
from narranexus.platform.utils.timezone import coerce_utc, utc_now
from narranexus.platform.repository.user_repository import UserRepository
from narranexus.platform.repository.ban_audit_repository import ACTION_WARN, BanAuditRepository

from ._admin_secret import admin_secret_dependency

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Fixed, generic, rule-agnostic wording. English only per binding rule #1 (no
# non-English strings in code); the frontend bell may localise on ``code``.
SENSITIVE_OP_WARNING = (
    "We detected activity on your account that may violate our Terms of Use "
    "(for example, attempting to transmit credentials externally or accessing "
    "sensitive environment data). Please stop immediately. Continued activity "
    "of this kind may result in your account being restricted or suspended."
)

# Idempotency window: a second warn for the same user inside this window is a
# no-op success. The window must stay consistent with the caller-side de-dup
# window (cross-repo convention); kept here so the endpoint is independently
# idempotent against retries.
_DEDUP_WINDOW_SEC = 6 * 3600

# Derived from the schema registry (single source of truth) rather than a
# hardcoded width that could drift from the DDL. ``reason`` (which carries
# ``category``) is MEDIUMTEXT, not a VARCHAR, so it has no column width to derive
# — its bound is a runaway-payload guard set below.
_ACTOR_MAX_LEN = varchar_width("ban_audit", "actor")


class WarnRequest(BaseModel):
    user_id: str
    # OPAQUE, audit-only. Never reaches the user notification. The 4096 bound is a
    # runaway-payload guard (keep a stray blob out of the audit row), NOT a column
    # width: ``ban_audit.reason`` is MEDIUMTEXT, so varchar_width would raise on it.
    category: Optional[str] = Field(default=None, max_length=4096)
    # Audit-only actor label. Bounded to ban_audit.actor's column width (derived
    # from the schema registry via _ACTOR_MAX_LEN); an over-long actor would 1406
    # the audit insert and lose the trail. This is an audit breadcrumb, not the
    # load-bearing enforcement row, so a 422 on a malformed actor is acceptable
    # (unlike the misuse endpoint).
    actor: Optional[str] = Field(default=None, max_length=_ACTOR_MAX_LEN)


class WarnResponse(BaseModel):
    warned: bool
    already: bool


@router.post("/warn-user", response_model=WarnResponse)
async def warn_user(
    request: WarnRequest,
    _: None = Depends(admin_secret_dependency),
) -> WarnResponse:
    db = await get_db_client()
    user = await UserRepository(db).get_user(request.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    # Idempotency: a recent warning short-circuits without a second row. We only
    # need the newest row's timestamp to decide, so read just that one column of
    # the single latest row — not every abuse_warning row in full. The payload
    # column is MEDIUMTEXT, so a full-row scan of a chatty user's history would
    # drag the whole notification body back for nothing.
    existing = await db.get(
        "user_notifications",
        {"user_id": request.user_id, "kind": "abuse_warning"},
        limit=1,
        order_by="created_at DESC",
        fields=["created_at"],
    )
    now = utc_now()
    cutoff = now - timedelta(seconds=_DEDUP_WINDOW_SEC)
    recent = [
        r
        for r in (existing or [])
        if (ts := coerce_utc(r.get("created_at"))) is not None and ts >= cutoff
    ]
    if recent:
        logger.info(
            f"[warn-user] user={request.user_id} already (within dedup window)"
        )
        return WarnResponse(warned=True, already=True)

    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")
    await db.insert(
        "user_notifications",
        {
            "user_id": request.user_id,
            "kind": "abuse_warning",
            "payload": json.dumps(
                {
                    "code": "sensitive_operation_warning",
                    "message": SENSITIVE_OP_WARNING,
                },
                ensure_ascii=False,
            ),
            "severity": "warning",
            "read_at": None,
            "created_at": now_iso,
        },
    )
    # Audit: category is OPAQUE (recorded verbatim in ``reason``), never shown to
    # the user. The precise rule/evidence stays with the private caller, never
    # in the user-facing payload.
    await BanAuditRepository(db).record(
        request.user_id,
        ACTION_WARN,
        reason=request.category,
        actor=request.actor,
        prev_status=user.status.value,
    )
    logger.info(
        f"[warn-user] user={request.user_id} category={request.category or '-'} "
        f"actor={request.actor or '-'}"
    )
    return WarnResponse(warned=True, already=False)
