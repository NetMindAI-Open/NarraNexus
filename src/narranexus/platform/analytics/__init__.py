"""
@file_name: __init__.py
@date: 2026-06-08
@description: First-party product analytics persistence.

track() is the only entry point capture sites use. It is async because the
opt-out lookup and event insert hit the database. Events never leave the
NarraNexus database for the current surface: cloud writes RDS; local and
desktop write their local SQLite database. There is deliberately no vendor
telemetry sink here.

Note: third-party WEB analytics on the cloud site is a SEPARATE, client-side
system loaded by frontend/src/lib/analytics/webAnalytics.ts (Google Tag Manager
only, event-only, gated on the same per-user opt-out this module reads). The
two are intentionally distinct — this backend sink never sends to a vendor;
do not "enable a cloud vendor sink" here on the assumption that cloud has none.
"""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from narranexus.platform.analytics.surface import SURFACE
from narranexus.platform.repository.product_analytics_repository import (
    ProductAnalyticsRepository,
)

__all__ = ["track"]


async def _opted_out(user_id: str) -> bool:
    try:
        from narranexus.platform.utils import get_db_client
        from narranexus.platform.repository.user_settings_repository import (
            UserSettingsRepository,
        )
        repo = UserSettingsRepository(await get_db_client())
        return await repo.is_analytics_opted_out(user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[analytics] opt-out lookup failed for {user_id}: {e}")
        return False  # default: tracking on


async def track(*, user_id: str, event: str,
                properties: Optional[dict[str, Any]] = None,
                event_id: Optional[str] = None,
                occurred_at: Optional[str] = None) -> None:
    try:
        if not user_id:
            return
        if await _opted_out(user_id):
            return
        props = dict(properties or {})
        props.setdefault("surface", SURFACE)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[analytics] prepare {event} failed: {e}")
        return
    try:
        await _persist_product_event(
            user_id=user_id,
            event=event,
            event_id=event_id,
            properties=props,
            occurred_at=occurred_at,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[analytics] persist {event} failed: {e}")


async def _persist_product_event(*, user_id: str, event: str,
                                 event_id: Optional[str],
                                 properties: dict[str, Any],
                                 occurred_at: Optional[str] = None) -> None:
    """Delegate database persistence to the product analytics repository."""
    from narranexus.platform.utils import get_db_client
    repository = ProductAnalyticsRepository(await get_db_client())
    await repository.record(
        user_id=user_id,
        event=event,
        event_id=event_id,
        properties=properties,
        occurred_at=occurred_at,
    )
