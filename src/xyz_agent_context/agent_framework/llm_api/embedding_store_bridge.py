"""
@file_name: embedding_store_bridge.py
@author: Bin Liang
@date: 2026-03-23
@description: Bridge between embedding generation and the embeddings_store table

Provides a simple helper that write-through to embeddings_store whenever an
embedding is generated. This avoids scattering repository imports across
all modules that generate embeddings.

Usage:
    from xyz_agent_context.agent_framework.llm_api.embedding_store_bridge import (
        store_embedding,
        get_stored_embedding,
        get_stored_embeddings_batch,
    )

    # After generating an embedding, also persist it
    await store_embedding("narrative", "nar_abc", vector, source_text="...")

    # Read from embeddings_store (preferred over old columns)
    vector = await get_stored_embedding("narrative", "nar_abc")
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from xyz_agent_context.agent_framework.api_config import embedding_config


def use_embedding_store() -> bool:
    """Check if the new embeddings_store path should be used.

    Returns True when llm_config.json exists (user has configured the new
    provider system). Returns False for legacy .env-only users, who should
    continue reading from old table columns.
    """
    from xyz_agent_context.agent_framework.provider_registry import provider_registry
    return provider_registry.config_exists()


async def _get_repo():
    """Lazy import to avoid circular dependencies at module load time."""
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.repository.embedding_store_repository import EmbeddingStoreRepository
    db = await get_db_client()
    return EmbeddingStoreRepository(db)


async def store_embedding(
    entity_type: str,
    entity_id: str,
    vector: list[float],
    source_text: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    """
    Store an embedding vector in embeddings_store.

    Args:
        entity_type: 'narrative' | 'event' | 'job' | 'entity'
        entity_id: The entity's primary key
        vector: The embedding vector
        source_text: Original text (for future re-embedding)
        model: Override model name (default: current active model)
    """
    try:
        repo = await _get_repo()
        m = model or embedding_config.model
        dims = len(vector)
        await repo.upsert(
            entity_type=entity_type,
            entity_id=entity_id,
            model=m,
            dimensions=dims,
            vector=vector,
            source_text=source_text[:2000] if source_text else None,
        )
    except Exception as e:
        # Non-fatal: log and continue (don't break the main flow)
        logger.warning(f"Failed to store embedding in embeddings_store: {e}")


async def get_stored_embedding(
    entity_type: str,
    entity_id: str,
    model: Optional[str] = None,
) -> Optional[list[float]]:
    """
    Read an embedding vector from embeddings_store.

    Returns None if not found for the current model.
    """
    try:
        repo = await _get_repo()
        m = model or embedding_config.model
        return await repo.get_vector(entity_type, entity_id, m)
    except Exception as e:
        logger.warning(f"Failed to read embedding from embeddings_store: {e}")
        return None


async def get_stored_embeddings_batch(
    entity_type: str,
    entity_ids: list[str],
    model: Optional[str] = None,
) -> dict[str, list[float]]:
    """
    Batch read embeddings from embeddings_store.

    Returns dict mapping entity_id -> vector (only includes found entries).
    """
    try:
        repo = await _get_repo()
        m = model or embedding_config.model
        return await repo.get_vectors_by_ids(entity_type, entity_ids, m)
    except Exception as e:
        logger.warning(f"Failed to batch read embeddings from embeddings_store: {e}")
        return {}


async def get_all_stored_embeddings(
    entity_type: str,
    model: Optional[str] = None,
) -> list[dict]:
    """
    Get all embeddings for an entity type and model.

    Returns list of {"entity_id": ..., "vector": ..., "source_text": ...}
    """
    try:
        repo = await _get_repo()
        m = model or embedding_config.model
        return await repo.get_all_by_model(entity_type, m)
    except Exception as e:
        logger.warning(f"Failed to get all embeddings from embeddings_store: {e}")
        return []
