"""
@file_name: quota.py
@author: Bin Liang
@date: 2026-04-16
@description: User-facing free-tier balance endpoint.

The path is unchanged (``GET /api/quota/me``) but the meaning is not: since
2026-07-28 the free tier is a USD wallet on the LiteLLM gateway, not a token
counter in our own DB, so this route is a thin read-through to the deploy-side
wallet service.

Three explicit response shapes so the frontend never has to infer "is the
feature on":
  - ``{enabled: false}``                         — local mode / free tier off
  - ``{enabled: true, status: "uninitialized"}`` — no wallet for this user yet
  - ``{enabled: true, status: "active"|"exhausted", …}`` — full balance
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from xyz_agent_context.agent_framework.providers.free_tier import (
    is_free_tier_enabled,
)
from xyz_agent_context.integrations.free_tier.wallet_client import (
    WalletBalance,
    WalletClient,
    WalletError,
    WalletMissing,
)

router = APIRouter(prefix="/api/quota", tags=["quota"])

_DISABLED: dict = {"enabled": False}


def balance_to_dict(balance: WalletBalance) -> dict:
    """Wire shape for a wallet. Shared with the staff top-up route so the two
    can never describe the same wallet differently."""
    return {
        "enabled": True,
        "status": "exhausted" if balance.exhausted else "active",
        "currency": balance.currency,
        "max_budget": balance.max_budget,
        "spend": balance.spend,
        "remaining": balance.remaining,
    }


@router.get("/me")
async def get_my_quota(request: Request) -> dict:
    if not is_free_tier_enabled():
        return _DISABLED

    client = WalletClient.from_settings()
    if client is None:
        # Flag on but the service isn't wired — report "off" rather than an
        # error: the settings panel must still render, and the misconfiguration
        # is already logged loudly by the provisioner.
        return _DISABLED

    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        return balance_to_dict(await client.balance(user_id))
    except WalletMissing:
        # Provisioning has not run yet (or ran before the feature was enabled).
        # Not an error — the next login provisions it.
        return {"enabled": True, "status": "uninitialized"}
    except WalletError as e:
        logger.warning(f"[quota] balance lookup failed for {user_id}: {e!r}")
        raise HTTPException(
            status_code=503, detail="Balance service unavailable, try again"
        ) from e
