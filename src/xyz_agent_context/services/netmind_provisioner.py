"""
@file_name: netmind_provisioner.py
@author: NarraNexus
@date: 2026-07-10
@description: Auto-register a user's NetMind.AI Power account as a provider.

Cloud login IS NetMind login, so a signed-in user's NetMind credits should be
available without a manual "use this account" button. This module holds the ONE
place that mints a NetMind inference key and registers the dual netmind provider,
shared by:
  - the login handler (fire-and-forget on every NetMind login), and
  - the explicit POST /api/providers/use-subscription route.

Register vs activate (the key split):
  - REGISTER always (if the user has no netmind provider yet): mint + create the
    two netmind provider rows so a NetMind card appears in LLM Providers.
  - ACTIVATE (bind agent/helper slots) only when the user has NO complete active
    config yet. A user who already configured their own provider is NOT hijacked —
    the NetMind card is merely available to switch to.

Gated by ``settings.netmind_use_subscription_enabled`` (off = no-op). Never logs
the loginToken or the minted apitoken. The per-user in-process lock lives here
(moved from the route) so concurrent logins/clicks for one user can't double-mint.

Pre-flip TODO (unchanged): before enabling the flag in a MULTI-worker deploy,
replace the in-process lock with a distributed guard covering every
netmind-source creator (this + add_provider/onboard).
"""

from __future__ import annotations

import asyncio

from loguru import logger

# Per-user in-process serialize of dedup + mint + onboard (one worker). Unbounded
# by design while the feature is single-worker + flag-gated; bound it before flip.
_locks: dict[str, asyncio.Lock] = {}


def _lock(user_id: str) -> asyncio.Lock:
    lock = _locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[user_id] = lock
    return lock


def _strip_bearer(token: str) -> str:
    token = (token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


async def ensure_netmind_provider(
    user_id: str,
    netmind_token: str,
    *,
    activate_if_fresh: bool = True,
) -> bool:
    """Register (and maybe activate) the user's NetMind provider if missing.

    Returns True if a provider was newly minted+registered this call; False on a
    no-op (feature flag off, no token, or a netmind provider already exists).
    Raises the NetmindKeyClient errors (KeyAuthError / KeyUpstreamError) or a
    ValueError from onboarding on genuine failure — AFTER best-effort revoking any
    key it minted, so no money-spending orphan lingers. Callers decide fatal vs
    non-fatal (the login task swallows; the route maps to HTTP).
    """
    from xyz_agent_context.settings import settings

    if not settings.netmind_use_subscription_enabled:
        return False
    token = _strip_bearer(netmind_token)
    if not token:
        return False

    from xyz_agent_context.services.netmind_key_client import NetmindKeyClient
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.agent_framework.user_provider_service import (
        UserProviderService,
    )
    from xyz_agent_context.agent_framework.provider_resolver import (
        _is_user_config_complete,
    )

    key_client = NetmindKeyClient(base_url=settings.netmind_key_api_base)

    async with _lock(user_id):
        db = await get_db_client()
        # Dedup BEFORE minting — one netmind provider per user; never a duplicate
        # money-spending key across repeated logins / tabs.
        existing = await db.get_one(
            "user_providers", {"user_id": user_id, "source": "netmind"}
        )
        if existing:
            # Backfill the account id/email for pre-existing rows that never had
            # it captured (rows minted before this feature, incl. the incident
            # users). Runs on the next login of an existing user — the whole
            # point is to reach the people who ALREADY have a netmind key, not
            # only brand-new ones. Only touches rows still missing it, so it's a
            # one-time cost. Best-effort; never fails the login path.
            if not existing.get("netmind_account_id"):
                await _capture_netmind_account(db, user_id, token)
            return False

        minted = await key_client.create_key(token)  # KeyAuthError / KeyUpstreamError
        try:
            svc = UserProviderService(db)
            # Activate (bind slots) ONLY when the user has no complete active
            # config; otherwise register-only (respect their own provider).
            activate = activate_if_fresh and not _is_user_config_complete(
                await svc.get_user_config(user_id)
            )
            await svc.onboard_one_key(
                user_id,
                minted.apitoken,
                provider_type="netmind",
                inference_base=settings.netmind_inference_base,
                activate=activate,
            )
            # Capture WHICH NetMind account this key belongs to (best-effort).
            await _capture_netmind_account(db, user_id, token)
            logger.info(
                f"[netmind-provisioner] registered netmind provider for "
                f"{user_id} (activate={activate})"
            )
            return True
        except Exception:
            # Onboard failed after minting — revoke the orphan key (best-effort,
            # never raises) then re-raise the original error.
            await key_client.delete_key(token, minted.token_id)
            raise


async def _capture_netmind_account(db, user_id: str, token: str) -> None:
    """Stamp WHICH NetMind account the just-minted key belongs to onto the
    user's ``source='netmind'`` provider rows, so Settings can show it and a
    user with several keys from one broke account tops up the right one
    (upstream incident).

    The login JWT is in hand here — ``verify_token`` → ``user_system_code`` +
    email — but we store ONLY the non-secret account id/email, never the JWT.
    Best-effort: a capture failure must never fail provisioning (the key is
    already minted + onboarded). ``onboard_one_key`` may create dual linked rows
    (anthropic+openai), so this stamps ALL of the user's netmind rows.
    """
    try:
        from xyz_agent_context.services.netmind_auth_client import NetmindAuthClient
        who = await NetmindAuthClient().verify_token(token)
        await db.update(
            "user_providers",
            {"user_id": user_id, "source": "netmind"},
            {
                "netmind_account_id": who.user_system_code,
                "netmind_account_email": who.email,
            },
        )
        logger.info(
            f"[netmind-provisioner] captured netmind account for {user_id} "
            f"(account={who.user_system_code})"
        )
    except Exception as e:  # noqa: BLE001 — capture is best-effort
        logger.warning(
            f"[netmind-provisioner] account capture failed for {user_id} "
            f"(provisioning still succeeded): {e}"
        )


def schedule_ensure_netmind_provider(user_id: str, netmind_token: str) -> None:
    """Fire-and-forget the auto-register off the login path (non-fatal).

    Login must never block on or be failed by NetMind minting (incident lesson
    #2: a bare create_task swallows exceptions at GC — attach a done-callback).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (shouldn't happen in the request path) — skip silently

    task = loop.create_task(
        ensure_netmind_provider(user_id, netmind_token, activate_if_fresh=True)
    )

    def _done(t: asyncio.Task) -> None:
        try:
            t.result()
        except Exception as e:  # noqa: BLE001 — background side effect, never fatal
            logger.warning(
                f"[netmind-provisioner] background auto-register for {user_id} "
                f"failed (non-fatal): {e}"
            )

    task.add_done_callback(_done)
