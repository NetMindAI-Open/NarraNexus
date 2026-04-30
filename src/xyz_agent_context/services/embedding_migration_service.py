"""
@file_name: embedding_migration_service.py
@author: Bin Liang
@date: 2026-04-20
@description: Per-user embedding vector migration/rebuild service.

When a user switches their embedding model, this service scans all entity
types (narrative, event, job, entity) that belong to THAT user and
generates missing embeddings for the new model. Supports progress tracking
(per-user) and batch processing.

Multi-tenant correctness (cloud version):
  - Every SQL query that counts or loads entities is filtered by user_id.
    Narratives and entities join through `agents.created_by` /
    `module_instances.user_id` because they don't carry `user_id` directly.
  - Progress state is kept per-user so concurrent rebuilds don't stomp.
  - The active embedding model is resolved from that user's provider slots
    (via `get_user_llm_configs`), not from the last-loaded global
    `embedding_config` singleton.

Single-user desktop still works: pass the local user_id (e.g. the one
stored in `agents.created_by`) and everything downstream behaves the same
as before — the SQL filter just matches every row for that user.

Usage:
    service = EmbeddingMigrationService(db_client, user_id="alice")
    status = await service.get_status()
    await service.rebuild_all()
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
# Progress Tracking (per-user)
# =============================================================================

@dataclass
class MigrationProgress:
    """Current state of an embedding migration for a single user."""
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


# Module-level registry. Key is user_id.
_progress_by_user: dict[str, MigrationProgress] = {}


def get_migration_progress(user_id: str) -> MigrationProgress:
    """Return the live progress struct for a user, creating it on demand."""
    if not user_id:
        raise ValueError("user_id is required")
    progress = _progress_by_user.get(user_id)
    if progress is None:
        progress = MigrationProgress()
        _progress_by_user[user_id] = progress
    return progress


def _reset_progress_for_tests() -> None:
    """Test helper — wipe the per-user progress registry."""
    _progress_by_user.clear()


# =============================================================================
# Model / provider resolution
# =============================================================================

async def _resolve_user_embedding_model(user_id: str) -> str:
    """
    Figure out which embedding model belongs to this user.

    Prefers the user's `embedding` slot from `user_providers`. Falls back
    to the global `embedding_config.model` for desktop/single-user mode
    where the user has no DB-side provider rows.
    """
    try:
        from xyz_agent_context.agent_framework.api_config import (
            get_user_llm_configs,
        )
        _, _, embedding_cfg = await get_user_llm_configs(user_id)
        if embedding_cfg and embedding_cfg.model:
            return embedding_cfg.model
    except Exception as e:  # pragma: no cover — defensive
        logger.debug(
            f"[EmbeddingMigration] user={user_id}: per-user llm_configs lookup "
            f"failed ({e}); falling back to global embedding_config"
        )
    return embedding_config.model


def _resolve_use_embedding_store(user_id: str) -> bool:
    """
    Synchronous fast-path: is the new embeddings_store path definitely
    enabled for this user?

    Returns True when `llm_config.json` exists on disk — the classic
    desktop/single-user case. The cloud branch (per-user provider rows in
    the database) is handled asynchronously inside
    `EmbeddingMigrationService._should_use_store` because it needs a DB
    handle to check `user_providers`.
    """
    if not user_id:
        return False
    try:
        from xyz_agent_context.agent_framework.provider_registry import (
            provider_registry,
        )
        return provider_registry.config_exists()
    except Exception:  # pragma: no cover — defensive
        return False


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
    Cross-ref: narrative/_narrative_impl/updater.py → _regenerate_topic_hint()
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
    Cross-ref: narrative/_event_impl/processor.py → _generate_embedding()
    """
    text = row.get("embedding_text", "") or ""
    if text:
        return text
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
    Cross-ref: agent_framework/llm_api/embedding.py → prepare_job_text_for_embedding()
    """
    title = row.get("title", "") or ""
    description = row.get("description", "") or ""
    payload = row.get("payload", "") or ""
    return prepare_job_text_for_embedding(title, description, payload)


def _entity_source_text(row: dict) -> str:
    """
    Cross-ref: module/social_network_module/_entity_updater.py → update_entity_embedding()
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
# Per-user SQL
#
# Every query is parameterised on user_id so one user's status/rebuild never
# touches another's rows. The shared WHERE fragments below keep get_status()
# and _rebuild_*() in sync: a mismatch would cause a permanent "N missing".
# =============================================================================

