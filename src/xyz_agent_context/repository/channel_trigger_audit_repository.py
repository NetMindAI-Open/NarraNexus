"""
@file_name: channel_trigger_audit_repository.py
@date: 2026-05-08
@description: Generic IM channel trigger audit log.

Generalisation of `LarkTriggerAuditRepository` keyed on a `channel`
column so all IM triggers can write to one observable table. Same
contract as the Lark version:

- ``append(...)`` — best-effort write. NEVER raises. Losing an audit
  row is always preferable to stalling real user traffic.
- ``cleanup_older_than_days(n)`` — bounded retention, called from the
  trigger's daily cleanup tick.
- ``recent(...)`` / ``count_by_type(...)`` — query helpers used by
  /healthz endpoints and post-incident review.

Phase 1 ships this alongside the existing Lark-specific repo. Phase 2
will redirect Lark writes here and drop the old repo as part of the
trigger refactor.

Event-type constants live in ``xyz_agent_context.channel.channel_audit_events``.
This module re-exports the most common ones for caller convenience so
``from .channel_trigger_audit_repository import EVENT_INGRESS_PROCESSED``
works the same way the Lark version did.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger

# Re-export the canonical constants so callers don't need a second import.
from xyz_agent_context.channel.channel_audit_events import (
    EVENT_INGRESS_PROCESSED,
    EVENT_INGRESS_DROPPED_DEDUP,
    EVENT_INGRESS_DROPPED_HISTORIC,
    EVENT_INGRESS_DROPPED_ECHO,
    EVENT_INGRESS_DROPPED_UNBOUND,
    EVENT_DEDUP_FAIL_OPEN,
    EVENT_DEBOUNCE_MERGED,
    EVENT_SUBSCRIBER_STARTED,
    EVENT_SUBSCRIBER_STOPPED,
    EVENT_TRANSPORT_CONNECTED,
    EVENT_TRANSPORT_DISCONNECTED,
    EVENT_TRANSPORT_BACKOFF,
    EVENT_WORKER_ERROR,
    EVENT_WORKER_TIMEOUT,
    EVENT_INBOX_WRITE_FAILED,
    EVENT_HEARTBEAT,
)


def _event_time_str(value: Any) -> str:
    """
    Normalise an event_time cell to a sortable ISO string.

    sqlite returns ``datetime`` objects; mysql returns strings.
    Comparisons must be type-uniform.
    """
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value or "")


class ChannelTriggerAuditRepository:
    """Append-only multi-channel lifecycle log."""

    TABLE = "channel_trigger_audit"

    def __init__(self, channel: str, db_client):
        if not channel:
            raise ValueError("channel must be a non-empty string")
        self._channel = channel
        self._db = db_client

    @property
    def channel(self) -> str:
        return self._channel

    async def append(
        self,
        event_type: str,
        *,
        message_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        app_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        sender_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Best-effort insert of one audit row. Never raises.

        ``details`` is serialised to JSON so callers can stash arbitrary
        debug context (backoff seconds, uptime, error class, ...) without
        schema changes.
        """
        now = datetime.now(timezone.utc).isoformat(sep=" ")
        row = {
            "channel": self._channel,
            "event_time": now,
            "event_type": event_type,
            "message_id": message_id or "",
            "agent_id": agent_id or "",
            "app_id": app_id or "",
            "chat_id": chat_id or "",
            "sender_id": sender_id or "",
            "details": json.dumps(details or {}, default=str),
        }
        try:
            await self._db.insert(self.TABLE, row)
        except Exception as e:  # noqa: BLE001 — audit writes are best-effort
            logger.warning(
                f"ChannelTriggerAuditRepository.append({self._channel}, {event_type}): "
                f"{type(e).__name__}: {e} (row dropped; audit is advisory only)"
            )

    async def recent(
        self,
        limit: int = 100,
        *,
        event_type: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> list[dict]:
        """Newest-first slice of THIS channel's log, optionally filtered."""
        filters: dict[str, Any] = {"channel": self._channel}
        if event_type:
            filters["event_type"] = event_type
        if agent_id:
            filters["agent_id"] = agent_id
        rows = await self._db.get(self.TABLE, filters)
        rows.sort(key=lambda r: _event_time_str(r.get("event_time")), reverse=True)
        return rows[:limit]

    async def count_by_type(self, since_hours: int = 1) -> dict[str, int]:
        """
        Summary for /healthz: event_type -> count over the last N hours.

        Implemented as a fetch-then-count (not a GROUP BY) because the
        underlying AsyncDatabaseClient API is filter-based and this stays
        portable across sqlite + mysql without hand-written SQL.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=since_hours)
        ).isoformat(sep=" ")
        rows = await self._db.get(self.TABLE, {"channel": self._channel})
        counts: dict[str, int] = {}
        for r in rows:
            if _event_time_str(r.get("event_time")) < cutoff:
                continue
            et = r.get("event_type", "")
            counts[et] = counts.get(et, 0) + 1
        return counts

    async def cleanup_older_than_days(self, days: int) -> int:
        """
        Delete rows for THIS channel older than ``days`` days.

        Per-channel scoping ensures one channel's incident review window
        cannot drag down another's retention.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat(sep=" ")
        try:
            rows = await self._db.get(self.TABLE, {"channel": self._channel})
            to_delete = [
                r["id"] for r in rows
                if _event_time_str(r.get("event_time")) < cutoff
            ]
            for row_id in to_delete:
                await self._db.delete(self.TABLE, {"id": row_id})
            return len(to_delete)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"ChannelTriggerAuditRepository.cleanup_older_than_days"
                f"({self._channel}, {days}): {type(e).__name__}: {e}"
            )
            return 0
