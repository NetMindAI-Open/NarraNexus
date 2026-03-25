"""
LLM API Package

@file_name: __init__.py
@description: LLM API utilities including embedding generation

Exports:
- EmbeddingClient: Text embedding generation (OpenAI)
- Convenience functions: get_embedding, cosine_similarity, etc.
"""

from xyz_agent_context.agent_framework.llm_api.embedding import (
    EmbeddingClient,
    get_embedding,
    prepare_job_text_for_embedding,
    cosine_similarity,
    compute_average_embedding,
)

__all__ = [
    "EmbeddingClient",
    "get_embedding",
    "prepare_job_text_for_embedding",
    "cosine_similarity",
    "compute_average_embedding",
]