# TRIM() aligns with Python's str.strip() — a whitespace-only value like
# '  ' passes `!= ''` in SQL but becomes empty after strip(), which would
# leave a row permanently missing.
_EVENT_TEXT_FILTER = (
    "(embedding_text IS NOT NULL AND TRIM(embedding_text) != '') "
    "OR (final_output IS NOT NULL AND TRIM(final_output) != '')"
)
_JOB_TEXT_FILTER = (
    "(title IS NOT NULL AND TRIM(title) != '') "
    "OR (description IS NOT NULL AND TRIM(description) != '')"
)
_ENTITY_TEXT_FILTER = (
    "(entity_name IS NOT NULL AND TRIM(entity_name) != '') "
    "OR (entity_description IS NOT NULL AND TRIM(entity_description) != '')"
)


def _narrative_count_sql() -> str:
    """Narratives owned by user (via agents.created_by)."""
    return (
        "SELECT COUNT(*) AS cnt FROM narratives n "
        "JOIN agents a ON a.agent_id = n.agent_id "
        "WHERE a.created_by = %s"
    )


def _event_count_sql() -> str:
    return (
        f"SELECT COUNT(*) AS cnt FROM events "
        f"WHERE user_id = %s AND ({_EVENT_TEXT_FILTER})"
    )


def _job_count_sql() -> str:
    return (
        f"SELECT COUNT(*) AS cnt FROM instance_jobs "
        f"WHERE user_id = %s AND ({_JOB_TEXT_FILTER})"
    )


def _entity_count_sql() -> str:
    """Entities owned by user (via module_instances.user_id)."""
    return (
        "SELECT COUNT(*) AS cnt FROM instance_social_entities ise "
        "JOIN module_instances mi ON mi.instance_id = ise.instance_id "
        f"WHERE mi.user_id = %s AND ({_ENTITY_TEXT_FILTER})"
    )


# =============================================================================
# Migration Service
# =============================================================================

