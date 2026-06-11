"""
@file_name: m0001_unified_memory_backfill.py
@author: NetMind.AI
@date: 2026-06-09
@description: Migration 0001 — make pre-existing data searchable via unified memory.

The unified-memory search layer added projection indexes (memory_narrative /
memory_job / memory_bus / memory_event) written by the live writers, and folded
entities into memory_entity. A database that predates this has operational rows
(narratives / jobs / bus messages / events / instance_social_entities) with no
index — invisible to `remember`. This migration rebuilds them.

Scope per kind:
  - narrative / job / bus / interaction(event): re-projected from the operational
    tables (backfill_agent_search_indexes), composing the same text the live
    writers do, with a source_ref pointer.
  - entity: the retired `instance_social_entities` rows are moved into
    memory_entity (migrate_legacy_entities).
  - chat: RETIRED kind — conversation search is the interaction index; skipped.
  - observation: LLM-distilled, never stored as raw source — unreconstructable;
    left to re-accumulate naturally.

Idempotent (deterministic record_ids → upsert), so safe to re-run if the ledger
row was never written.
"""
from __future__ import annotations

from typing import Dict, TYPE_CHECKING

from loguru import logger

from . import Migration

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


async def _apply(db: "AsyncDatabaseClient") -> Dict:
    from xyz_agent_context.memory.backfill import (
        backfill_agent_search_indexes,
        migrate_legacy_entities,
    )

    agents = await db.get("agents")
    indexed = 0
    for a in agents:
        aid = a.get("agent_id")
        if not aid:
            continue
        try:
            indexed += await backfill_agent_search_indexes(db, aid)
        except Exception as e:  # noqa: BLE001 — one agent's failure must not sink the rest
            logger.warning(f"[migrate 0001] backfill agent {aid} failed: {e}")

    entities = await migrate_legacy_entities(db)

    return {
        "agents": len(agents),
        "indexes_backfilled": indexed,
        "legacy_entities_migrated": entities,
    }


MIGRATION = Migration(
    id="0001_unified_memory_backfill",
    description=(
        "Backfill memory_* search indexes (narrative/job/bus/interaction) for all "
        "agents + migrate legacy instance_social_entities into memory_entity"
    ),
    apply=_apply,
)
