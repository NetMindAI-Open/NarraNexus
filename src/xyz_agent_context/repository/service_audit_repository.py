"""
@file_name: service_audit_repository.py
@author: Bin Liang
@date: 2026-05-29
@description: Generic L2 audit log for long-running background services.

Why this exists
===============
JobTrigger and ModulePoller run for hours/days on EC2. When their poll
coroutine silently wedges (an ``await`` that never returns), L1 ("process
alive") still reports healthy while no work happens — the exact zombie
incident lesson #4 warns about. Application logs get rotated/wiped on
``docker restart``; the DB does not (lesson #5). This table is the L2
trail: a stale-or-missing heartbeat row for a service reveals a stuck
loop that ``ps`` / ``is_alive()`` cannot.

Generalised from ``LarkTriggerAuditRepository`` (channel-specific) so any
service shares one table, keyed by ``service`` name. Event vocabulary:
started / stopped / heartbeat / error.

**Best-effort writes** — ``record`` NEVER raises. The observer must not
break the observed; losing an audit row beats stalling a poller.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

EVENT_STARTED = "started"
EVENT_STOPPED = "stopped"
EVENT_HEARTBEAT = "heartbeat"
EVENT_ERROR = "error"


class ServiceAuditRepository:
    """Append-only lifecycle log shared by background services."""

    TABLE = "service_audit"

    def __init__(self, db_client):
        # Untyped on purpose (mirrors LarkTriggerAuditRepository): the
        # async DB client is injected; importing its type here would only
        # add a load-order coupling for no benefit.
        self._db = db_client

    @staticmethod
    def _to_detail(detail: Any) -> Optional[str]:
        if detail is None or isinstance(detail, str):
            return detail
        try:
            return json.dumps(detail, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            return str(detail)

    async def record(
        self,
        service: str,
        event_type: str,
        detail: Any = None,
    ) -> None:
        """Best-effort audit write. Never raises into the caller."""
        try:
            await self._db.insert(
                self.TABLE,
                {
                    "service": service,
                    "event_type": event_type,
                    "detail": self._to_detail(detail),
                },
            )
        except Exception as e:  # noqa: BLE001 — audit writes are advisory
            logger.warning(
                f"ServiceAudit write failed ({service}/{event_type}): "
                f"{type(e).__name__}: {e} (row dropped; audit is advisory)"
            )

    async def recent(
        self,
        service: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return recent audit rows, newest first; optionally filtered."""
        try:
            filters: dict[str, Any] = {}
            if service:
                filters["service"] = service
            if event_type:
                filters["event_type"] = event_type
            rows = await self._db.get(self.TABLE, filters)
            rows.sort(key=lambda r: r.get("id", 0), reverse=True)
            return rows[:limit]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ServiceAudit recent() failed: {type(e).__name__}: {e}")
            return []

    async def last_heartbeat(self, service: str) -> Optional[dict]:
        """Most recent heartbeat row for a service, or None — the single
        query an L2 health check needs to answer 'is this loop alive?'."""
        rows = await self.recent(service=service, event_type=EVENT_HEARTBEAT, limit=1)
        return rows[0] if rows else None