class EmbeddingMigrationService:
    """
    Per-user scanner that populates embeddings_store for the active model.

    `user_id` is required — single-user desktop mode still passes one
    (whatever value lives in `agents.created_by`). Concurrent rebuilds by
    different users are isolated: each has its own MigrationProgress and
    each sees only its own rows.
    """

    # Batch size for embedding generation (avoid overwhelming the API)
    BATCH_SIZE = 20

    def __init__(self, db_client, user_id: str):
        if not user_id:
            raise ValueError("EmbeddingMigrationService requires a user_id")
        self.db = db_client
        self.user_id = user_id
        self.emb_repo = EmbeddingStoreRepository(db_client)

    # ---- Status ----

    async def get_status(self) -> dict:
        """
        Report how many of this user's entities already have embeddings for
        the active model vs. how many exist in total.
        """
        # Resolve whether the new store should be used for this user.
        if not await self._should_use_store():
            model = await _resolve_user_embedding_model(self.user_id)
            return {
                "model": model,
                "stats": {},
                "all_done": True,
                "migration": get_migration_progress(self.user_id).to_dict(),
                "legacy_mode": True,
            }

        model = await _resolve_user_embedding_model(self.user_id)

        # Clean stale data before counting — scoped to this user's rows.
        await self._cleanup_before_rebuild(model)

        stats: dict[str, dict[str, int]] = {}

        for entity_type, count_sql in self._status_queries():
            total_rows = await self.db.execute(
                count_sql, (self.user_id,), fetch=True
            )
            total = total_rows[0]["cnt"] if total_rows else 0
            existing_ids = await self._user_entity_ids(entity_type)
            existing = await self.emb_repo.get_vectors_by_ids(
                entity_type, existing_ids, model
            )
            stats[entity_type] = {
                "total": total,
                "migrated": len(existing),
                "missing": max(0, total - len(existing)),
            }

        all_done = all(s["missing"] == 0 for s in stats.values())
        return {
            "model": model,
            "stats": stats,
            "all_done": all_done,
            "migration": get_migration_progress(self.user_id).to_dict(),
        }

    async def rebuild_all(self) -> None:
        """Rebuild missing embeddings for every entity type owned by the user."""
        progress = get_migration_progress(self.user_id)

        if progress.is_running:
            logger.warning(
                f"[EmbeddingMigration] user={self.user_id}: rebuild already "
                f"running, skipping new request"
            )
            return

        model = await _resolve_user_embedding_model(self.user_id)

        # Reset progress for this run
        progress.is_running = True
        progress.current_model = model
        progress.total = {}
        progress.completed = {}
        progress.failed = {}
        progress.error = None
        progress.finished = False

        logger.info(
            f"[EmbeddingMigration] user={self.user_id}: starting rebuild for "
            f"model={model}"
        )

        try:
            await self._cleanup_before_rebuild(model)

            await self._rebuild_narratives(model)
            await self._rebuild_events(model)
            await self._rebuild_jobs(model)
            await self._rebuild_entities(model)

            progress.finished = True
            logger.info(
                f"[EmbeddingMigration] user={self.user_id}: completed "
                f"{progress.completed_count}/{progress.total_count}"
            )
        except Exception as e:
            progress.error = str(e)
            logger.exception(
                f"[EmbeddingMigration] user={self.user_id}: failed: {e}"
            )
        finally:
            progress.is_running = False

    # ---- Hook for tests / providers ----

    async def _should_use_store(self) -> bool:
        """
        Async-friendly version of `_resolve_use_embedding_store`.

        Returns True when either (a) the legacy global `llm_config.json`
        exists (desktop), or (b) the user has at least one provider row in
        `user_providers` (cloud multi-tenant).
        """
        # Desktop fast path
        if _resolve_use_embedding_store(self.user_id):
            return True

        # Cloud: does this user own any provider row?
        rows = await self.db.get(
            "user_providers",
            filters={"user_id": self.user_id},
            limit=1,
        )
        return bool(rows)

    def _status_queries(self) -> list[tuple[str, str]]:
        return [
            ("narrative", _narrative_count_sql()),
            ("event", _event_count_sql()),
            ("job", _job_count_sql()),
            ("entity", _entity_count_sql()),
        ]

    async def _user_entity_ids(self, entity_type: str) -> list[str]:
        """Return the ID list for a given entity_type, scoped to this user."""
        if entity_type == "narrative":
            sql = (
                "SELECT n.narrative_id FROM narratives n "
                "JOIN agents a ON a.agent_id = n.agent_id "
                "WHERE a.created_by = %s"
            )
            rows = await self.db.execute(sql, (self.user_id,), fetch=True)
            return [r["narrative_id"] for r in rows]
        if entity_type == "event":
            sql = (
                f"SELECT event_id FROM events "
                f"WHERE user_id = %s AND ({_EVENT_TEXT_FILTER})"
            )
            rows = await self.db.execute(sql, (self.user_id,), fetch=True)
            return [r["event_id"] for r in rows]
        if entity_type == "job":
            sql = (
                f"SELECT job_id FROM instance_jobs "
                f"WHERE user_id = %s AND ({_JOB_TEXT_FILTER})"
            )
            rows = await self.db.execute(sql, (self.user_id,), fetch=True)
            return [r["job_id"] for r in rows]
        if entity_type == "entity":
            sql = (
                "SELECT ise.entity_id FROM instance_social_entities ise "
                "JOIN module_instances mi ON mi.instance_id = ise.instance_id "
                f"WHERE mi.user_id = %s AND ({_ENTITY_TEXT_FILTER})"
            )
            rows = await self.db.execute(sql, (self.user_id,), fetch=True)
            return [r["entity_id"] for r in rows]
        return []

    # ---- Data cleanup (scoped to user's entities) ----

    async def _cleanup_before_rebuild(self, model: str) -> None:
        """
        Remove stale rows before counting or rebuilding:
          1. Sentinel rows (dimensions=0) for this user's entities under this model
          2. Empty-shell entities (no name AND no description) owned by this user

        Scope is always constrained to the user — we never touch other users' data.
        """
        # 1. Sentinel rows for this user's entity_ids under this model
        for entity_type in ("narrative", "event", "job", "entity"):
            ids = await self._user_entity_ids(entity_type)
            if not ids:
                continue
            placeholders = ",".join(["%s"] * len(ids))
            sql = (
                f"DELETE FROM {self.emb_repo.TABLE} "
                f"WHERE entity_type = %s AND model = %s AND dimensions = 0 "
                f"AND entity_id IN ({placeholders})"
            )
            await self.db.execute(sql, (entity_type, model, *ids))

        # 2. Empty-shell entities owned by this user (via module_instances).
        # Use a scalar subquery (portable across MySQL and SQLite) instead of
        # MySQL-only `DELETE alias FROM ... JOIN ...`.
        await self.db.execute(
            "DELETE FROM instance_social_entities "
            "WHERE instance_id IN ("
            "    SELECT instance_id FROM module_instances WHERE user_id = %s"
            ") "
            "AND (entity_name IS NULL OR TRIM(entity_name) = '') "
            "AND (entity_description IS NULL OR TRIM(entity_description) = '')",
            (self.user_id,),
        )

    # ---- Per-entity-type rebuild (user-scoped SELECTs) ----

    async def _rebuild_narratives(self, model: str) -> None:
        entity_type = "narrative"
        rows = await self.db.execute(
            "SELECT n.narrative_id, "
            "JSON_UNQUOTE(JSON_EXTRACT(n.narrative_info, '$.name')) AS name, "
            "JSON_UNQUOTE(JSON_EXTRACT(n.narrative_info, '$.current_summary')) AS current_summary, "
            "n.topic_hint "
            "FROM narratives n "
            "JOIN agents a ON a.agent_id = n.agent_id "
            "WHERE a.created_by = %s",
            (self.user_id,),
            fetch=True,
        )
        await self._process_rows(entity_type, model, rows, "narrative_id", _narrative_source_text)

    async def _rebuild_events(self, model: str) -> None:
        entity_type = "event"
        rows = await self.db.execute(
            "SELECT event_id, embedding_text, "
            "JSON_UNQUOTE(JSON_EXTRACT(env_context, '$.input')) AS input_content, "
            f"final_output FROM events "
            f"WHERE user_id = %s AND ({_EVENT_TEXT_FILTER})",
            (self.user_id,),
            fetch=True,
        )
        await self._process_rows(entity_type, model, rows, "event_id", _event_source_text)

    async def _rebuild_jobs(self, model: str) -> None:
        entity_type = "job"
        rows = await self.db.execute(
            f"SELECT job_id, title, description, payload FROM instance_jobs "
            f"WHERE user_id = %s AND ({_JOB_TEXT_FILTER})",
            (self.user_id,),
            fetch=True,
        )
        await self._process_rows(entity_type, model, rows, "job_id", _job_source_text)

    async def _rebuild_entities(self, model: str) -> None:
        entity_type = "entity"
        rows = await self.db.execute(
            "SELECT ise.entity_id, ise.entity_name, ise.entity_description, ise.tags "
            "FROM instance_social_entities ise "
            "JOIN module_instances mi ON mi.instance_id = ise.instance_id "
            f"WHERE mi.user_id = %s AND ({_ENTITY_TEXT_FILTER})",
            (self.user_id,),
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
        progress = get_migration_progress(self.user_id)

        if not rows:
            progress.total[entity_type] = 0
            progress.completed[entity_type] = 0
            progress.failed[entity_type] = 0
            return

        all_ids = [row[id_field] for row in rows]
        existing = await self.emb_repo.get_vectors_by_ids(
            entity_type, all_ids, model
        )
        rows_to_process = [r for r in rows if r[id_field] not in existing]

        progress.total[entity_type] = len(rows_to_process)
        progress.completed[entity_type] = 0
        progress.failed[entity_type] = 0

        logger.info(
            f"[EmbeddingMigration] user={self.user_id} [{entity_type}] "
            f"{len(rows_to_process)} need embedding "
            f"({len(existing)} already done, {len(rows)} total)"
        )

        for i in range(0, len(rows_to_process), self.BATCH_SIZE):
            batch = rows_to_process[i:i + self.BATCH_SIZE]
            records = []

            for row in batch:
                entity_id = row[id_field]
                source_text = source_text_fn(row)
                if not source_text.strip():
                    progress.completed[entity_type] += 1
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
                        "source_text": source_text[:2000],
                    })
                except Exception as e:
                    logger.warning(
                        f"[EmbeddingMigration] user={self.user_id} "
                        f"[{entity_type}] Failed to embed {entity_id}: {e}"
                    )
                    progress.failed[entity_type] += 1

            if records:
                await self.emb_repo.upsert_batch(records)
                progress.completed[entity_type] += len(records)

            if i + self.BATCH_SIZE < len(rows_to_process):
                await asyncio.sleep(0.1)

        logger.info(
            f"[EmbeddingMigration] user={self.user_id} [{entity_type}] Done: "
            f"{progress.completed[entity_type]} completed, "
            f"{progress.failed[entity_type]} failed"
        )
