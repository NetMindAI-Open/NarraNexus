"""
@file_name: provider_readiness.py
@author: Bin Liang
@date: 2026-06-01
@description: Framework-level "is this user ready to run right now" check, used
by the edge-triggered recovery of PAUSED_NO_QUOTA jobs (login / quota grant /
preference toggle / provider save).

This sits ABOVE the module layer (it is part of the agent_framework provider
stack, not owned by any Module — 铁律 #3) and is callable by anyone: the job
recovery path, the backend routes, future capabilities.

Two tiers, by design (see 2026-06-01-job-scheduler-resilience-design.md):
- `classify_provider_availability` (in provider_resolver) is the CHEAP static
  verdict used on the hot path (every job pickup / runtime). It must match the
  runtime exactly — that is the oscillation fix.
- `ProviderReadiness.validate` here adds a LIVE connectivity test on top, run
  only at rare edge events where a human action could have just fixed things.
  "Tested OK → recover" gives better UX than re-arming into another failure.

`validate` is intentionally a thin pipeline so it can grow into a hook
environment: today it runs (static gate → live provider test); future readiness
hooks (rate-limit checks, account status, business rules) slot in here without
touching every caller.
"""
from __future__ import annotations

from loguru import logger

from xyz_agent_context.agent_framework.provider_resolver import (
    ProviderAvailability,
    classify_provider_for_user,
    is_runnable,
)


class ProviderReadiness:
    """Readiness facade. `validate` returns (ready, reason)."""

    @staticmethod
    async def validate(user_id: str, db) -> tuple[bool, str]:
        """True iff a run for this user would resolve a *working* provider now.

        Step 1 (static): the same classifier the runtime uses. A non-runnable
        verdict short-circuits — no point live-testing a user with no budget /
        no provider.

        Step 2 (live, USER_OK only): ping the user's agent-slot provider. A
        freshly-configured key that is malformed/revoked would pass the static
        completeness check but fail here, so we don't recover into a failure.
        SYSTEM_OK / SYSTEM_DISABLED skip the live test (the platform's own
        provider, or local passthrough).
        """
        try:
            verdict = await classify_provider_for_user(user_id, db)
        except Exception as e:  # noqa: BLE001 — quota/provider subsystem optional
            logger.debug(f"ProviderReadiness.validate classify failed for {user_id}: {e}")
            return False, "classify_error"

        if not is_runnable(verdict):
            return False, verdict.value
        if verdict != ProviderAvailability.USER_OK:
            return True, verdict.value

        # USER_OK → live-test the agent-slot provider.
        try:
            from xyz_agent_context.agent_framework.user_provider_service import (
                UserProviderService,
            )
            ups = UserProviderService(db)
            cfg = await ups.get_user_config(user_id)
            slot = cfg.slots.get("agent") if cfg and cfg.slots else None
            if slot and slot.provider_id:
                ok, msg = await ups.test_provider(user_id, slot.provider_id)
                return ok, msg
        except Exception as e:  # noqa: BLE001 — never block recovery on a flaky live test
            logger.debug(f"ProviderReadiness live test skipped for {user_id}: {e}")

        return True, verdict.value
