"""
@file_name: agent_circuit_breaker_repository.py
@author:
@date: 2026-07-13
@description: Data access for the real-time-layer Agent circuit-breaker.

CRUD over ``instance_agent_circuit_breaker`` (one row per agent_id). The
breaker service owns all the escalation logic; this layer only reads/writes
rows. ``upsert_state`` is the workhorse — a partial, agent_id-keyed
insert-or-update that stamps ``updated_at``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from xyz_agent_context.schema import AgentCircuitBreaker, CbStatus
from xyz_agent_context.utils.timezone import utc_now

from .base import BaseRepository


class AgentCircuitBreakerRepository(BaseRepository[AgentCircuitBreaker]):
    """Repository for Agent circuit-breaker state."""

    table_name = "instance_agent_circuit_breaker"
    id_field = "agent_id"

    async def get(self, agent_id: str) -> Optional[AgentCircuitBreaker]:
        """Return the breaker row for an agent, or None if it has none yet."""
        row = await self._db.get_one(self.table_name, {"agent_id": agent_id})
        return self._row_to_entity(row) if row else None

    async def upsert_state(self, agent_id: str, updates: Dict[str, Any]) -> None:
        """Insert-or-update the agent's breaker row with ``updates``.

        Keyed on agent_id; always refreshes ``updated_at``. A partial write —
        only the keys in ``updates`` (plus updated_at) are touched on an
        existing row.
        """
        data = dict(updates)
        data["updated_at"] = utc_now()
        existing = await self._db.get_one(self.table_name, {"agent_id": agent_id})
        if existing:
            await self._db.update(self.table_name, {"agent_id": agent_id}, data)
        else:
            data["agent_id"] = agent_id
            await self._db.insert(self.table_name, data)

    async def find_by_status(self, status: str) -> List[AgentCircuitBreaker]:
        """All breaker rows currently in the given ``cb_status``."""
        rows = await self._db.get(self.table_name, filters={"cb_status": status})
        return [self._row_to_entity(r) for r in rows if r]

    async def find_paused(self) -> List[AgentCircuitBreaker]:
        """All agents currently in PAUSED state (any reason)."""
        return await self.find_by_status(CbStatus.PAUSED.value)

    def _row_to_entity(self, row: Dict[str, Any]) -> AgentCircuitBreaker:
        # Pydantic ignores the extra ``id`` column and coerces ISO strings /
        # enum strings into the model's types.
        return AgentCircuitBreaker(**row)

    def _entity_to_row(self, entity: AgentCircuitBreaker) -> Dict[str, Any]:
        row = entity.model_dump()
        row.pop("created_at", None)  # DB default handles first insert
        return row
