"""
@file_name: quota.py
@author: Bin Liang
@date: 2026-04-16
@description: Staff-only free-tier wallet management.

``/topup``  — add USD headroom to a user's wallet. Raises the ceiling; never
              rewrites what was already spent, so consumption stays auditable.
``/init``   — provision the wallet + free-tier provider card for a user who
              missed it (feature enabled after they first logged in, or a
              provisioning failure). Idempotent.

Both require ``role=staff`` on the caller's JWT.

Token grants are gone with the token quota: the wallet's unit is dollars, and
the gateway is the ledger.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from backend.auth import _is_cloud_mode
from backend.routes.quota import balance_to_dict
from xyz_agent_context.agent_framework.providers.free_tier import (
    is_free_tier_enabled,
)
from xyz_agent_context.integrations.free_tier.wallet_client import (
    WalletClient,
    WalletError,
    WalletMissing,
)

router = APIRouter(prefix="/api/admin/quota", tags=["admin", "quota"])


class TopupRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    amount_usd: float = Field(..., gt=0)
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


async def _resolve_user_id_or_404(request: Request, target_user_id: str) -> None:
    """Confirm the target exists in `users`. Reuses the UserRepository on
    app.state (populated by lifespan)."""
    user_repo = getattr(request.app.state, "user_repository", None)
    if user_repo is None:
        raise HTTPException(status_code=503, detail="user repository not wired")
    user = await user_repo.get_user(target_user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")


def _client_or_503() -> WalletClient:
    if not is_free_tier_enabled():
        raise HTTPException(status_code=503, detail="free tier is disabled")
    client = WalletClient.from_settings()
    if client is None:
        raise HTTPException(status_code=503, detail="wallet service not configured")
    return client


@router.post("/topup")
async def topup(request: Request, payload: TopupRequest) -> dict:
    _require_staff_or_raise(request)
    await _resolve_user_id_or_404(request, payload.user_id)
    client = _client_or_503()

    try:
        balance = await client.topup(payload.user_id, payload.amount_usd)
    except WalletMissing as e:
        raise HTTPException(
            status_code=404,
            detail="user has no wallet — run /api/admin/quota/init first",
        ) from e
    except WalletError as e:
        logger.warning(f"[admin-quota] topup failed for {payload.user_id}: {e!r}")
        raise HTTPException(status_code=503, detail="wallet service unavailable") from e

    # Edge-triggered recovery: fresh headroom can make the user runnable again —
    # revive their PAUSED_NO_QUOTA jobs in the background (non-blocking).
    from xyz_agent_context.module.job_module.job_recovery import (
        schedule_user_no_quota_rearm,
    )
    schedule_user_no_quota_rearm(payload.user_id)
    return balance_to_dict(balance)


@router.post("/init")
async def init(request: Request, payload: InitRequest) -> dict:
    _require_staff_or_raise(request)
    await _resolve_user_id_or_404(request, payload.user_id)
    client = _client_or_503()

    from backend.integrations.free_tier.provisioner import (
        ensure_free_tier_provider,
    )

    try:
        created = await ensure_free_tier_provider(payload.user_id)
        balance = await client.balance(payload.user_id)
    except WalletError as e:
        logger.warning(f"[admin-quota] init failed for {payload.user_id}: {e!r}")
        raise HTTPException(status_code=503, detail="wallet service unavailable") from e

    return {"created": created, **balance_to_dict(balance)}
