"""
@file_name: _matrix_dedup.py
@author: Bin Liang
@date: 2026-03-13
@description: Persistent event deduplication for MatrixTrigger

Uses a single DB table (matrix_processed_events) as the source of truth.
Composite PK (event_id, agent_id) ensures per-agent dedup.
No in-memory cache — DB PK lookups are fast enough and eliminate
all cache-coherency bugs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Set

from loguru import logger

from xyz_agent_context.utils import DatabaseClient


class MatrixEventDedup:
    """
    DB-only event deduplication.

    Usage:
        dedup = MatrixEventDedup(db)
        processed = await dedup.filter_processed("agent_123", ["$evt1", "$evt2"])
        # processed = {"$evt1"}  → skip
        # "$evt2" is new → process it

        await dedup.mark_processed("agent_123", ["$evt2"])
    """

    TABLE = "matrix_processed_events"

    def __init__(self, db: DatabaseClient):
        self._db = db

    async def filter_processed(
        self, agent_id: str, event_ids: List[str]
    ) -> Set[str]:
        """
        Return the subset of event_ids already processed by this agent.
        """
        if not event_ids:
            return set()

        try:
            placeholders = ",".join(["%s"] * len(event_ids))
            rows = await self._db.execute(
                f"SELECT event_id FROM {self.TABLE} "
                f"WHERE agent_id = %s AND event_id IN ({placeholders})",
                tuple([agent_id] + list(event_ids)),
            )
            return {row["event_id"] for row in rows}
        except Exception as e:
            logger.warning(f"Dedup check failed: {e}")
            return set()

    async def mark_processed(
        self, agent_id: str, event_ids: List[str]
    ) -> None:
        """
        Mark events as processed. Uses INSERT IGNORE for idempotency.
        """
        if not event_ids:
            return

        try:
            now = datetime.now(timezone.utc)
            values = [(eid, agent_id, now) for eid in event_ids]
            placeholders = ",".join(["(%s, %s, %s)"] * len(values))
            flat_params = [p for triple in values for p in triple]

            await self._db.execute(
                f"INSERT IGNORE INTO {self.TABLE} "
                f"(event_id, agent_id, processed_at) VALUES {placeholders}",
                tuple(flat_params),
                fetch=False,
            )
        except Exception as e:
            logger.warning(f"Dedup write failed: {e}")

    async def cleanup_expired(self, days: int = 7) -> int:
        """
        Delete records older than N days. Returns deleted count, or -1 on error.
        """
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            deleted = await self._db.execute(
                f"DELETE FROM {self.TABLE} WHERE processed_at < %s",
                (cutoff,),
                fetch=False,
            )
            if deleted > 0:
                logger.info(f"Dedup cleanup: deleted {deleted} expired records")
            return deleted
        except Exception as e:
            logger.warning(f"Dedup cleanup failed: {e}")
            return -1
