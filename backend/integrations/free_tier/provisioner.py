"""
@file_name: provisioner.py
@author: Bin Liang
@date: 2026-07-28
@description: Give a new user their free-tier wallet, as an ordinary provider.

The whole free tier is this function: open a $10 wallet on the gateway, then
register the wallet key as a normal two-row provider card and bind the slots.
After it runs there is nothing special about the user — every later code path
resolves them like anyone with their own NetMind card.

Deliberately mirrors ``integrations/netmind/netmind_provisioner.py`` (which does
the same dance for the user's OWN NetMind account) down to the lock and the
register-vs-activate split: the two are the same operation with a different
source of key, and keeping them shaped alike is what stops one from growing a
subtlety the other lacks.

Ordering rule that matters: dedup happens BEFORE the wallet call, and the
wallet call is idempotent on the service side as well. Two logins racing must
never produce two budgets for one user.
"""
from __future__ import annotations

import asyncio

from loguru import logger

from xyz_agent_context.integrations.free_tier.wallet_client import (
    WalletClient,
    WalletError,
)
from xyz_agent_context.agent_framework.providers.free_tier import (
    FREE_TIER_SOURCE,
    free_tier_default_models,
    is_free_tier_enabled,
)

# Per-user in-process serialisation of dedup + provision + onboard. Same
# single-worker caveat as the NetMind provisioner: before running multiple
# backend workers this must become a distributed guard.
_locks: dict[str, asyncio.Lock] = {}


def _lock(user_id: str) -> asyncio.Lock:
    lock = _locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[user_id] = lock
    return lock


async def ensure_free_tier_provider(
    user_id: str, *, activate_if_fresh: bool = True
) -> bool:
    """Ensure the user has a free-tier card. Returns True if one was created.

    No-ops (returns False) when the free tier is off, not configured, or the
    user already has the card. Raises only on a genuine failure the caller may
    want to surface; the login path swallows everything.
    """
    if not is_free_tier_enabled():
        return False

    client = WalletClient.from_settings()
    if client is None:
        logger.warning(
            "[free-tier] FREE_TIER_ENABLED=true but the wallet service is not "
            "configured (FREE_TIER_WALLET_API_URL / _TOKEN) — skipping"
        )
        return False

    from xyz_agent_context.agent_framework.providers.resolver import (
        is_user_config_complete,
    )
    from xyz_agent_context.agent_framework.providers.user_service import (
        UserProviderService,
    )
    from xyz_agent_context.utils.db.db_factory import get_db_client

    async with _lock(user_id):
        db = await get_db_client()
        # Dedup BEFORE touching the wallet service: one free card per user, and
        # never a second budget across repeated logins or parallel tabs.
        existing = await db.get_one(
            "user_providers", {"user_id": user_id, "source": FREE_TIER_SOURCE}
        )
        if existing:
            return False

        wallet = await client.provision(user_id)
        if wallet.api_key is None:
            # The service says the wallet already existed, but this user has no
            # provider row — the key was minted and then lost (a crash between
            # the two steps of an earlier attempt). The secret is
            # unrecoverable by design (the gateway shows it once), so the only
            # cure is a fresh wallet; say so loudly rather than half-fixing it.
            logger.error(
                f"[free-tier] wallet for {user_id} exists but no provider row "
                f"does — its key is unrecoverable; delete the gateway key "
                f"'free::{user_id}' so the next login mints a new one"
            )
            return False

        svc = UserProviderService(db)
        agent_model, helper_model = free_tier_default_models()
        # Seed the card with what the GATEWAY serves, not the upstream
        # catalogue the netmind card would inherit — see served_models(). A
        # lookup failure is not fatal: fall back to the inherited list rather
        # than block provisioning over a dropdown.
        try:
            models = await client.served_models() or None
        except WalletError as e:  # noqa: BLE001 — cosmetic, never fatal
            logger.warning(f"[free-tier] model list lookup failed for {user_id}: {e!r}")
            models = None
        # Activate (bind slots) only when the user has nothing usable yet. A
        # user who already configured their own provider is never hijacked —
        # the free card is merely made available to switch to.
        activate = activate_if_fresh and not is_user_config_complete(
            await svc.get_user_config(user_id)
        )
        await svc.onboard_one_key(
            user_id,
            wallet.api_key,
            provider_type=FREE_TIER_SOURCE,
            activate=activate,
            models=models,
        )
        logger.info(
            f"[free-tier] provisioned wallet for {user_id} "
            f"({wallet.balance.max_budget:.2f} {wallet.balance.currency}, "
            f"activate={activate}, agent={agent_model}, helper={helper_model})"
        )
        return True
