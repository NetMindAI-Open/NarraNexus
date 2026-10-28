"""
@file_name: artifact_event_repository.py
@author: NetMind.AI
@date: 2026-08-20
@description: The owner of `instance_artifact_events` (the artifact_changed
outbox). Before this class the table had no owner: notify.py inserted rows
directly and BackgroundRun carried two hand-written SQL statements — three
files to touch for one schema change, and a raw-client escape hatch
(`BaseRepository.db`) grown just to reach the table (review #334 I9). All
reads and writes go through here now; the escape hatch is gone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from narranexus.platform.utils.db.database import AsyncDatabaseClient


class ArtifactEventRepository:
    """CRUD for the artifact_changed outbox (see notify.py for the payload
    contract and background_run for the drain discipline).

    Deliberately NOT a BaseRepository subclass: the table's id is an
    auto-increment integer with no entity model, and every consumer reads
    plain dicts — entity plumbing here would be ceremony without a reader
    (same reasoning as team_workspace_repository)."""

    def __init__(self, db: AsyncDatabaseClient):
        self._db = db

    async def stage(self, *, agent_id: str, payload_json: str) -> None:
        await self._db.insert(
            "instance_artifact_events",
            {"agent_id": agent_id, "payload_json": payload_json},
        )

    async def pending_for_agent(self, agent_id: str, limit: int) -> List[Dict[str, Any]]:
        """Oldest-first unconsumed rows for one agent."""
        return await self._db.execute(
            "SELECT id, payload_json FROM instance_artifact_events "
            "WHERE agent_id = %s AND consumed_at IS NULL ORDER BY id LIMIT %s",
            params=(agent_id, limit),
            fetch=True,
        ) or []

    async def mark_consumed(self, ids: List[int]) -> None:
        if not ids:
            return
        placeholders = ", ".join(["%s"] * len(ids))
        await self._db.execute(
            f"UPDATE instance_artifact_events SET consumed_at = %s "
            f"WHERE id IN ({placeholders})",
            params=(datetime.now(timezone.utc).isoformat(), *ids),
        )
