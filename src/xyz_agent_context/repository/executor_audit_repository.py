"""
@file_name: executor_audit_repository.py
@author: Bin Liang
@date: 2026-06-18
@description: Append-only audit log for executor/loop lifecycle and OOM events.

Why this exists
===============
Container restarts wipe docker logs; the DB survives (incident lesson #5).
After an OOM or runaway-loop incident the post-mortem question is always
"what was the executor doing in the minutes before it died?" This repository
writes one row per lifecycle event and exposes two read paths:

  recent()       — newest-first slice for admin/debug UIs and manual inspection
  counts_since() — event_type -> count for L3 monitoring (alert when OOM rate
                   spikes, admission queue grows, or culled rate is abnormally
                   high)

Best-effort writes
------------------
record() NEVER raises. The observer must not break the observed; losing an
audit row beats stalling an executor loop (same policy as ServiceAuditRepository
and LarkTriggerAuditRepository).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.schema.executor_audit import ExecutorAuditEvent


class ExecutorAuditRepository:
    """Append-only lifecycle log for executor containers and loop events."""

    TABLE = "instance_executor_audit"

    def __init__(self, db_client: Any) -> None:
        # Untyped on purpose — importing AsyncDatabaseClient here would add
        # a load-order coupling for no benefit (same pattern as
        # ServiceAuditRepository).
        self._db = db_client

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def record(
        self,
        *,
        event_type: str,
        user_id: Optional[str] = None,
        container_id: Optional[str] = None,
        run_id: Optional[str] = None,
        active_loops: Optional[int] = None,
        active_users: Optional[int] = None,
        queue_depth: Optional[int] = None,
        free_mem_mb: Optional[int] = None,
        detail: Optional[dict] = None,
    ) -> None:
        """Best-effort insert of one audit row. Never raises into the caller."""
        row: dict[str, Any] = {"event_type": event_type}
        if user_id is not None:
            row["user_id"] = user_id
        if container_id is not None:
            row["container_id"] = container_id
        if run_id is not None:
            row["run_id"] = run_id
        if active_loops is not None:
            row["active_loops"] = active_loops
        if active_users is not None:
            row["active_users"] = active_users
        if queue_depth is not None:
            row["queue_depth"] = queue_depth
        if free_mem_mb is not None:
            row["free_mem_mb"] = free_mem_mb
        if detail is not None:
            row["detail_json"] = self._to_json(detail)
        try:
            await self._db.insert(self.TABLE, row)
        except Exception as e:  # noqa: BLE001 — audit writes are advisory
            logger.warning(
                f"ExecutorAudit write failed ({event_type}): "
                f"{type(e).__name__}: {e} (row dropped; audit is advisory)"
            )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def recent(self, limit: int = 50) -> list[dict]:
        """Return most recent audit rows, newest first."""
        try:
            rows = await self._db.get(self.TABLE, {})
            rows.sort(key=lambda r: r.get("id", 0), reverse=True)
            return rows[:limit]
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"ExecutorAudit recent() failed: {type(e).__name__}: {e}"
            )
            return []

    async def counts_since(self, since_iso: str) -> dict[str, int]:
        """Return event_type -> count for rows created at or after since_iso.

        Used by L3 monitoring: pass a window start (e.g. now - 1 hour) to
        get a summary of activity for that window.
        """
        try:
            rows = await self._db.get(self.TABLE, {})
            counts: dict[str, int] = {}
            for row in rows:
                # created_at may be a datetime object (sqlite) or string (mysql)
                created = row.get("created_at", "")
                if isinstance(created, str):
                    ts = created
                else:
                    # datetime object from sqlite backend
                    ts = created.isoformat(sep="T") if created else ""
                if ts < since_iso:
                    continue
                et = row.get("event_type", "")
                counts[et] = counts.get(et, 0) + 1
            return counts
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"ExecutorAudit counts_since() failed: {type(e).__name__}: {e}"
            )
            return {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_json(value: Any) -> Optional[str]:
        if value is None or isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            return str(value)
