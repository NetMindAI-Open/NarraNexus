"""
Narrative vector store

@file_name: vector_store.py
@author: NetMind.AI
@date: 2025-12-22
@description: Memory-cached vector store with on-demand database loading
"""

from __future__ import annotations

from typing import Dict, List, Optional, TYPE_CHECKING

from loguru import logger

from ..models import NarrativeSearchResult

if TYPE_CHECKING:
    from xyz_agent_context.utils import DatabaseClient


class VectorStore:
    """
    Narrative Vector Store

    Features:
    1. In-memory cached embedding vectors
    2. On-demand loading from database
    3. Similarity search
    """

    def __init__(self):
        """Initialize vector store"""
        self._embeddings: Dict[str, List[float]] = {}
        self._metadata: Dict[str, Dict[str, str]] = {}
        self._loaded_filters: set = set()

        # Try to import numpy
        self._use_numpy = False
        try:
            import numpy as np
            self._np = np
            self._use_numpy = True
        except ImportError:
            pass

        logger.debug("VectorStore initialized")

    async def load_from_db(
        self,
        db_client: "DatabaseClient",
        agent_id: str,
        user_id: Optional[str] = None
    ) -> int:
        """
        Load embedding vectors from database into memory

        Prefers embeddings_store (multi-model) over legacy routing_embedding column.
        Falls back to the legacy column if embeddings_store has no data.

        Args:
            db_client: Database client
            agent_id: Agent ID
            user_id: User ID (optional)

        Returns:
            Number of loaded items
        """
        filter_key = (agent_id, user_id or "")
        if filter_key in self._loaded_filters:
            return 0

        from xyz_agent_context.agent_framework.llm_api.embedding_store_bridge import (
            use_embedding_store,
            get_stored_embeddings_batch,
        )
        from xyz_agent_context.repository import NarrativeRepository

        narrative_repo = NarrativeRepository(db_client)
        narratives = await narrative_repo.get_with_embedding(
            agent_id=agent_id,
            user_id=user_id,
            limit=1000
        )

        if not narratives:
            self._loaded_filters.add(filter_key)
            return 0

        narrative_ids = [n.id for n in narratives if n.id]
        new_system = use_embedding_store()

        # New system: only use embeddings_store (model-aware, no cross-model mixing)
        # Legacy: only use old routing_embedding column
        store_vectors: dict = {}
        if new_system:
            store_vectors = await get_stored_embeddings_batch("narrative", narrative_ids)

        loaded_count = 0
        for narrative in narratives:
            if not narrative.id:
                continue
            if new_system:
                vector = store_vectors.get(narrative.id)
            else:
                vector = narrative.routing_embedding
            if vector:
                self._embeddings[narrative.id] = vector
                self._metadata[narrative.id] = {
                    "agent_id": agent_id,
                    "user_id": user_id or "",
                }
                loaded_count += 1

        self._loaded_filters.add(filter_key)
        logger.info(f"Loaded {loaded_count} Narrative embeddings ({'embeddings_store' if new_system else 'legacy'})")
        return loaded_count

    async def add(
        self,
        narrative_id: str,
        embedding: List[float],
        metadata: Optional[Dict[str, str]] = None
    ):
        """Add embedding vector"""
        self._embeddings[narrative_id] = embedding
        self._metadata[narrative_id] = metadata or {}

    async def search(
        self,
        query_embedding: List[float],
        filters: Optional[Dict[str, str]] = None,
        top_k: int = 3,
        min_score: float = 0.0,
        db_client=None
    ) -> List[NarrativeSearchResult]:
        """
        Search for similar Narratives

        Args:
            query_embedding: Query vector
            filters: Filter conditions
            top_k: Number of results to return
            min_score: Minimum similarity score
            db_client: Database client (for on-demand loading)

        Returns:
            List of search results
        """
        # On-demand loading from DB
        if not self._embeddings and db_client and filters:
            agent_id = filters.get("agent_id")
            user_id = filters.get("user_id")
            if agent_id:
                await self.load_from_db(db_client, agent_id, user_id)

        # Filter candidates
        candidates = []
        for narrative_id, embedding in self._embeddings.items():
            metadata = self._metadata.get(narrative_id, {})

            if filters:
                match = all(metadata.get(k) == v for k, v in filters.items())
                if match:
                    candidates.append((narrative_id, embedding))
            else:
                candidates.append((narrative_id, embedding))

        if not candidates:
            return []

        # Calculate similarity
        similarities = []
        for narrative_id, embedding in candidates:
            score = self._cosine_similarity(query_embedding, embedding)
            if score >= min_score:
                similarities.append((narrative_id, score))

        # Sort and return Top-K
        similarities.sort(key=lambda x: x[1], reverse=True)
        top_results = similarities[:top_k]

        return [
            NarrativeSearchResult(
                narrative_id=narrative_id,
                similarity_score=score,
                rank=rank + 1
            )
            for rank, (narrative_id, score) in enumerate(top_results)
        ]

    async def get(self, narrative_id: str) -> Optional[List[float]]:
        """Get embedding vector"""
        return self._embeddings.get(narrative_id)

    async def update(self, narrative_id: str, embedding: List[float]):
        """Update embedding vector"""
        if narrative_id in self._embeddings:
            self._embeddings[narrative_id] = embedding

    async def delete(self, narrative_id: str):
        """Delete embedding vector"""
        self._embeddings.pop(narrative_id, None)
        self._metadata.pop(narrative_id, None)

    async def clear(self):
        """Clear all data"""
        self._embeddings.clear()
        self._metadata.clear()
        self._loaded_filters.clear()

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity"""
        if self._use_numpy:
            v1 = self._np.array(vec1)
            v2 = self._np.array(vec2)
            similarity = float(self._np.dot(v1, v2))
        else:
            similarity = sum(a * b for a, b in zip(vec1, vec2))
        return max(0.0, min(1.0, similarity))
