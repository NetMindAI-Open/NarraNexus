"""
@file_name: embedding_migration_service.py
@author: Bin Liang
@date: 2026-03-23
@description: Embedding vector migration/rebuild service

When users switch embedding models, this service scans all entity types
(narrative, event, job, entity) and generates missing embeddings for the
new model. Supports progress tracking and batch processing.

Key design decisions:
  - Each _source_text builder mirrors the ORIGINAL embedding generation
    logic exactly (see docstrings for cross-references).
  - SQL queries extract fields from JSON columns where needed — e.g.
    narratives.narrative_info is a JSON object containing {name, description,
    current_summary, actors}; there is no top-level 'name' column.
  - tags in instance_social_entities is a JSON array; raw SQL returns it
    as a string, so we JSON-parse it before joining.

Usage:
    from xyz_agent_context.services.embedding_migration_service import EmbeddingMigrationService

    service = EmbeddingMigrationService(db_client)
    status = await service.get_status()       # Check current progress
    await service.rebuild_all()               # Start full rebuild
"""

from __future__ import annotations

import asyncio
import json as _json
from dataclasses import dataclass, field
from typing import Optional, Callable

from loguru import logger

from xyz_agent_context.agent_framework.api_config import embedding_config
from xyz_agent_context.agent_framework.llm_api.embedding import (
    get_embedding,
    prepare_job_text_for_embedding,
)
from xyz_agent_context.repository.embedding_store_repository import EmbeddingStoreRepository


# =============================================================================
# Status Tracking
# =============================================================================

@dataclass
class MigrationProgress:
    """Current state of an embedding migration"""
    is_running: bool = False
    current_model: str = ""
    # Per-entity-type counts
    total: dict[str, int] = field(default_factory=dict)
    completed: dict[str, int] = field(default_factory=dict)
    failed: dict[str, int] = field(default_factory=dict)
    # Overall
    error: Optional[str] = None
    finished: bool = False

    @property
    def total_count(self) -> int:
        return sum(self.total.values())

    @property
    def completed_count(self) -> int:
        return sum(self.completed.values())

    @property
    def progress_pct(self) -> float:
        t = self.total_count
        return (self.completed_count / t * 100) if t > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "is_running": self.is_running,
            "current_model": self.current_model,
            "total": self.total,
            "completed": self.completed,
            "failed": self.failed,
            "total_count": self.total_count,
            "completed_count": self.completed_count,
            "progress_pct": round(self.progress_pct, 1),
            "error": self.error,
            "finished": self.finished,
        }


# Global progress singleton
_progress = MigrationProgress()


def get_migration_progress() -> MigrationProgress:
    return _progress


# =============================================================================
# Source Text Builders
#
# Each function reconstructs the text that was ORIGINALLY used to generate
# the embedding for that entity type. The cross-reference comment tells you
# which production code path produces the same text so you can verify they
# stay in sync.
# =============================================================================

def _narrative_source_text(row: dict) -> str:
    """
    Build embedding source text for a narrative.

    Cross-ref: narrative/_narrative_impl/updater.py → _regenerate_topic_hint()

    Priority:
      1. topic_hint (pre-built by the updater, best source)
      2. Reconstruct from name + current_summary (both extracted from
         narrative_info JSON column via SQL JSON_EXTRACT)
    """
    hint = row.get("topic_hint", "")
    if hint:
        return hint
    name = row.get("name", "") or ""
    summary = row.get("current_summary", "") or ""
    if name and summary:
        return f"{name}: {summary}"
    return summary or name or f"Conversation {row.get('narrative_id', '')}"


