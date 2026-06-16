"""
@file_name: repository.py
@author: NetMind.AI
@date: 2026-06-03
@description: MemoryRepository — generic data access for one memory `kind`.

A thin BaseRepository[MemoryRecord] specialisation: the constructor picks the
physical table (`memory_<kind>`) so one class serves every kind ("实例化参数
决定数据表"). All CRUD + N+1 batch loading is inherited from BaseRepository;
this only adds the memory-specific reads (scope / bi-temporal / tags) and the
supersession primitive (tombstone, not delete).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from xyz_agent_context.repository.base import BaseRepository
from xyz_agent_context.memory.record import MemoryRecord
from xyz_agent_context.utils.timezone import utc_now


class MemoryRepository(BaseRepository[MemoryRecord]):
    """Per-kind store. CRUD/upsert/get_by_ids come from BaseRepository."""

    id_field = "record_id"

    def __init__(self, kind: str, db_client: Any):
        # Instance attribute overrides the empty class attribute → selects the
        # per-kind table before BaseRepository validates table_name is set.
        self.table_name = f"memory_{kind}"
        super().__init__(db_client)
        self.kind = kind

    def _row_to_entity(self, row: Dict[str, Any]) -> MemoryRecord:
        return MemoryRecord.from_row(row)

    def _entity_to_row(self, entity: MemoryRecord) -> Dict[str, Any]:
        return entity.to_row()

    async def query(
        self,
        *,
        agent_id: str,
        scope_type: Optional[str] = None,
        scope_id: Optional[str] = None,
        subtype: Optional[str] = None,
        live_only: bool = True,
        valid_now: bool = False,
        tags_any: Optional[List[str]] = None,
        limit: Optional[int] = None,
        candidate_cap: Optional[int] = None,
    ) -> List[MemoryRecord]:
        """Scope-filtered fetch, newest first.

        Equality predicates go to the DB (indexed); the bi-temporal/tag
        predicates are applied in Python — per-(agent,scope) memory is bounded,
        and this keeps one portable code path instead of the dialect-specific
        SQL that bit the old EventMemoryRepository (MySQL-only `WHERE`).

        `candidate_cap` bounds the DB read to the most-recent N rows (indexed
        ORDER BY created_at). High-volume kinds (e.g. a 10k-row event log) must
        pass it so a per-turn recall never loads the whole table — recency is
        the right prefilter, and grep covers exact lookups of older rows.
        """
        filters: Dict[str, Any] = {"agent_id": agent_id}
        for col, val in (("scope_type", scope_type), ("scope_id", scope_id), ("subtype", subtype)):
            if val is not None:
                filters[col] = val

        records = await self.find(filters, limit=candidate_cap, order_by="created_at DESC")
        now = utc_now()
        want_tags = set(tags_any) if tags_any else None

        out = [
            r for r in records
            if (not live_only or r.is_live)
            and (not valid_now or r.is_valid_at(now))
            and (want_tags is None or want_tags & set(r.tags))
        ]
        out.sort(key=lambda r: r.created_at or now, reverse=True)
        return out[:limit] if limit else out

    async def tombstone(self, record_id: str, invalid_at: Optional[Any] = None) -> int:
        """Supersession: mark a record no longer current WITHOUT deleting it
        (history is retained, design §9.2). `invalid_at` is the reality-axis
        moment it stopped being true; `expired_at` is the system-axis stamp."""
        now = utc_now()
        return await self.update(record_id, {"invalid_at": invalid_at or now, "expired_at": now})

    async def touch(self, record_id: str) -> int:
        """Bump recency after a recall (feeds the recency ranking boost)."""
        return await self.update(record_id, {"last_used_at": utc_now()})
