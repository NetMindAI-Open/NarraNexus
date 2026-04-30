"""
@file_name: model_catalog.py
@author: Bin Liang
@date: 2026-03-23
@description: Static model catalog — default model lists and metadata lookup

Provides:
- Default model lists for auto-populating providers (NetMind, Claude OAuth, etc.)
- Metadata lookup for known models (embedding dimensions, max output tokens)

The catalog is NOT indexed by preset/source. Instead:
- get_default_models(source, protocol) returns default model IDs for pre-population
- get_embedding_dimensions(model_id) / get_max_output_tokens(model_id) do global lookups

Usage:
    from xyz_agent_context.agent_framework.model_catalog import (
        get_default_models,
        get_embedding_dimensions,
        get_max_output_tokens,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# Model Metadata
# =============================================================================

@dataclass(frozen=True)
class ModelMeta:
    """Known metadata for a model (dimensions, output limits, etc.)"""
    model_id: str
    display_name: str
    dimensions: Optional[int] = None          # Embedding dimensions
    max_output_tokens: Optional[int] = None   # 90% of model limit


# =============================================================================
# Known Model Metadata Registry
# =============================================================================

_KNOWN_MODELS: dict[str, ModelMeta] = {}


def _register(*models: ModelMeta) -> None:
    for m in models:
        _KNOWN_MODELS[m.model_id] = m


# --- NetMind models ---
# `max_output_tokens` left None for newer entries whose official limits
# we have not yet verified — callers fall back to the provider's own cap.
_register(
    ModelMeta("minimax/minimax-m2.7", "MiniMax M2.7", max_output_tokens=58982),
    ModelMeta("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro", max_output_tokens=58982),
    ModelMeta("google/gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash Lite", max_output_tokens=58982),
    ModelMeta("moonshotai/Kimi-K2.5", "Kimi K2.5", max_output_tokens=58981),
    ModelMeta("moonshotai/Kimi-K2.6", "Kimi K2.6"),
    ModelMeta("zai-org/GLM-5", "GLM-5", max_output_tokens=117964),
    ModelMeta("zai-org/GLM-5.1", "GLM-5.1", max_output_tokens=117964),
    ModelMeta("deepseek-ai/DeepSeek-V3", "DeepSeek V3", max_output_tokens=7200),
    ModelMeta("deepseek-ai/DeepSeek-V4-Pro", "DeepSeek V4 Pro"),
    ModelMeta("deepseek-ai/DeepSeek-V4-Flash", "DeepSeek V4 Flash"),
    ModelMeta("Qwen/Qwen3.6-Plus", "Qwen3.6 Plus"),
    ModelMeta("Qwen/Qwen3.6-Flash", "Qwen3.6 Flash"),
    ModelMeta("Qwen/Qwen3.6-35B-A3B", "Qwen3.6 35B-A3B"),
    ModelMeta("BAAI/bge-m3", "BGE-M3 (Multilingual)", dimensions=1024),
    ModelMeta("nvidia/NV-Embed-v2", "NV-Embed-v2", dimensions=4096),
    ModelMeta("dunzhang/stella_en_1.5B_v5", "Stella EN 1.5B v5", dimensions=1024),
)

# --- Anthropic / Claude models ---
# max_output_tokens left None for models whose official limits we haven't
# independently verified; callers fall back to the provider's own cap.
_register(
    ModelMeta("claude-opus-4-7", "Claude Opus 4.7", max_output_tokens=115200),
    ModelMeta("claude-sonnet-4-6", "Claude Sonnet 4.6", max_output_tokens=115200),
    ModelMeta("claude-haiku-4-5", "Claude Haiku 4.5"),
    ModelMeta("claude-haiku-4-5-20251001", "Claude Haiku 4.5 (2025-10-01)"),
)

# --- OpenAI models ---
# Text / chat / reasoning models surfaced as in-UI suggestions. Embeddings
# stay alongside because the embedding slot filters by dimensions != None.
_register(
    ModelMeta("gpt-5.4", "GPT-5.4"),
    ModelMeta("gpt-5.4-mini", "GPT-5.4 Mini"),
    ModelMeta("gpt-5.4-nano", "GPT-5.4 Nano"),
    ModelMeta("gpt-5.2", "GPT-5.2"),
    ModelMeta("gpt-5.2-mini", "GPT-5.2 Mini"),
    ModelMeta("gpt-5.1", "GPT-5.1"),
    ModelMeta("gpt-5", "GPT-5"),
    ModelMeta("gpt-4.1", "GPT-4.1"),
    ModelMeta("o4-mini", "o4-mini (reasoning)"),
    ModelMeta("o3", "o3 (reasoning)"),
    ModelMeta("text-embedding-3-small", "Embedding 3 Small", dimensions=1536),
    ModelMeta("text-embedding-3-large", "Embedding 3 Large", dimensions=3072),
)


# =============================================================================
# Default Model Lists (for pre-populating providers)
# =============================================================================

# Key: (source, protocol) → list of default model IDs
_DEFAULT_MODELS: dict[tuple[str, str], list[str]] = {
    # NetMind Anthropic protocol → agent models
    ("netmind", "anthropic"): [
        "minimax/minimax-m2.7",
        "deepseek-ai/DeepSeek-V4-Pro",
        "deepseek-ai/DeepSeek-V4-Flash",
        "Qwen/Qwen3.6-Plus",
        "Qwen/Qwen3.6-Flash",
        "zai-org/GLM-5.1",
    ],
    # NetMind OpenAI protocol → helper_llm + embedding models
    ("netmind", "openai"): [
        "minimax/minimax-m2.7",
        "google/gemini-3.1-pro-preview",
        "google/gemini-3.1-flash-lite-preview",
        "moonshotai/Kimi-K2.5",
        "moonshotai/Kimi-K2.6",
        "zai-org/GLM-5",
        "zai-org/GLM-5.1",
        "deepseek-ai/DeepSeek-V3",
        "deepseek-ai/DeepSeek-V4-Pro",
        "deepseek-ai/DeepSeek-V4-Flash",
        "Qwen/Qwen3.6-Plus",
        "Qwen/Qwen3.6-Flash",
        "Qwen/Qwen3.6-35B-A3B",
        "BAAI/bge-m3",
        "nvidia/NV-Embed-v2",
        "dunzhang/stella_en_1.5B_v5",
    ],
    # Yunwu Anthropic protocol → Claude models (Yunwu proxies official Claude)
    ("yunwu", "anthropic"): [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
    # Yunwu OpenAI protocol → OpenAI models (Yunwu proxies official OpenAI)
    ("yunwu", "openai"): [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.2",
        "gpt-5.1",
        "text-embedding-3-small",
        "text-embedding-3-large",
    ],
    # OpenRouter Anthropic protocol → Claude models (OpenRouter proxies official Claude)
    ("openrouter", "anthropic"): [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
    # OpenRouter OpenAI protocol → OpenAI models (OpenRouter proxies official OpenAI)
    ("openrouter", "openai"): [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.2",
        "gpt-5.1",
        "text-embedding-3-small",
        "text-embedding-3-large",
    ],
    # Claude OAuth → agent models
    ("claude_oauth", "anthropic"): [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
}

# Suggested models when user adds a generic Anthropic / OpenAI provider.
#
# These are the "official channel" pre-populated lists. They feed:
#   - /api/providers/catalog → frontend Section 2 assignment dropdowns
#     (when a custom provider points at api.openai.com / api.anthropic.com)
#   - get_official_models() for the same purpose on the server side
#
# The richer per-vendor chip suggestions (Gemini, GLM, Kimi, Qwen, MiniMax,
# DeepSeek, …) live in the frontend as MODEL_SUGGESTION_GROUPS — those are
# UI affordances for the create form, not authoritative capability data,
# and every vendor we include there is accessed via OpenAI-compatible proxy,
# so they all fall under the "openai" protocol too.
_SUGGESTED_MODELS: dict[str, list[str]] = {
    "anthropic": [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "claude-haiku-4-5-20251001",
    ],
    "openai": [
        # Top-10 most recent text / chat / reasoning models.
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.2",
        "gpt-5.2-mini",
        "gpt-5.1",
        "gpt-5",
        "gpt-4.1",
        "o4-mini",
        "o3",
        # Embeddings — not text models, kept here because the embedding
        # slot filters this list by `dimensions != None`.
        "text-embedding-3-small",
        "text-embedding-3-large",
    ],
}


# =============================================================================
# Query Functions
# =============================================================================

def get_default_models(source: str, protocol: str) -> list[str]:
    """
    Get default model IDs for a provider source + protocol combination.

    Used to pre-populate the models list when a provider is created.
    For user-created providers, returns suggested models based on protocol.

    Args:
        source: Provider source ("netmind", "claude_oauth", "user")
        protocol: Provider protocol ("anthropic", "openai")

    Returns:
        List of model ID strings
    """
    # Check exact (source, protocol) match first
    defaults = _DEFAULT_MODELS.get((source, protocol))
    if defaults is not None:
        return list(defaults)

    # For user-created providers, return suggestions based on protocol
    if source == "user":
        return list(_SUGGESTED_MODELS.get(protocol, []))

    return []


def get_embedding_dimensions(model_id: str) -> Optional[int]:
    """
    Look up the embedding dimensions for a given model ID.

    Returns None if the model is not found or is not an embedding model.
    """
    meta = _KNOWN_MODELS.get(model_id)
    return meta.dimensions if meta else None


def get_max_output_tokens(model_id: str) -> Optional[int]:
    """
    Look up the max output tokens for a given model ID.

    Returns None if the model is not found.
    """
    meta = _KNOWN_MODELS.get(model_id)
    return meta.max_output_tokens if meta else None


def get_model_display_name(model_id: str) -> str:
    """
    Get a human-readable display name for a model.

    Falls back to the model_id itself if not in the catalog.
    """
    meta = _KNOWN_MODELS.get(model_id)
    return meta.display_name if meta else model_id


def get_all_known_models() -> dict[str, dict]:
    """
    Get all known model metadata for API/frontend use.

    Returns:
        Dict mapping model_id to metadata dict
    """
    return {
        model_id: {
            "model_id": m.model_id,
            "display_name": m.display_name,
            "dimensions": m.dimensions,
            "max_output_tokens": m.max_output_tokens,
        }
        for model_id, m in _KNOWN_MODELS.items()
    }


def get_suggested_models(protocol: str) -> list[str]:
    """
    Get suggested model IDs for a given protocol.

    Used by the frontend to show suggestions when the user adds
    a new Anthropic/OpenAI protocol provider.
    """
    return list(_SUGGESTED_MODELS.get(protocol, []))


# =============================================================================
# Official Provider Detection
# =============================================================================

OFFICIAL_BASE_URLS: dict[str, set[str]] = {
    "openai": {"", "https://api.openai.com/v1", "https://api.openai.com/v1/"},
    "anthropic": {"", "https://api.anthropic.com", "https://api.anthropic.com/"},
}


def is_official_provider(protocol: str, base_url: str) -> bool:
    """Check if a base_url belongs to an official provider."""
    return base_url in OFFICIAL_BASE_URLS.get(protocol, set())


def get_official_models(protocol: str) -> list[str]:
    """Get the full model list for an official provider (OpenAI or Anthropic)."""
    return list(_SUGGESTED_MODELS.get(protocol, []))


def get_known_embedding_models() -> list[dict]:
    """
    Get all known embedding models (hardcoded, not user-configurable).

    Returns list of {model_id, display_name, dimensions} for the frontend.
    """
    return [
        {
            "model_id": m.model_id,
            "display_name": m.display_name,
            "dimensions": m.dimensions,
        }
        for m in _KNOWN_MODELS.values()
        if m.dimensions is not None
    ]
