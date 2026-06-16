"""
@file_name: __init__.py
@date: 2026-06-08
@description: Public analytics API.

track() / identify_user() are the only entry points capture sites use.
Both are async (opt-out lookup hits the DB), best-effort, and NEVER raise.
get_analytics() returns the active sink, gated by env + surface:
NullSink unless NARRA_ANALYTICS_ENABLED=true AND POSTHOG_API_KEY set AND
surface != cloud (cloud deferred this phase).
"""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from typing import Optional

from loguru import logger

from xyz_agent_context.analytics.base import AnalyticsClient
from xyz_agent_context.analytics._impl.null_sink import NullSink
from xyz_agent_context.analytics.surface import SURFACE

__all__ = [
    "AnalyticsClient", "get_analytics", "track", "identify_user",
    "shutdown_analytics",
]

# Pseudonymize the user id before it leaves the process: PostHog only ever
# sees a stable hash, never the raw (often human-named) local user_id. This
# is pseudonymization, not anonymization — the salt lives in source, so a
# determined attacker with a username guess-list could reverse it — but it
# keeps real names out of the analytics dashboard, which is the goal.
# NOTE: the opt-out lookup still uses the RAW user_id (it only queries the
# local DB; nothing leaves the machine there).
_DISTINCT_ID_SALT = "narranexus.analytics.v1"


def _hash_distinct_id(user_id: str) -> str:
    return hashlib.sha256(
        f"{_DISTINCT_ID_SALT}:{user_id}".encode()
    ).hexdigest()[:32]


def _build_sink() -> AnalyticsClient:
    if (os.environ.get("NARRA_ANALYTICS_ENABLED", "true").lower() != "true"):
        return NullSink()
    if SURFACE == "cloud":  # deferred this phase
        return NullSink()
    key = os.environ.get("POSTHOG_API_KEY")
    if not key:
        return NullSink()
    from xyz_agent_context.analytics._impl.posthog_sink import PostHogSink
    return PostHogSink(api_key=key, host=os.environ.get("POSTHOG_HOST"))


@lru_cache(maxsize=1)
def _get_sink_cached() -> AnalyticsClient:
    return _build_sink()


def get_analytics() -> AnalyticsClient:
    return _get_sink_cached()


async def _opted_out(user_id: str) -> bool:
    try:
        from xyz_agent_context.utils import get_db_client
        from xyz_agent_context.repository.user_settings_repository import (
            UserSettingsRepository,
        )
        repo = UserSettingsRepository(await get_db_client())
        return await repo.is_analytics_opted_out(user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[analytics] opt-out lookup failed for {user_id}: {e}")
        return False  # default: tracking on


async def track(*, user_id: str, event: str,
                properties: Optional[dict] = None) -> None:
    try:
        if not user_id:
            return
        if await _opted_out(user_id):
            return
        props = dict(properties or {})
        props.setdefault("surface", SURFACE)
        get_analytics().capture(
            distinct_id=_hash_distinct_id(user_id), event=event, properties=props
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[analytics] track {event} failed: {e}")


async def identify_user(*, user_id: str, traits: Optional[dict] = None) -> None:
    try:
        if not user_id or await _opted_out(user_id):
            return
        t = dict(traits or {})
        t.setdefault("surface", SURFACE)
        get_analytics().identify(distinct_id=_hash_distinct_id(user_id), traits=t)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[analytics] identify {user_id} failed: {e}")


async def shutdown_analytics() -> None:
    try:
        get_analytics().flush()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[analytics] shutdown flush failed: {e}")
