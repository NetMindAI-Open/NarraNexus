"""
@file_name: provider_schema.py
@author: Bin Liang
@date: 2026-03-23
@description: LLM Provider and Slot configuration data models

Defines the schema for the multi-provider LLM configuration system.
Users can configure multiple providers (NetMind, OpenAI, Anthropic, or custom)
and assign them to different functional slots (agent, helper_llm).

Core concepts:
- Provider: A connection to an LLM service (api_key + base_url + protocol)
- Slot: A functional role in the system that requires a specific protocol
- Source: How the provider was created (see ProviderSource enum)

Schemas ONLY. The rules that decide which card may be bound to a slot read
the framework registry, which lives above this layer; they are in
``platform.agent_framework.providers.framework_binding``
(``SLOT_REQUIRED_PROTOCOLS`` / ``get_slot_required_protocols`` /
``framework_can_drive_provider``). Keep policy out of here: expressing the
upward dependency as a function-body import is what made the layering
violation invisible to import-linter. ``SUBSCRIPTION_AUTH_TYPES`` is the one
exception that is NOT policy: it is a subset of the ``AuthType`` enum values
(which transports carry a CLI subscription credential), consumed by
``api_config``, the claude driver and ``framework_binding`` alike, so it is
defined ONCE next to the enum and re-exported by ``framework_binding``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

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
    OAUTH_TOKEN = "oauth_token"      # Long-lived subscription token from `claude setup-token`,
    #                                  env-injected as CLAUDE_CODE_OAUTH_TOKEN (no CLI credential store)


SUBSCRIPTION_AUTH_TYPES: frozenset[str] = frozenset(
    {AuthType.OAUTH.value, AuthType.OAUTH_TOKEN.value}
)
"""Auth types that carry a CLI SUBSCRIPTION credential rather than an API key.

Both transports of the same thing: ``oauth`` = the CLI's own credential
store on the host, ``oauth_token`` = a ``setup-token`` long-lived token
env-injected at spawn. Neither can make a direct Messages /
Chat-Completions call — only the CLI that owns the credential can spend it.
This is also the set the Claude Code CLI treats as a subscriber
(``isSubscriber()``): the only auth where the CLI skips its own 429 retry,
which is why the claude driver's transient-retry gate and the parallel-tool
cap in ``api_config`` key on it. THE definition — every other spelling is an
import of this name (``framework_binding`` re-exports it; the frontend copy
in ``lib/agentFramework.ts`` mirrors it by hand).
"""


class ProviderSource(str, Enum):
    """How this provider was created. SECURITY-RELEVANT, not informational.

    USER rows (and rows whose source is missing or unknown, fail closed) carry
    a user-supplied base_url and are gated by the cloud SSRF egress gate on
    save, Test and read (``registry.assert_user_base_url_allowed`` /
    ``registry.is_stored_user_base_url_blocked``); platform rows are trusted
    (netmind_free legitimately points at our internal gateway). Never relabel
    a row's source: that silently switches its egress gate off.

    This enum is NOT the full value domain of ``user_providers.source``: the
    cloud free-tier pool is stored as ``system`` / ``system_pool`` with no
    member here. The trust decision therefore reads
    ``registry.PLATFORM_PROVIDER_SOURCES``, never enum membership.
    """
    NETMIND = "netmind"            # Auto-created from NetMind one-key card
    NETMIND_FREE = "netmind_free"  # Platform free-tier wallet card (see providers/free_tier)
    YUNWU = "yunwu"                # Auto-created from Yunwu one-key card
    OPENROUTER = "openrouter"      # Auto-created from OpenRouter one-key card
    CLAUDE_OAUTH = "claude_oauth"  # Auto-created from Claude Code Login card
    CODEX_OAUTH = "codex_oauth"    # Auto-created from Codex CLI Login card
    USER = "user"                  # User-configured cards; base_url is egress-gated (cloud)


class SlotName(str, Enum):
    """Functional slots in the system, each requiring an LLM provider"""
    AGENT = "agent"              # Main Agent Loop (dialogue)
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
    # Capability: does this provider's endpoint run Anthropic's server-side
    # tools (web_search_20250305, text_editor, computer_use, ...)? Only the
    # official Anthropic API and transparent forward proxies implement these;
    # aggregators such as NetMind / OpenRouter / Yunwu do not, and calling
    # WebSearch against them hangs indefinitely. Default False is the
    # conservative choice for user-added custom providers — the user opts
    # in when they know their proxy forwards to official.
    supports_anthropic_server_tools: bool = Field(
        default=False,
        description=(
            "True only when this provider exposes Anthropic's server-side "
            "tools (web_search, text_editor, etc.). Leave False for most "
            "third-party proxies; the tool-policy hook will deny WebSearch "
            "calls upfront instead of letting them hang."
        ),
    )
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

    Reasoning params are framework-NEUTRAL (rule #9): the slot never stores
    a provider dialect ("adaptive", "minimal", ...). Each agent-framework
    adapter owns the mapping from these values to its own wire format and
    clamps values outside its vocabulary (with a log line, never an error —
    rule #15: we don't police the user's provider choice).
    """
    provider_id: str = Field(..., description="Reference to ProviderConfig.provider_id")
    model: str = Field(..., description="Model name, e.g. 'BAAI/bge-m3'")
    thinking: Literal["", "on", "off"] = Field(
        default="",
        description="Neutral thinking switch: '' = auto (adapter passes nothing)",
    )
    reasoning_effort: Literal["", "low", "medium", "high", "max"] = Field(
        default="",
        description="Neutral reasoning-effort level: '' = auto (adapter passes nothing)",
    )


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
        description="Map of slot name -> SlotConfig (keys: agent, helper_llm)",
    )
