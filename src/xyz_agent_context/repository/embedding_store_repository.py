"""
@file_name: embedding_store_repository.py
@author: Bin Liang
@date: 2026-03-23
@description: Repository for the embeddings_store table

Provides CRUD operations for multi-model embedding vector storage.
Supports lazy migration: vectors from different models coexist,
and queries filter by the currently active model.

Usage:
    from xyz_agent_context.repository import EmbeddingStoreRepository

    repo = EmbeddingStoreRepository(db_client)

    # Store an embedding
    await repo.upsert("narrative", "nar_abc", "BAAI/bge-m3", 1024, [0.1, ...], "source text")

    # Query embeddings for a specific model
    rows = await repo.get_by_model("narrative", "BAAI/bge-m3")

    # Get embedding for a specific entity
    vector = await repo.get_vector("narrative", "nar_abc", "BAAI/bge-m3")
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger


class EmbeddingStoreRepository:
    """
    Repository for the embeddings_store table.

    Unlike other repositories that extend BaseRepository[T], this one
    operates directly on dicts since the data structure is simple and
    the primary use case is vector storage/retrieval rather than entity mapping.
    """

    TABLE = "embeddings_store"

    def __init__(self, db_client: Any):
        """
        Args:
            db_client: AsyncDatabaseClient instance
        """
        self.db = db_client

    # ---- Write Operations ----

    async def upsert(
        self,
        entity_type: str,
        entity_id: str,
        model: str,
        dimensions: int,
        vector: list[float],
        source_text: Optional[str] = None,
    ) -> None:
        """
        Insert or update an embedding vector.

        Uses INSERT ... ON DUPLICATE KEY UPDATE to atomically create or replace.

        Args:
            entity_type: Entity type ('narrative', 'job', 'entity')
            entity_id: Entity primary key
            model: Embedding model ID
            dimensions: Vector dimensions
            vector: Embedding vector
            source_text: Original text (optional, for re-embedding)
        """
        vector_json = json.dumps(vector)
        sql = f"""
            INSERT INTO {self.TABLE}
                (entity_type, entity_id, model, dimensions, vector, source_text)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                dimensions = VALUES(dimensions),
                vector = VALUES(vector),
                source_text = VALUES(source_text),
                updated_at = CURRENT_TIMESTAMP(6)
        """
        await self.db.execute(
            sql,
            (entity_type, entity_id, model, dimensions, vector_json, source_text),
        )

    async def upsert_batch(
        self,
        records: list[dict],
    ) -> int:
        """
        Batch upsert multiple embeddings.

        Args:
            records: List of dicts with keys:
                entity_type, entity_id, model, dimensions, vector, source_text

        Returns:
            Number of records processed
        """
        if not records:
            return 0

        sql = f"""
            INSERT INTO {self.TABLE}
                (entity_type, entity_id, model, dimensions, vector, source_text)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                dimensions = VALUES(dimensions),
                vector = VALUES(vector),
                source_text = VALUES(source_text),
                updated_at = CURRENT_TIMESTAMP(6)
        """
        params = [
            (
                r["entity_type"],
                r["entity_id"],
                r["model"],
                r["dimensions"],
                json.dumps(r["vector"]),
                r.get("source_text"),
            )
            for r in records
        ]
        for p in params:
            await self.db.execute(sql, p)
        return len(params)

    # ---- Read Operations ----

    async def get_vector(
        self,
        entity_type: str,
        entity_id: str,
        model: str,
    ) -> Optional[list[float]]:
        """
        Get the embedding vector for a specific entity and model.

        Returns:
            The vector as a list of floats, or None if not found.
        """
        sql = f"""
            SELECT vector FROM {self.TABLE}
            WHERE entity_type = %s AND entity_id = %s AND model = %s
        """
        rows = await self.db.execute(sql, (entity_type, entity_id, model), fetch=True)
        if not rows:
            return None
        raw = rows[0]["vector"]
        return json.loads(raw) if isinstance(raw, str) else raw

    async def get_vectors_by_ids(
        self,
        entity_type: str,
        entity_ids: list[str],
        model: str,
    ) -> dict[str, list[float]]:
        """
        Batch get vectors for multiple entities of the same type and model.

        Args:
            entity_type: Entity type
            entity_ids: List of entity IDs
            model: Embedding model

        Returns:
            Dict mapping entity_id -> vector (only includes entities that have vectors)
        """
        if not entity_ids:
            return {}

        placeholders = ",".join(["%s"] * len(entity_ids))
        sql = f"""
            SELECT entity_id, vector FROM {self.TABLE}
            WHERE entity_type = %s AND model = %s AND entity_id IN ({placeholders})
        """
        params = [entity_type, model] + list(entity_ids)
        rows = await self.db.execute(sql, params, fetch=True)

        result: dict[str, list[float]] = {}
        for row in rows:
            raw = row["vector"]
            result[row["entity_id"]] = json.loads(raw) if isinstance(raw, str) else raw
        return result

    async def get_all_by_model(
        self,
        entity_type: str,
        model: str,
    ) -> list[dict]:
        """
        Get all embeddings for a given entity type and model.

        Returns:
            List of dicts with keys: entity_id, vector, source_text
        """
        sql = f"""
            SELECT entity_id, vector, source_text FROM {self.TABLE}
            WHERE entity_type = %s AND model = %s
        """
        rows = await self.db.execute(sql, (entity_type, model), fetch=True)
        results = []
        for row in rows:
            raw = row["vector"]
            results.append({
                "entity_id": row["entity_id"],
                "vector": json.loads(raw) if isinstance(raw, str) else raw,
                "source_text": row.get("source_text"),
            })
        return results

    async def get_entity_ids_missing_model(
        self,
        entity_type: str,
        all_entity_ids: list[str],
        model: str,
    ) -> list[str]:
        """
        Find entity IDs that don't have an embedding for the given model.

        Useful for lazy migration: find which entities need re-embedding.

        Args:
            entity_type: Entity type
            all_entity_ids: Complete list of entity IDs to check
            model: Target embedding model

        Returns:
            List of entity_ids that are missing embeddings for this model
        """
        if not all_entity_ids:
            return []

        existing = await self.get_vectors_by_ids(entity_type, all_entity_ids, model)
        return [eid for eid in all_entity_ids if eid not in existing]

    # ---- Delete Operations ----

    async def delete_by_entity(self, entity_type: str, entity_id: str) -> int:
        """
        Delete all embeddings for an entity (all models).

        Returns:
            Number of rows deleted
        """
        sql = f"DELETE FROM {self.TABLE} WHERE entity_type = %s AND entity_id = %s"
        result = await self.db.execute(sql, (entity_type, entity_id))
        return result if isinstance(result, int) else 0

    async def delete_by_model(self, entity_type: str, model: str) -> int:
        """
        Delete all embeddings for a specific entity type and model.

        Returns:
            Number of rows deleted
        """
        sql = f"DELETE FROM {self.TABLE} WHERE entity_type = %s AND model = %s"
        result = await self.db.execute(sql, (entity_type, model))
        return result if isinstance(result, int) else 0

    # ---- Stats ----

    async def count_by_model(self, entity_type: str, model: str) -> int:
        """Count the number of embeddings for a given entity type and model."""
        sql = f"""
            SELECT COUNT(*) as cnt FROM {self.TABLE}
            WHERE entity_type = %s AND model = %s
        """
        rows = await self.db.execute(sql, (entity_type, model), fetch=True)
        return rows[0]["cnt"] if rows else 0

    async def get_model_stats(self) -> list[dict]:
        """
        Get embedding count grouped by entity_type and model.

        Returns:
            List of dicts: [{"entity_type": ..., "model": ..., "count": ...}, ...]
        """
        sql = f"""
            SELECT entity_type, model, COUNT(*) as count
            FROM {self.TABLE}
            GROUP BY entity_type, model
            ORDER BY entity_type, model
        """
        rows = await self.db.execute(sql, fetch=True)
        return [dict(row) for row in rows] if rows else []
