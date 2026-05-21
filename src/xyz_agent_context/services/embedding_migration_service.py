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

from xyz_agent_context.agent_framework.api_config import EmbeddingConfig
from xyz_agent_context.agent_framework.llm_api.embedding import (
    EmbeddingClient,
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

async def _resolve_user_embedding_cfg(
    user_id: str,
    resolver=None,
    db=None,
    *,
    raise_on_gating: bool = True,
) -> Optional[EmbeddingConfig]:
    """Resolve the full embedding provider (model + api_key + base_url) for a user.

    The embedding config MUST come from what the user explicitly configured:
      - Cloud multi-tenant: ``resolver`` (ProviderResolver, duck-typed) returns
        either the user's own provider or the system-default free tier.
      - Local / desktop: the user's embedding slot in ``user_providers`` (the
        same table Settings writes to).

    There is deliberately NO fallback to the global ``embedding_config`` holder
    (env / llm_config.json). Falling back there is exactly what made "rebuild
    vector" silently embed against a stale/empty global key — we refuse to read
    embedding credentials from the environment. When nothing is configured we
    return ``None`` so the caller can surface a clear "not configured" error.

    With ``raise_on_gating=True`` (rebuild path) the resolver's gating errors
    (no provider / quota exhausted) propagate so the caller records a clear
    error. With ``raise_on_gating=False`` (status path) they are swallowed and
    ``None`` is returned (status renders an empty/unconfigured model).
    """
    if resolver is not None:
        from xyz_agent_context.agent_framework.provider_resolver import (
            ProviderResolverError,
        )
        try:
            resolved = await resolver.resolve(user_id)
        except ProviderResolverError:
            if raise_on_gating:
                raise
            resolved = None
        if resolved is not None:
            _, _, embedding_cfg, _ = resolved
            if embedding_cfg and embedding_cfg.model:
                return embedding_cfg

    # Local / desktop (resolver disabled): read THIS user's embedding slot
    # straight from `user_providers` — the same table Settings writes to.
    # We can't rely on the global `embedding_config` proxy here: `set_user_config`
    # (the Settings hot-reload) only primes a ContextVar this background task
    # never sees, so the proxy would fall back to the static holder
    # (llm_config.json / .env) and ignore what the user configured.
    #
    # We read ONLY the embedding slot (not get_user_llm_configs, which is
    # all-or-nothing across agent/helper_llm/embedding and raises if any other
    # slot is unconfigured) — an embedding rebuild only needs the embedding slot.
    if db is not None:
        try:
            from xyz_agent_context.agent_framework.user_provider_service import (
                UserProviderService,
            )
            cfg = await UserProviderService(db).get_user_config(user_id)
            emb_slot = cfg.slots.get("embedding") if cfg else None
            prov = cfg.providers.get(emb_slot.provider_id) if (cfg and emb_slot) else None
            if emb_slot and emb_slot.model and prov and prov.api_key:
                return EmbeddingConfig(
                    api_key=prov.api_key,
                    base_url=prov.base_url,
                    model=emb_slot.model,
                )
        except Exception as e:  # pragma: no cover — defensive
            logger.debug(
                f"[EmbeddingMigration] user={user_id}: user_providers embedding "
                f"slot lookup failed ({e})"
            )

    # No env / llm_config.json fallback by design — see docstring.
    return None


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
    """Entities owned by user (via module_instances.user_id).

    COUNT(DISTINCT entity_id), not COUNT(*): the same entity_id can appear
    under multiple module_instances (one social-network instance per agent),
    so the JOIN fans out to one row per (entity_id, instance_id). But
    embeddings_store is keyed on (entity_type, entity_id, model) — one vector
    per entity_id. Counting raw rows would over-count and leave a permanent
    "N missing" the rebuild can never close. Must stay in sync with
    `_user_entity_ids('entity')` (also DISTINCT).
    """
    return (
        "SELECT COUNT(DISTINCT ise.entity_id) AS cnt FROM instance_social_entities ise "
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

    def __init__(self, db_client, user_id: str, resolver=None):
        if not user_id:
            raise ValueError("EmbeddingMigrationService requires a user_id")
        self.db = db_client
        self.user_id = user_id
        # ProviderResolver (duck-typed), injected by the route from
        # app.state.provider_resolver. Lets a background rebuild resolve the
        # user's embedding provider without the request ContextVar.
        self.resolver = resolver
        self.emb_repo = EmbeddingStoreRepository(db_client)
        self._emb_cfg: Optional[EmbeddingConfig] = None
        self._cfg_resolved = False  # distinguishes "not resolved yet" from "resolved to None"
        self._emb_client: Optional[EmbeddingClient] = None

    async def _resolve_cfg(self, *, raise_on_gating: bool) -> Optional[EmbeddingConfig]:
        """Resolve (and cache) this user's embedding provider config.

        Returns ``None`` when the user has no embedding provider configured —
        we never fall back to env / llm_config.json.
        """
        if not self._cfg_resolved:
            self._emb_cfg = await _resolve_user_embedding_cfg(
                self.user_id, self.resolver, self.db, raise_on_gating=raise_on_gating
            )
            self._cfg_resolved = True
        return self._emb_cfg

    def _embedding_client(self, cfg: EmbeddingConfig) -> EmbeddingClient:
        """Build (once) a dedicated embedding client pinned to the user's
        provider — controlled fully by (base_url, api_key, model). Caching is
        off so a background rebuild can never serve another model's cached
        vector."""
        if self._emb_client is None:
            self._emb_client = EmbeddingClient(
                model=cfg.model,
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                enable_cache=False,
            )
        return self._emb_client

    # ---- Status ----

    async def get_status(self) -> dict:
        """
        Report how many of this user's entities already have embeddings for
        the active model vs. how many exist in total.
        """
        # Resolve whether the new store should be used for this user.
        # Status is read-only/display, so gating errors are swallowed and an
        # unconfigured embedding provider renders as an empty model (no env
        # fallback). `or ""` handles the None (not-configured) case.
        if not await self._should_use_store():
            cfg = await self._resolve_cfg(raise_on_gating=False)
            return {
                "model": cfg.model if cfg else "",
                "stats": {},
                "all_done": True,
                "migration": get_migration_progress(self.user_id).to_dict(),
                "legacy_mode": True,
            }

        cfg = await self._resolve_cfg(raise_on_gating=False)
        model = cfg.model if cfg else ""

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

        # Reset progress for this run
        progress.is_running = True
        progress.current_model = ""
        progress.total = {}
        progress.completed = {}
        progress.failed = {}
        progress.error = None
        progress.finished = False

        try:
            # Resolve the user's embedding provider and pin a dedicated client
            # to it BEFORE embedding. Gating errors (no provider / quota) now
            # surface as progress.error instead of a per-row 401 storm. A
            # missing config is a hard error — we never fall back to env.
            cfg = await self._resolve_cfg(raise_on_gating=True)
            if cfg is None:
                raise RuntimeError(
                    "No embedding provider configured for this user. Configure an "
                    "embedding provider in Settings before rebuilding."
                )
            model = cfg.model
            progress.current_model = model
            self._embedding_client(cfg)

            logger.info(
                f"[EmbeddingMigration] user={self.user_id}: starting rebuild for "
                f"model={model}"
            )

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
            # DISTINCT — entity_id can recur across module_instances; the
            # embedding store holds one vector per entity_id (see
            # _entity_count_sql).
            sql = (
                "SELECT DISTINCT ise.entity_id FROM instance_social_entities ise "
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

        # Dedup by id — a fan-out JOIN (e.g. an entity_id under several
        # module_instances) can return the same id more than once. The
        # embedding store keys on the id, so embedding it twice is wasted
        # work and would also double the progress totals vs get_status's
        # distinct count. Keep the first row per id.
        seen_ids: set = set()
        deduped_rows = []
        for row in rows:
            rid = row[id_field]
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            deduped_rows.append(row)
        rows = deduped_rows

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
                    # Client is pinned in rebuild_all() before any _rebuild_*()
                    # runs, so it's always set here.
                    vector = await self._emb_client.embed(source_text)
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
