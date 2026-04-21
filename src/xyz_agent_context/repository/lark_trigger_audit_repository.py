"""
@file_name: lark_trigger_audit_repository.py
@author: Bin Liang
@date: 2026-04-21
@description: Append-only audit log for the Lark trigger lifecycle.

The trigger runs in its own container on EC2 and users often cannot
pull logs out. Past incidents (e.g. "the bot went silent for hours
then replied 5 times to old messages") could not be diagnosed because
nobody could reconstruct what the trigger was doing during the gap.

This repository is the trigger's black-box recorder:
  - Every message ingress (processed / dropped / dedup-fail-open)
  - Every WS connect / disconnect / backoff
  - Every worker error / timeout
  - Periodic heartbeats
  - Subscriber lifecycle (started / stopped)
  - Inbox write failures

`details` is JSON so adding fields never requires a migration. Retention
is 30 days (see `cleanup_older_than_days`), longer than the dedup
window because incident reviews often span weeks.

**Best-effort writes** — `append` NEVER raises. Losing an audit row is
always preferable to stalling real user traffic. Failures are logged
to loguru (the last-resort sink).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger


def _event_time_str(value: Any) -> str:
    """Normalise an event_time cell to a sortable ISO string.

    sqlite backend yields a ``datetime`` object while mysql tends to
    yield a string; comparisons must be type-uniform.
    """
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value or "")


# --- Event type constants ---------------------------------------------------
# Kept as module-level constants (not an Enum) so callers can grep for them
# as string literals and the DB column stays a simple VARCHAR.
EVENT_INGRESS_PROCESSED = "ingress_processed"
EVENT_INGRESS_DROPPED_DEDUP = "ingress_dropped_dedup"
EVENT_INGRESS_DROPPED_HISTORIC = "ingress_dropped_historic"
EVENT_INGRESS_DROPPED_ECHO = "ingress_dropped_echo"
EVENT_INGRESS_DROPPED_UNBOUND = "ingress_dropped_unbound"
EVENT_DEDUP_FAIL_OPEN = "dedup_fail_open"
EVENT_WS_CONNECTED = "ws_connected"
EVENT_WS_DISCONNECTED = "ws_disconnected"
EVENT_WS_BACKOFF = "ws_backoff"
EVENT_SUBSCRIBER_STARTED = "subscriber_started"
EVENT_SUBSCRIBER_STOPPED = "subscriber_stopped"
EVENT_WORKER_ERROR = "worker_error"
EVENT_WORKER_TIMEOUT = "worker_timeout"
EVENT_INBOX_WRITE_FAILED = "inbox_write_failed"
EVENT_HEARTBEAT = "heartbeat"


class LarkTriggerAuditRepository:
    """Append-only lifecycle log for the Lark trigger."""

    TABLE = "lark_trigger_audit"

    def __init__(self, db_client):
        self._db = db_client

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

        `details` is serialised to JSON so callers can stash arbitrary
        debug context (backoff seconds, uptime, error class, etc.)
        without schema changes.
        """
        now = datetime.now(timezone.utc).isoformat(sep=" ")
        row = {
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
                f"LarkTriggerAuditRepository.append({event_type}): "
                f"{type(e).__name__}: {e} (row dropped; audit is advisory only)"
            )

    async def recent(
        self,
        limit: int = 100,
        *,
        event_type: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> list[dict]:
        """Newest-first slice of the log, optionally filtered."""
        filters: dict[str, Any] = {}
        if event_type:
            filters["event_type"] = event_type
        if agent_id:
            filters["agent_id"] = agent_id
        rows = await self._db.get(self.TABLE, filters)
        # Newest first — rely on event_time ordering without dialect quirks
        rows.sort(key=lambda r: _event_time_str(r.get("event_time")), reverse=True)
        return rows[:limit]

    async def count_by_type(self, since_hours: int = 1) -> dict[str, int]:
        """
        Summary for /healthz: event_type -> count over the last N hours.

        Implemented as a fetch-then-count (not a GROUP BY) because the
        underlying AsyncDatabaseClient API is filter-based and this stays
        portable across sqlite + mysql without hand-written SQL. At
        N<=24h expected volume the row count is tiny.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=since_hours)
        ).isoformat(sep=" ")
        rows = await self._db.get(self.TABLE, {})
        counts: dict[str, int] = {}
        for r in rows:
            if _event_time_str(r.get("event_time")) < cutoff:
                continue
            et = r.get("event_type", "")
            counts[et] = counts.get(et, 0) + 1
        return counts

    async def cleanup_older_than_days(self, days: int) -> int:
        """
        Delete rows older than ``days`` days. Called periodically from the
        trigger's watcher loop.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat(sep=" ")
        try:
            # Use the dialect-agnostic client API. A hand-written DELETE
            # statement would need per-backend placeholder handling
            # (`?` vs `%s`), which is error-prone — see bug M-8 from the
            # lark_seen_messages cleanup path.
            rows = await self._db.get(self.TABLE, {})
            to_delete = [
                r["id"] for r in rows
                if _event_time_str(r.get("event_time")) < cutoff
            ]
            for row_id in to_delete:
                await self._db.delete(self.TABLE, {"id": row_id})
            return len(to_delete)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"LarkTriggerAuditRepository.cleanup_older_than_days({days}): "
                f"{type(e).__name__}: {e}"
            )
            return 0
