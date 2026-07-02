"""
@file_name: model_catalog.py
@author: Bin Liang
@date: 2026-03-23
@description: Static model catalog — default model lists and metadata lookup

Provides:
- Default model lists for auto-populating providers (NetMind, Claude OAuth, etc.)
- Metadata lookup for known models (max output tokens, etc.)

The catalog is NOT indexed by preset/source. Instead:
- get_default_models(source, protocol) returns default model IDs for pre-population

Usage:
    from xyz_agent_context.agent_framework.model_catalog import (
        get_default_models,
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
    """Known metadata for a model (output limits, etc.)"""
    model_id: str
    display_name: str
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
    # Anthropic Claude routed via NetMind's anthropic-protocol endpoint.
    # NetMind prefixes the upstream model id with "anthropic/", which is
    # how its inference router dispatches; the prefix is part of the
    # model id, not a separate provider. max_output_tokens matches the
    # native Claude limits because NetMind is a transparent proxy here.
    ModelMeta("anthropic/claude-opus-4-8", "Claude Opus 4.8 (NetMind)", max_output_tokens=115200),
    ModelMeta("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6 (NetMind)", max_output_tokens=115200),
    ModelMeta("Qwen/Qwen3.6-Plus", "Qwen3.6 Plus"),
    ModelMeta("Qwen/Qwen3.6-Flash", "Qwen3.6 Flash"),
    ModelMeta("Qwen/Qwen3.6-35B-A3B", "Qwen3.6 35B-A3B"),
)

# --- Anthropic / Claude models ---
# max_output_tokens left None for models whose official limits we haven't
# independently verified; callers fall back to the provider's own cap.
_register(
    ModelMeta("claude-opus-4-8", "Claude Opus 4.8", max_output_tokens=115200),
    ModelMeta("claude-sonnet-4-6", "Claude Sonnet 4.6", max_output_tokens=115200),
    ModelMeta("claude-haiku-4-5", "Claude Haiku 4.5"),
    ModelMeta("claude-haiku-4-5-20251001", "Claude Haiku 4.5 (2025-10-01)"),
    # Claude Code CLI family aliases — always resolve to the latest model of
    # each family. Used by the Claude OAuth candidate list so it never goes
    # stale; only valid on the CLI (`claude --model opus`), not the raw API.
    ModelMeta("opus", "Claude Opus (latest)"),
    ModelMeta("sonnet", "Claude Sonnet (latest)"),
    ModelMeta("haiku", "Claude Haiku (latest)"),
)

# --- OpenAI models ---
# Text / chat / reasoning models surfaced as in-UI suggestions.
_register(
    ModelMeta("gpt-5.5", "GPT-5.5"),
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
)


# =============================================================================
# Default Model Lists (for pre-populating providers)
# =============================================================================

# Key: (source, protocol) → list of default model IDs
_DEFAULT_MODELS: dict[tuple[str, str], list[str]] = {
    # NetMind Anthropic protocol → agent models.
    # claude-opus-4-8 and claude-sonnet-4-6 sit at the top: when a new
    # user adds a NetMind provider we want Claude available out of the
    # box, since the free-tier agent model defaults to Sonnet 4.6.
    ("netmind", "anthropic"): [
        "anthropic/claude-opus-4-8",
        "anthropic/claude-sonnet-4-6",
        "minimax/minimax-m2.7",
        "deepseek-ai/DeepSeek-V4-Pro",
        "deepseek-ai/DeepSeek-V4-Flash",
        "Qwen/Qwen3.6-Plus",
        "Qwen/Qwen3.6-Flash",
        "zai-org/GLM-5.1",
    ],
    # NetMind OpenAI protocol → helper_llm models
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
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
    # Yunwu OpenAI protocol → OpenAI models (Yunwu proxies official OpenAI)
    ("yunwu", "openai"): [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.2",
        "gpt-5.1",
    ],
    # OpenRouter Anthropic protocol → Claude models (OpenRouter proxies official Claude)
    ("openrouter", "anthropic"): [
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
    # OpenRouter OpenAI protocol → OpenAI models (OpenRouter proxies official OpenAI)
    ("openrouter", "openai"): [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.2",
        "gpt-5.1",
    ],
    # Claude OAuth → agent models. Use the Claude Code CLI's family ALIASES
    # (`claude --model opus|sonnet|haiku` resolves to the latest of each family)
    # instead of pinned versions — so the OAuth candidate list auto-tracks the
    # newest Claude release and never needs a manual version bump on every
    # Opus/Sonnet/Haiku update. (Only the OAuth path goes through the CLI, where
    # these aliases are valid; the API-proxy providers above need full ids.)
    ("claude_oauth", "anthropic"): [
        "opus",
        "sonnet",
        "haiku",
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
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "claude-haiku-4-5-20251001",
    ],
    "openai": [
        # Top recent text / chat / reasoning models. ``gpt-5.5`` is
        # the current flagship and the codex CLI default — keep it
        # first so a user adding a generic OpenAI provider for the
        # codex_cli agent framework gets it in the dropdown by
        # default.
        "gpt-5.5",
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
    # Auto-discovered sources: the probe ledger ([[model_probe_ledger]]) is
    # authoritative once populated (overwrite semantics — only models that
    # actually answer on this protocol are listed). Fall back to the hardcoded
    # list when the ledger has no entry yet (fresh checkout before first sync,
    # or a source we haven't probed).
    from xyz_agent_context.agent_framework.model_probe_ledger import ledger_models

    if source in ("netmind", "system_pool", "openrouter", "yunwu"):
        synced = ledger_models(source, protocol)
        if synced:
            return synced

    # Check exact (source, protocol) match first
    defaults = _DEFAULT_MODELS.get((source, protocol))
    if defaults is not None:
        return list(defaults)

    # For user-created providers, return suggestions based on protocol
    if source == "user":
        return list(_SUGGESTED_MODELS.get(protocol, []))

    return []


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
# One-key onboarding defaults
# =============================================================================

# Defaults the /api/providers/onboard endpoint wires when a user pastes a
# single key. BYOK rationale: the user pays with their own key, so the
# agent slot defaults to the strongest model of that family;
# cost-sensitive users downgrade in Settings → Providers (Advanced).
# (The cloud free tier defaults to Sonnet because the PLATFORM pays —
# different scenario, configured in SystemProviderService.)
_ONBOARD_AGENT_MODELS: dict[str, str] = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5.5",          # codex_cli's current flagship default
    # NetMind one-key card: DeepSeek is the cost-effective default on
    # the aggregator; Claude-via-NetMind stays available in the
    # dropdown for users who want it.
    "netmind": "deepseek-ai/DeepSeek-V4-Pro",
    # Yunwu / OpenRouter proxy the official APIs — same defaults as a
    # direct Claude key.
    "yunwu": "claude-opus-4-8",
    "openrouter": "claude-opus-4-8",
}

# Helper does small structured jobs (entity extraction, narrative
# updates, fallback replies) — cheap + fast wins.
_ONBOARD_HELPER_MODELS: dict[str, str] = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-5.4-mini",
    "netmind": "deepseek-ai/DeepSeek-V4-Flash",
    "yunwu": "gpt-5.4-mini",
    "openrouter": "gpt-5.4-mini",
}


def get_default_agent_model(protocol: str) -> str:
    """Default agent-slot model for one-key onboarding, per protocol."""
    return _ONBOARD_AGENT_MODELS.get(protocol, _ONBOARD_AGENT_MODELS["anthropic"])


def get_default_helper_model(protocol: str) -> str:
    """Default helper_llm-slot model for one-key onboarding, per protocol."""
    return _ONBOARD_HELPER_MODELS.get(protocol, _ONBOARD_HELPER_MODELS["openai"])


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