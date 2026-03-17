#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Embedding Utilities

@file_name: embedding.py
@author: NetMind.AI
@date: 2025-11-26
@description: Text embedding generation utilities for semantic search

This module provides utilities for generating text embeddings using OpenAI's
embedding models. These embeddings enable semantic search capabilities across
various modules (Job, Chat, etc.).

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                    EmbeddingClient                          │
    │  ┌─────────────────────────────────────────────────────────┐│
    │  │  OpenAI API  │  Local Model (future)  │  Cache Layer   ││
    │  └─────────────────────────────────────────────────────────┘│
    └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │  JobModule  │  ChatModule  │  Other Modules                 │
    │  - Semantic job search                                       │
    │  - Content similarity matching                               │
    └─────────────────────────────────────────────────────────────┘

Features:
    1. OpenAI embedding generation (text-embedding-3-small by default)
    2. Batch processing for multiple texts
    3. In-memory caching (optional) for repeated queries
    4. Async interface for non-blocking operations

Usage:
    from xyz_agent_context.utils.embedding import (
        get_embedding,
        EmbeddingClient,
    )

    # Simple usage
    embedding = await get_embedding("Search for AI news")

    # With custom client
    client = EmbeddingClient(model="text-embedding-3-large")
    embedding = await client.embed("Some text")

Environment Variables:
    OPENAI_API_KEY: OpenAI API key (required)
    OPENAI_EMBEDDING_MODEL: Model to use (default: text-embedding-3-small)