def _event_source_text(row: dict) -> str:
    """
    Build embedding source text for an event.

    Cross-ref: narrative/_event_impl/processor.py → _generate_embedding()

    Priority:
      1. embedding_text (pre-built by the processor, best source)
      2. Reconstruct from env_context.input + final_output
    """
    text = row.get("embedding_text", "") or ""
    if text:
        return text
    # Fallback: reconstruct from input/output (same logic as processor)
    inp = row.get("input_content", "") or ""
    out = row.get("final_output", "") or ""
    max_len = 2000
    text = inp[:max_len // 2]
    remaining = max_len - len(text)
    if remaining > 50 and out:
        text += " " + out[:remaining]
    return text.strip()


def _job_source_text(row: dict) -> str:
    """
    Build embedding source text for a job.

    Cross-ref: agent_framework/llm_api/embedding.py → prepare_job_text_for_embedding()

    Delegates to the exact same function used in production.
    """
    title = row.get("title", "") or ""
    description = row.get("description", "") or ""
    payload = row.get("payload", "") or ""
    return prepare_job_text_for_embedding(title, description, payload)


def _entity_source_text(row: dict) -> str:
    """
    Build embedding source text for a social entity.

    Cross-ref: module/social_network_module/_entity_updater.py → update_entity_embedding()

    Note: tags is a JSON array in MySQL. Raw SQL returns it as a JSON string
    (e.g. '["tag1","tag2"]'), so we parse it before joining.
    """
    parts = []
    name = row.get("entity_name", "") or ""
    desc = row.get("entity_description", "") or ""
    tags_raw = row.get("tags", "")
    if name:
        parts.append(f"Name: {name}")
    if desc:
        parts.append(f"Description: {desc}")
    if tags_raw:
        # Parse JSON string → list if needed
        if isinstance(tags_raw, str):
            try:
                tags_raw = _json.loads(tags_raw)
            except (ValueError, TypeError):
                pass
        if isinstance(tags_raw, list) and tags_raw:
            parts.append(f"Tags: {', '.join(str(t) for t in tags_raw)}")
        elif isinstance(tags_raw, str) and tags_raw:
            parts.append(f"Tags: {tags_raw}")
    return "\n".join(parts)


# =============================================================================
# SQL Queries
#
# Centralised here so get_status() and _rebuild_*() always use identical
# WHERE clauses. A mismatch would cause "missing" to never reach 0.
# =============================================================================

# Shared WHERE clauses — used by both get_status() and _rebuild_*() so the
# "total" count always matches what can actually be processed.
#
# TRIM() is used to align with Python's str.strip(): a whitespace-only value
# like '  ' passes `!= ''` in SQL but becomes empty after strip(), causing
# a permanent "1 missing" if we don't trim here.
_EVENT_WHERE = (
    "WHERE (embedding_text IS NOT NULL AND TRIM(embedding_text) != '') "
    "OR (final_output IS NOT NULL AND TRIM(final_output) != '')"
)
_JOB_WHERE = (
    "WHERE (title IS NOT NULL AND TRIM(title) != '') "
    "OR (description IS NOT NULL AND TRIM(description) != '')"
)
_ENTITY_WHERE = (
    "WHERE (entity_name IS NOT NULL AND TRIM(entity_name) != '') "
    "OR (entity_description IS NOT NULL AND TRIM(entity_description) != '')"
)

_STATUS_QUERIES: list[tuple[str, str]] = [
    # Narratives always produce text (fallback to "Conversation {id}")
    ("narrative", "SELECT COUNT(*) as cnt FROM narratives"),
    ("event",    f"SELECT COUNT(*) as cnt FROM events {_EVENT_WHERE}"),
    ("job",      f"SELECT COUNT(*) as cnt FROM instance_jobs {_JOB_WHERE}"),
    ("entity",   f"SELECT COUNT(*) as cnt FROM instance_social_entities {_ENTITY_WHERE}"),
]


# =============================================================================
# Migration Service
# =============================================================================

class EmbeddingMigrationService:
    """
    Scans all entity types and generates missing embeddings for the current model.
    """

    # Batch size for embedding generation (avoid overwhelming the API)
    BATCH_SIZE = 20

    def __init__(self, db_client):
        self.db = db_client
        self.emb_repo = EmbeddingStoreRepository(db_client)

    async def get_status(self) -> dict:
        """
        Get the current migration status.

        Checks how many entities have embeddings for the active model
        vs. how many exist in total. In legacy mode (no llm_config.json),
        always returns all_done=True since the old columns are used.
        """
        from xyz_agent_context.agent_framework.llm_api.embedding_store_bridge import use_embedding_store

        # Legacy mode: embeddings_store not in use, everything is fine
        if not use_embedding_store():
            return {
                "model": embedding_config.model,
                "stats": {},
                "all_done": True,
                "migration": _progress.to_dict(),
                "legacy_mode": True,
            }

        model = embedding_config.model

        # Clean stale data before counting
        await self._cleanup_before_rebuild(model)

        stats = {}

        for entity_type, count_sql in _STATUS_QUERIES:
            total_rows = await self.db.execute(count_sql, fetch=True)
            total = total_rows[0]["cnt"] if total_rows else 0
            existing = await self.emb_repo.count_by_model(entity_type, model)
            stats[entity_type] = {
                "total": total,
                "migrated": existing,
                "missing": max(0, total - existing),
            }

        all_done = all(s["missing"] == 0 for s in stats.values())
        return {
            "model": model,
            "stats": stats,
            "all_done": all_done,
            "migration": _progress.to_dict(),
        }

    async def rebuild_all(self) -> None:
        """
        Rebuild embeddings for all entity types under the current model.

        This runs as a long task. Progress is tracked via get_migration_progress().
        """
        global _progress

        if _progress.is_running:
            logger.warning("Embedding migration already running, skipping")
            return

        model = embedding_config.model

        _progress = MigrationProgress(is_running=True, current_model=model)
        logger.info(f"Starting embedding migration for model={model}")

        try:
            # Phase 0: Data cleanup before rebuild
            await self._cleanup_before_rebuild(model)

            await self._rebuild_narratives(model)
            await self._rebuild_events(model)
            await self._rebuild_jobs(model)
            await self._rebuild_entities(model)

            _progress.finished = True
            logger.info(
                f"Embedding migration completed: "
                f"{_progress.completed_count}/{_progress.total_count} succeeded"
            )
        except Exception as e:
            _progress.error = str(e)
            logger.error(f"Embedding migration failed: {e}")
        finally:
            _progress.is_running = False

    # ---- Data cleanup ----

    async def _cleanup_before_rebuild(self, model: str) -> None:
        """
        Clean up stale/empty data before rebuild or status check.

        1. Remove sentinel records (dimensions=0) from embeddings_store
        2. Remove empty-shell entities (no name AND no description)
        3. Remove orphaned embeddings_store entries whose source entity no longer exists
        """
        # 1. Sentinel records from a previous buggy run
        sentinel_count = await self.db.execute(
            f"DELETE FROM {self.emb_repo.TABLE} WHERE dimensions = 0 AND model = %s",
            (model,),
        )
        if sentinel_count:
            logger.info(f"Cleaned {sentinel_count} sentinel records")

        # 2. Empty-shell entities (no embeddable text at all)
        empty_entity_count = await self.db.execute(
            "DELETE FROM instance_social_entities "
            "WHERE (entity_name IS NULL OR TRIM(entity_name) = '') "
            "AND (entity_description IS NULL OR TRIM(entity_description) = '')",
        )
        if empty_entity_count:
            logger.info(f"Cleaned {empty_entity_count} empty-shell entities from instance_social_entities")
            # Also remove their orphaned embeddings
            await self.db.execute(
                f"DELETE es FROM {self.emb_repo.TABLE} es "
                "LEFT JOIN instance_social_entities ise ON es.entity_id = ise.entity_id "
                "WHERE es.entity_type = 'entity' AND ise.entity_id IS NULL",
            )

    # ---- Per-entity-type rebuild ----

    async def _rebuild_narratives(self, model: str) -> None:
        entity_type = "narrative"
        # name and current_summary live inside narrative_info JSON column;
        # topic_hint is a standalone TEXT column.
        rows = await self.db.execute(
            "SELECT narrative_id, "
            "JSON_UNQUOTE(JSON_EXTRACT(narrative_info, '$.name')) as name, "
            "JSON_UNQUOTE(JSON_EXTRACT(narrative_info, '$.current_summary')) as current_summary, "
            "topic_hint FROM narratives",
            fetch=True,
        )
        await self._process_rows(entity_type, model, rows, "narrative_id", _narrative_source_text)

    async def _rebuild_events(self, model: str) -> None:
        entity_type = "event"
        # Use the same WHERE as _STATUS_QUERIES to keep total/missing in sync
        rows = await self.db.execute(
            "SELECT event_id, embedding_text, "
            "JSON_UNQUOTE(JSON_EXTRACT(env_context, '$.input')) as input_content, "
            f"final_output FROM events {_EVENT_WHERE}",
            fetch=True,
        )
        await self._process_rows(entity_type, model, rows, "event_id", _event_source_text)

    async def _rebuild_jobs(self, model: str) -> None:
        entity_type = "job"
        # Use the same WHERE as _STATUS_QUERIES to keep total/missing in sync
        rows = await self.db.execute(
            f"SELECT job_id, title, description, payload FROM instance_jobs {_JOB_WHERE}",
            fetch=True,
        )
        await self._process_rows(entity_type, model, rows, "job_id", _job_source_text)

    async def _rebuild_entities(self, model: str) -> None:
        entity_type = "entity"
        # Use the same WHERE as _STATUS_QUERIES to keep total/missing in sync
        rows = await self.db.execute(
            f"SELECT entity_id, entity_name, entity_description, tags FROM instance_social_entities {_ENTITY_WHERE}",
            fetch=True,
        )
        await self._process_rows(entity_type, model, rows, "entity_id", _entity_source_text)

    # ---- Core batch processor ----

    async def _process_rows(
        self,
        entity_type: str,
        model: str,
        rows: list[dict],
        id_field: str,
        source_text_fn: Callable[[dict], str],
    ) -> None:
        """Process rows in batches, skipping those that already have embeddings."""
        if not rows:
            _progress.total[entity_type] = 0
            _progress.completed[entity_type] = 0
            _progress.failed[entity_type] = 0
            return

        # Filter out rows that already have embeddings for this model
        all_ids = [row[id_field] for row in rows]
        existing = await self.emb_repo.get_vectors_by_ids(entity_type, all_ids, model)
        rows_to_process = [r for r in rows if r[id_field] not in existing]

        _progress.total[entity_type] = len(rows_to_process)
        _progress.completed[entity_type] = 0
        _progress.failed[entity_type] = 0

        logger.info(
            f"[{entity_type}] {len(rows_to_process)} need embedding "
            f"({len(existing)} already done, {len(rows)} total)"
        )

        # Process in batches
        for i in range(0, len(rows_to_process), self.BATCH_SIZE):
            batch = rows_to_process[i:i + self.BATCH_SIZE]
            records = []

            for row in batch:
                entity_id = row[id_field]
                source_text = source_text_fn(row)
                if not source_text.strip():
                    # Entity has no embeddable text — skip it.
                    # SQL WHERE clauses should prevent this, but defensive.
                    _progress.completed[entity_type] += 1
                    continue
                try:
                    vector = await get_embedding(source_text)
                    actual_dims = len(vector)
                    records.append({
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "model": model,
                        "dimensions": actual_dims,
                        "vector": vector,
                        "source_text": source_text[:2000],  # Truncate for storage
                    })
                except Exception as e:
                    logger.warning(f"[{entity_type}] Failed to embed {entity_id}: {e}")
                    _progress.failed[entity_type] += 1

            # Batch upsert
            if records:
                await self.emb_repo.upsert_batch(records)
                _progress.completed[entity_type] += len(records)

            # Small delay to avoid rate limiting
            if i + self.BATCH_SIZE < len(rows_to_process):
                await asyncio.sleep(0.1)

        logger.info(
            f"[{entity_type}] Done: {_progress.completed[entity_type]} completed, "
            f"{_progress.failed[entity_type]} failed"
        )
