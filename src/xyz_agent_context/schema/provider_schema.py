"""
@file_name: provider_schema.py
@author: Bin Liang
@date: 2026-03-23
@description: LLM Provider and Slot configuration data models

Defines the schema for the multi-provider LLM configuration system.
Users can configure multiple providers (NetMind, OpenAI, Anthropic, or custom)
and assign them to different functional slots (agent, embedding, helper_llm).

Core concepts:
- Provider: A connection to an LLM service (api_key + base_url + protocol)
- Slot: A functional role in the system that requires a specific protocol
- Source: How the provider was created (see ProviderSource enum)
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================

class ProviderProtocol(str, Enum):
    """API protocol type that determines how requests are formatted"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    # Future: GEMINI = "gemini"


class AuthType(str, Enum):
    """Authentication method for the provider"""
    API_KEY = "api_key"              # Standard API key (X-Api-Key header for Anthropic, Bearer for OpenAI)
    BEARER_TOKEN = "bearer_token"    # Custom Bearer token (e.g., NetMind key via Anthropic protocol)
    OAUTH = "oauth"                  # Claude Code CLI managed OAuth (no key needed)


class ProviderSource(str, Enum):
    """How this provider was created (informational, not logic-driving)"""
    NETMIND = "netmind"            # Auto-created from NetMind one-key card
    CLAUDE_OAUTH = "claude_oauth"  # Auto-created from Claude Code Login card
    USER = "user"                  # User-configured (Anthropic/OpenAI protocol cards)


class SlotName(str, Enum):
    """Functional slots in the system, each requiring an LLM provider"""
    AGENT = "agent"              # Main Agent Loop (dialogue)
    EMBEDDING = "embedding"      # Vector embedding generation
    HELPER_LLM = "helper_llm"   # Auxiliary LLM calls (entity extraction, narrative update, etc.)


# =============================================================================
# Provider Configuration
# =============================================================================

class ProviderConfig(BaseModel):
    """
    A single LLM provider connection configuration.

    One physical API key may produce multiple ProviderConfig entries
    if it supports different protocols (e.g., NetMind key -> anthropic + openai).
    These are linked via `linked_group`.
    """
    provider_id: str = Field(..., description="Unique identifier, e.g. 'prov_a1b2c3d4'")
    name: str = Field(..., description="Display name, e.g. 'NetMind (Anthropic)'")
    source: ProviderSource = Field(..., description="How this provider was created")
    protocol: ProviderProtocol = Field(..., description="API protocol")
    auth_type: AuthType = Field(..., description="Authentication method")
    api_key: str = Field(default="", description="API key or token")
    base_url: str = Field(default="", description="API base URL (empty = provider default)")
    models: list[str] = Field(default_factory=list, description="Available model IDs on this provider")
    linked_group: str = Field(default="", description="Group ID linking providers from the same key")
    is_active: bool = Field(default=True, description="Whether this provider is enabled")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}


# =============================================================================
# Slot Configuration
# =============================================================================

class SlotConfig(BaseModel):
    """
    Assignment of a provider + model to a functional slot.

    The provider's protocol must match the slot's required protocol
    (validated by ProviderRegistry).
    """
    provider_id: str = Field(..., description="Reference to ProviderConfig.provider_id")
    model: str = Field(..., description="Model name, e.g. 'BAAI/bge-m3'")


# =============================================================================
# Top-level Configuration
# =============================================================================

class LLMConfig(BaseModel):
    """
    Complete LLM configuration persisted to ~/.nexusagent/llm_config.json.

    Contains all provider definitions and slot assignments.
    """
    version: str = Field(default="1.0", description="Config schema version")
    providers: dict[str, ProviderConfig] = Field(
        default_factory=dict,
        description="Map of provider_id -> ProviderConfig",
    )
    slots: dict[str, SlotConfig] = Field(
        default_factory=dict,
        description="Map of slot name -> SlotConfig (keys: agent, embedding, helper_llm)",
    )


# =============================================================================
# Slot Protocol Requirements (runtime metadata, not persisted)
# =============================================================================

SLOT_REQUIRED_PROTOCOLS: dict[str, list[ProviderProtocol]] = {
    SlotName.AGENT: [ProviderProtocol.ANTHROPIC],
    SlotName.EMBEDDING: [ProviderProtocol.OPENAI],
    SlotName.HELPER_LLM: [ProviderProtocol.OPENAI],
}
"""
Maps each slot to the list of protocols it currently supports.
When a user assigns a provider to a slot, the provider's protocol
must be in this list. Expand the lists as new adapters are added.
"""