"""

from __future__ import annotations

import hashlib
from typing import Dict, List, Optional

from loguru import logger

# Retry utility for API calls
from xyz_agent_context.utils.retry import with_retry

from xyz_agent_context.settings import settings

# Try to import OpenAI
try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("OpenAI package not installed. Embedding features will be limited.")


# =============================================================================
# Constants
# =============================================================================

# Default embedding model - smaller and faster
DEFAULT_MODEL = "text-embedding-3-small"

# Embedding dimensions by model
MODEL_DIMENSIONS: Dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

# Maximum tokens per request (approximate)
MAX_TOKENS_PER_REQUEST = 8000

# Cache size limit
CACHE_SIZE = 1000


# =============================================================================
# Embedding Client
# =============================================================================

class EmbeddingClient:
    """
    Embedding Client for generating text embeddings.

    This client wraps OpenAI's embedding API and provides:
    - Async interface for non-blocking operations
    - Batch processing with automatic chunking
    - Optional in-memory caching
    - Error handling and retries

    Attributes:
        model: The embedding model to use
        dimensions: The embedding vector dimensions
        enable_cache: Whether to cache embeddings

    Example:
        client = EmbeddingClient()
        embedding = await client.embed("Hello world")
        embeddings = await client.embed_batch(["Text 1", "Text 2"])
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        enable_cache: bool = True,
    ):
        """
        Initialize EmbeddingClient.

        Args:
            model: OpenAI embedding model name (default: text-embedding-3-small)
            api_key: OpenAI API key (default: from environment)
            enable_cache: Whether to enable embedding caching
        """
        if not OPENAI_AVAILABLE:
            raise ImportError(
                "OpenAI package is required for embedding generation. "
                "Install with: pip install openai"
            )

        self.model = model or settings.openai_embedding_model
        self.dimensions = MODEL_DIMENSIONS.get(self.model, 1536)
        self.enable_cache = enable_cache

        # Initialize OpenAI client
        self._client = AsyncOpenAI(api_key=api_key or settings.openai_api_key)

        # In-memory cache: hash(text) -> embedding
        self._cache: Dict[str, List[float]] = {}

        # Cost tracking context (set via set_cost_context before calls)
        self._cost_agent_id: Optional[str] = None
        self._cost_db = None

        logger.debug(f"EmbeddingClient initialized with model: {self.model}")

    def set_cost_context(self, agent_id: str, db) -> None:
        """
        Set cost tracking context for subsequent embedding calls.

        Args:
            agent_id: Agent that incurs the cost
            db: AsyncDatabaseClient instance
        """
        self._cost_agent_id = agent_id
        self._cost_db = db

    async def _try_record_cost(self, usage) -> None:
        """Record embedding cost if tracking context is available."""
        if not usage:
            return
        # Resolve cost context: instance vars > global ContextVar
        agent_id = self._cost_agent_id
        db = self._cost_db
        if not agent_id or not db:
            from xyz_agent_context.utils.cost_tracker import get_cost_context
            ctx = get_cost_context()
            if ctx:
                agent_id, db = ctx
        if not agent_id or not db:
            return
        try:
            from xyz_agent_context.utils.cost_tracker import record_cost
            input_tokens = getattr(usage, "total_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0
            if input_tokens > 0:
                await record_cost(
                    db=db,
                    agent_id=agent_id,
                    event_id=None,
                    call_type="embedding",
                    model=self.model,
                    input_tokens=input_tokens,
                    output_tokens=0,
                )
        except Exception as e:
            logger.warning(f"Failed to record embedding cost: {e}")

    def _get_cache_key(self, text: str) -> str:
        """Generate cache key from text."""
        return hashlib.md5(f"{self.model}:{text}".encode()).hexdigest()

    @with_retry(
        max_attempts=3,
        delay=1.0,
        backoff=2.0,
        exceptions=(ConnectionError, TimeoutError, OSError),
    )
    async def _make_embedding_request(self, text: str) -> List[float]:
        """
        Make embedding API request with retry.

        This is an internal method that handles the actual API call
        with automatic retry for transient failures.
        """
        response = await self._client.embeddings.create(
            model=self.model,
            input=text,
        )
        # Record embedding cost if tracking context is set
        await self._try_record_cost(getattr(response, "usage", None))
        return response.data[0].embedding

    async def embed(self, text: str) -> List[float]:
        """
        Generate embedding for a single text.

        Args:
            text: The text to embed

        Returns:
            List of floats representing the embedding vector

        Example:
            embedding = await client.embed("Search for machine learning papers")
            # Returns: [0.012, -0.034, 0.056, ...] (1536 dimensions)

        Note:
            This method includes automatic retry for transient network failures.
        """
        # Check cache first
        if self.enable_cache:
            cache_key = self._get_cache_key(text)
            if cache_key in self._cache:
                logger.debug(f"Embedding cache hit for text: {text[:50]}...")
                return self._cache[cache_key]

        try:
            # Use retry-enabled method for API call
            embedding = await self._make_embedding_request(text)

            # Cache the result
            if self.enable_cache:
                if len(self._cache) >= CACHE_SIZE:
                    # Simple cache eviction: remove oldest half
                    keys_to_remove = list(self._cache.keys())[:CACHE_SIZE // 2]
                    for key in keys_to_remove:
                        del self._cache[key]
                self._cache[cache_key] = embedding

            return embedding

        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            raise

    async def embed_batch(
        self,
        texts: List[str],
        batch_size: int = 100
    ) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.

        Processes texts in batches to handle large lists efficiently
        and stay within API limits.

        Args:
            texts: List of texts to embed
            batch_size: Number of texts per API call (default: 100)

        Returns:
            List of embedding vectors, one per input text

        Example:
            texts = ["Query 1", "Query 2", "Query 3"]
            embeddings = await client.embed_batch(texts)
            # Returns: [[0.01, ...], [0.02, ...], [0.03, ...]]
        """
        if not texts:
            return []

        # Check cache for all texts
        results: List[Optional[List[float]]] = [None] * len(texts)
        texts_to_embed: List[tuple[int, str]] = []

        if self.enable_cache:
            for i, text in enumerate(texts):
                cache_key = self._get_cache_key(text)
                if cache_key in self._cache:
                    results[i] = self._cache[cache_key]
                else:
                    texts_to_embed.append((i, text))
        else:
            texts_to_embed = list(enumerate(texts))

        # If all cached, return early
        if not texts_to_embed:
            return [r for r in results if r is not None]

        # Process in batches with retry
        try:
            for batch_start in range(0, len(texts_to_embed), batch_size):
                batch = texts_to_embed[batch_start:batch_start + batch_size]
                batch_texts = [text for _, text in batch]

                # Use retry-enabled method for API call
                response = await self._make_batch_embedding_request(batch_texts)

                # Map results back to original positions
                for (original_idx, text), embedding_data in zip(
                    batch, response.data
                ):
                    embedding = embedding_data.embedding
                    results[original_idx] = embedding

                    # Cache the result
                    if self.enable_cache:
                        cache_key = self._get_cache_key(text)
                        self._cache[cache_key] = embedding

            # Filter out None values (shouldn't happen, but safety check)
            return [r for r in results if r is not None]

        except Exception as e:
            logger.error(f"Error generating batch embeddings: {e}")
            raise

    @with_retry(
        max_attempts=3,
        delay=1.0,
        backoff=2.0,
        exceptions=(ConnectionError, TimeoutError, OSError),
    )
    async def _make_batch_embedding_request(self, texts: List[str]):
        """
        Make batch embedding API request with retry.

        This is an internal method that handles the actual API call
        with automatic retry for transient failures.
        """
        response = await self._client.embeddings.create(
            model=self.model,
            input=texts,
        )
        await self._try_record_cost(getattr(response, "usage", None))
        return response

    def clear_cache(self) -> None:
        """Clear the embedding cache."""
        self._cache.clear()
        logger.debug("Embedding cache cleared")

    @property
    def cache_size(self) -> int:
        """Get current cache size."""
        return len(self._cache)


# =============================================================================
# Global Client Instance (Lazy Initialization)
# =============================================================================

_global_client: Optional[EmbeddingClient] = None


def _get_global_client() -> EmbeddingClient:
    """Get or create the global embedding client."""
    global _global_client
    if _global_client is None:
        _global_client = EmbeddingClient()
    return _global_client


# =============================================================================
# Convenience Functions
# =============================================================================

async def get_embedding(
    text: str,
    model: Optional[str] = None
) -> List[float]:
    """
    Generate embedding for a single text using the global client.

    This is the simplest way to get an embedding. Uses a shared client
    instance with caching enabled.

    Args:
        text: The text to embed
        model: Optional model override (default: text-embedding-3-small)

    Returns:
        List of floats representing the embedding vector

    Example:
        embedding = await get_embedding("Push news every morning at 8am")
        # Use embedding for semantic search
    """
    if model:
        # Create a temporary client for custom model
        client = EmbeddingClient(model=model)
        return await client.embed(text)

    return await _get_global_client().embed(text)


# =============================================================================
# Vector Calculation Utilities
# =============================================================================

def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """
    Calculate cosine similarity between two vectors

    Args:
        vec1: First vector
        vec2: Second vector

    Returns:
        Cosine similarity (between -1 and 1, where 1 means identical)

    Example:
        similarity = cosine_similarity(embedding1, embedding2)
        if similarity > 0.8:
            print("Highly similar")
    """
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0

    try:
        import numpy as np
        v1 = np.array(vec1)
        v2 = np.array(vec2)
        dot = np.dot(v1, v2)
        norm1 = np.linalg.norm(v1)
        norm2 = np.linalg.norm(v2)
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return float(dot / (norm1 * norm2))
    except ImportError:
        # Pure Python implementation (no numpy dependency)
        import math
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot_product / (norm1 * norm2)


def compute_average_embedding(embeddings: List[List[float]]) -> List[float]:
    """
    Compute the average of multiple vectors

    Args:
        embeddings: List of vectors

    Returns:
        Average vector

    Example:
        avg = compute_average_embedding([emb1, emb2, emb3])
    """
    if not embeddings:
        return []
    if len(embeddings) == 1:
        return embeddings[0]

    try:
        import numpy as np
        arr = np.array(embeddings)
        return np.mean(arr, axis=0).tolist()
    except ImportError:
        # Pure Python implementation
        n = len(embeddings)
        dim = len(embeddings[0])
        avg = [0.0] * dim
        for emb in embeddings:
            for i in range(dim):
                avg[i] += emb[i]
        return [x / n for x in avg]


# =============================================================================
# Text Preparation Utilities
# =============================================================================

def prepare_job_text_for_embedding(
    title: str,
    description: str,
    payload: str
) -> str:
    """
    Prepare Job fields for embedding generation.

    Combines Job fields into a single text optimized for semantic search.
    The text is structured to emphasize searchable content.

    Args:
        title: Job title
        description: Job description
        payload: Job execution payload

    Returns:
        Combined text for embedding

    Example:
        text = prepare_job_text_for_embedding(
            title="Daily AI News",
            description="Push AI news every morning",
            payload="Search and summarize AI news..."
        )
        embedding = await get_embedding(text)
    """
    # Combine fields with clear separation
    parts = []

    if title:
        parts.append(f"Title: {title}")

    if description:
        parts.append(f"Description: {description}")

    if payload:
        # Truncate payload if too long (keep first 500 chars)
        payload_text = payload[:500] if len(payload) > 500 else payload
        parts.append(f"Task: {payload_text}")

    return "\n".join(parts)


