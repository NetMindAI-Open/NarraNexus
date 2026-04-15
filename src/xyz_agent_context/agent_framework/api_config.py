"""
@file_name: api_config.py
@author: Bin Liang
@date: 2026-03-23
@description: Centralized LLM API configuration for all agent framework components

All API keys, base URLs, and model names used by the agent framework are defined
here. Components (Claude SDK, OpenAI Agents SDK, Gemini SDK, Embedding Client)
should read from this module instead of accessing settings/os.environ directly.

Configuration priority:
    1. ~/.nexusagent/llm_config.json (managed by provider_registry)
    2. .env / settings.py (legacy fallback for existing users)

Usage:
    from xyz_agent_context.agent_framework.api_config import (
        claude_config,
        openai_config,
        gemini_config,
        embedding_config,
    )

    # Access config values
    model = openai_config.model
    api_key = embedding_config.api_key
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from xyz_agent_context.schema.provider_schema import (
    AuthType,
    ProviderProtocol,
    SlotName,
)


# =============================================================================
# Configuration Dataclasses (public interface, unchanged)
# =============================================================================

@dataclass(frozen=True)
class ClaudeConfig:
    """Claude API configuration (passed to Claude Code CLI subprocess)"""
    api_key: str = ""
    base_url: str = ""
    model: str = ""          # Empty = let Claude Code CLI use its default model
    auth_type: str = "api_key"  # "api_key" | "bearer_token" | "oauth"

    def to_cli_env(self) -> dict[str, str]:
        """Build env vars dict for Claude Code CLI subprocess.

        Only includes non-empty values to avoid overriding CLI defaults.
        Uses ANTHROPIC_AUTH_TOKEN for bearer_token auth, ANTHROPIC_API_KEY otherwise.
        """
        env: dict[str, str] = {}
        if self.api_key:
            if self.auth_type == "bearer_token":
                env["ANTHROPIC_AUTH_TOKEN"] = self.api_key
            else:
                env["ANTHROPIC_API_KEY"] = self.api_key
        if self.base_url:
            env["ANTHROPIC_BASE_URL"] = self.base_url
        return env


@dataclass(frozen=True)
class OpenAIConfig:
    """OpenAI Chat Completions API configuration (used by helper_llm slot)"""
    api_key: str = ""
    base_url: str = ""  # Empty = default https://api.openai.com/v1
    model: str = "gpt-5.1-2025-11-13"


@dataclass(frozen=True)
class GeminiConfig:
    """Google Gemini API configuration"""
    api_key: str = ""
    model: str = "gemini-2.5-flash"


@dataclass(frozen=True)
class EmbeddingConfig:
    """OpenAI Embedding API configuration"""
    api_key: str = ""
    base_url: str = ""  # Empty = default https://api.openai.com/v1
    model: str = "text-embedding-3-small"
    dimensions: Optional[int] = None  # None = use model default


# =============================================================================
# Config Loading
# =============================================================================

def _load_from_llm_config() -> Optional[tuple[ClaudeConfig, OpenAIConfig, EmbeddingConfig]]:
    """
    Try to load configuration from ~/.nexusagent/llm_config.json.

    Returns:
        Tuple of (claude_config, openai_config, embedding_config) if successful,
        None if the file doesn't exist or is invalid.
    """
    from xyz_agent_context.agent_framework.provider_registry import provider_registry

    config = provider_registry.load()
    if config is None:
        return None

    # Per-slot loading: use whatever slots ARE configured, leave the rest
    # as empty defaults. The caller merges with .env fallback per-slot.
    errors = provider_registry.validate(config)
    if errors:
        logger.info(f"llm_config.json partial config ({len(config.slots)}/3 slots): {errors}")

    # Build ClaudeConfig from agent slot
    agent_slot = config.slots.get(SlotName.AGENT) or config.slots.get("agent")
    agent_provider = config.providers.get(agent_slot.provider_id) if agent_slot else None

    if agent_provider:
        claude = ClaudeConfig(
            api_key=agent_provider.api_key,
            base_url=agent_provider.base_url,
            model=agent_slot.model,
            auth_type=agent_provider.auth_type.value if isinstance(agent_provider.auth_type, AuthType) else agent_provider.auth_type,
        )
    else:
        claude = ClaudeConfig()

    # Build OpenAIConfig from helper_llm slot
    helper_slot = config.slots.get(SlotName.HELPER_LLM) or config.slots.get("helper_llm")
    helper_provider = config.providers.get(helper_slot.provider_id) if helper_slot else None

    if helper_provider:
        openai_cfg = OpenAIConfig(
            api_key=helper_provider.api_key,
            base_url=helper_provider.base_url,
            model=helper_slot.model,
        )
    else:
        openai_cfg = OpenAIConfig()

    # Build EmbeddingConfig from embedding slot
    emb_slot = config.slots.get(SlotName.EMBEDDING) or config.slots.get("embedding")
    emb_provider = config.providers.get(emb_slot.provider_id) if emb_slot else None

    if emb_provider:
        # dimensions is NOT passed to EmbeddingConfig — it's metadata only
        # (for UI display / storage sizing). Passing it as an API request
        # parameter causes errors when switching between models with
        # different native dimensions.
        embedding = EmbeddingConfig(
            api_key=emb_provider.api_key,
            base_url=emb_provider.base_url,
            model=emb_slot.model,
        )
    else:
        embedding = EmbeddingConfig()

    logger.info("LLM config loaded from llm_config.json")
    return claude, openai_cfg, embedding


def _load_from_settings() -> tuple[ClaudeConfig, OpenAIConfig, EmbeddingConfig]:
    """
    Fallback: load configuration from .env / settings.py (legacy path).
    """
    from xyz_agent_context.settings import settings

    claude = ClaudeConfig(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url,
        model=settings.anthropic_model,
    )

    openai_cfg = OpenAIConfig(
        api_key=settings.openai_api_key,
    )

    embedding = EmbeddingConfig(
        api_key=settings.openai_api_key,
        model=settings.openai_embedding_model,
    )

    return claude, openai_cfg, embedding


def _load_gemini_config() -> GeminiConfig:
    """Load Gemini config (always from settings, not part of the slot system yet)."""
    from xyz_agent_context.settings import settings
    return GeminiConfig(api_key=settings.google_api_key)


# =============================================================================
# Lazy-loading config with hot-reload support
# =============================================================================

class _ConfigHolder:
    """
    Holds LLM configs with lazy-loading and hot-reload.

    Config is loaded on first access and cached. Call reload() after
    changing llm_config.json to pick up new settings without restarting.
    """

    def __init__(self) -> None:
        self._claude: Optional[ClaudeConfig] = None
        self._openai: Optional[OpenAIConfig] = None
        self._embedding: Optional[EmbeddingConfig] = None
        self._gemini: Optional[GeminiConfig] = None
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self.reload()

    def reload(self) -> None:
        """Reload config from llm_config.json + .env fallback."""
        json_result = _load_from_llm_config()
        env_claude, env_openai, env_embedding = _load_from_settings()

        if json_result is not None:
            json_claude, json_openai, json_embedding = json_result
            self._claude = json_claude if (json_claude.api_key or json_claude.auth_type == "oauth") else env_claude
            self._openai = json_openai if json_openai.api_key else env_openai
            self._embedding = json_embedding if json_embedding.api_key else env_embedding
        else:
            self._claude, self._openai, self._embedding = env_claude, env_openai, env_embedding

        self._gemini = _load_gemini_config()
        self._loaded = True

        # Log provider summary so it's clear which providers/models are active
        def _mask(k: str) -> str:
            return f"***{k[-4:]}" if k and len(k) > 4 else "(empty)"
        logger.info(
            f"LLM configs (re)loaded:\n"
            f"  Agent:      model={self._claude.model or '(default)'}, "
            f"base_url={self._claude.base_url or '(official)'}, "
            f"auth={self._claude.auth_type}, key={_mask(self._claude.api_key)}\n"
            f"  HelperLLM:  model={self._openai.model}, "
            f"base_url={self._openai.base_url or '(official)'}, "
            f"key={_mask(self._openai.api_key)}\n"
            f"  Embedding:  model={self._embedding.model}, "
            f"base_url={self._embedding.base_url or '(official)'}, "
            f"dims={self._embedding.dimensions or '(default)'}, "
            f"key={_mask(self._embedding.api_key)}"
        )

    @property
    def claude(self) -> ClaudeConfig:
        self._ensure_loaded()
        return self._claude  # type: ignore

    @property
    def openai(self) -> OpenAIConfig:
        self._ensure_loaded()
        return self._openai  # type: ignore

    @property
    def embedding(self) -> EmbeddingConfig:
        self._ensure_loaded()
        return self._embedding  # type: ignore

    @property
    def gemini(self) -> GeminiConfig:
        self._ensure_loaded()
        return self._gemini  # type: ignore


_holder = _ConfigHolder()


# =============================================================================
# Per-coroutine config via ContextVar (multi-tenant concurrency safe)
# =============================================================================
#
# Why ContextVar:
# - asyncio.Task copies the parent context at creation time, so each task
#   started by asyncio.gather() has its own isolated ContextVar state.
# - set_user_config() inside one task does NOT affect sibling tasks.
# - This is critical when multiple background triggers (bus_trigger,
#   job_trigger) concurrently process agents from different owners.
# - Without ContextVar, the global _holder mutation would leak API keys
#   across users (Alice's agent using Bob's API key).
#
# Fallback chain:
# 1. ContextVar value set for current task (per-user, highest priority)
# 2. Global _holder (loaded from llm_config.json or .env on first access)

_claude_ctx: ContextVar[Optional[ClaudeConfig]] = ContextVar("claude_config", default=None)
_openai_ctx: ContextVar[Optional[OpenAIConfig]] = ContextVar("openai_config", default=None)
_embedding_ctx: ContextVar[Optional[EmbeddingConfig]] = ContextVar("embedding_config", default=None)


class _ConfigProxy:
    """
    Proxy that delegates attribute access to the context-local config if
    set, otherwise to the global holder.

    Existing code reads `claude_config.model` etc. — this proxy resolves
    to the right config for the current asyncio task at read time, which
    makes multi-tenant concurrent execution safe.
    """

    def __init__(self, attr_name: str, ctx_var: Optional[ContextVar] = None):
        self._attr_name = attr_name
        self._ctx_var = ctx_var

    def __getattr__(self, name: str):
        # Check context-local override first (per-user in current task)
        if self._ctx_var is not None:
            ctx_val = self._ctx_var.get()
            if ctx_val is not None:
                return getattr(ctx_val, name)
        # Fall back to global holder
        return getattr(getattr(_holder, self._attr_name), name)


claude_config: ClaudeConfig = _ConfigProxy("claude", _claude_ctx)  # type: ignore
openai_config: OpenAIConfig = _ConfigProxy("openai", _openai_ctx)  # type: ignore
embedding_config: EmbeddingConfig = _ConfigProxy("embedding", _embedding_ctx)  # type: ignore
gemini_config: GeminiConfig = _ConfigProxy("gemini")  # type: ignore


def reload_llm_config() -> None:
    """Reload LLM config from disk. Call after llm_config.json changes."""
    _holder.reload()


def set_user_config(claude: ClaudeConfig, openai: OpenAIConfig, embedding: EmbeddingConfig) -> None:
    """
    Set per-user LLM config for the CURRENT asyncio task only.

    This uses ContextVar so concurrent tasks from different users cannot
    see each other's config. Call this at the start of an agent turn
    after loading the owner's config from the database.

    The setting automatically goes out of scope when the task finishes.
    """
    _claude_ctx.set(claude)
    _openai_ctx.set(openai)
    _embedding_ctx.set(embedding)


# =============================================================================
# Per-user config loading (for cloud multi-tenant mode)
# =============================================================================

# =============================================================================
# TODO: LONG-TERM REFACTOR
# =============================================================================
#
# The current design uses ContextVar + module-level proxies to propagate
# per-user LLM config through the agent execution call chain. It works, but
# it's not elegant — it has several issues:
#
# 1. Action at a distance: reading `claude_config.api_key` in any module
#    silently depends on whoever set the ContextVar earlier in the task.
# 2. Hidden contract: every code path that invokes an agent turn MUST call
#    set_user_config first, or the proxy falls through to legacy behavior.
# 3. Type system lies: claude_config is annotated as ClaudeConfig but is
#    actually a _ConfigProxy. Attribute errors won't be caught statically.
# 4. ContextVar only propagates inside asyncio tasks — code using
#    ThreadPoolExecutor or manual loop.call_soon will break silently.
#
# The clean solution is explicit parameter passing: construct a
# RuntimeContext dataclass at the top of AgentRuntime.run() and thread it
# through every component (step_3_agent_loop, ClaudeAgentSDK.agent_loop,
# EmbeddingClient.__init__, etc.). Blast radius is ~20 files, mostly
# mechanical changes to function signatures.
#
# Blocked by: none — just time.
# Priority: medium (current design is safe thanks to fail-fast in
# get_user_llm_configs, so this is cleanup not a bug fix).


class LLMConfigNotConfigured(RuntimeError):
    """Raised when a user's LLM config is missing or incomplete.

    No silent fallback to global defaults — users MUST configure their
    own providers via the Settings page. This prevents accidentally
    billing one user's agent runs to another user (or to the company's
    default keys).
    """


async def get_agent_owner_llm_configs(
    agent_id: str,
) -> tuple[ClaudeConfig, OpenAIConfig, EmbeddingConfig]:
    """
    Load LLM configs for an agent based on its OWNER (agents.created_by).

    This is the correct multi-tenant lookup: LLM API keys are billed to
    the agent owner, not to whoever triggered the agent run. Background
    triggers (bus_trigger, job_trigger) pass arbitrary user_ids that may
    represent other agents or target identities, but LLM billing must
    always go to the owner.

    Raises:
        LLMConfigNotConfigured: if the agent does not exist, has no
            owner, or the owner has not configured all required slots.
            No silent fallback — the caller must surface the error.
    """
    from xyz_agent_context.utils.db_factory import get_db_client

    db = await get_db_client()
    agent_row = await db.get_one("agents", {"agent_id": agent_id})
    if not agent_row:
        raise LLMConfigNotConfigured(
            f"Agent {agent_id!r} not found. Cannot resolve LLM config."
        )
    owner_user_id = agent_row.get("created_by")
    if not owner_user_id:
        raise LLMConfigNotConfigured(
            f"Agent {agent_id!r} has no owner (created_by is empty)."
        )
    return await get_user_llm_configs(owner_user_id)


async def get_user_llm_configs(user_id: str) -> tuple[ClaudeConfig, OpenAIConfig, EmbeddingConfig]:
    """
    Load LLM configs for a specific user from the database.

    Reads from user_providers + user_slots tables. Requires all three
    slots (agent, embedding, helper_llm) to be configured. If ANY slot
    is missing or its provider is broken, raises LLMConfigNotConfigured
    with a clear message — no silent fallback to global defaults.

    Raises:
        LLMConfigNotConfigured: if any slot is missing or invalid.
    """
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.agent_framework.user_provider_service import UserProviderService

    db = await get_db_client()
    service = UserProviderService(db)
    config = await service.get_user_config(user_id)

    # ─── Agent slot ──────────────────────────────────────────────────
    agent_slot = config.slots.get(SlotName.AGENT) or config.slots.get("agent")
    if not agent_slot:
        raise LLMConfigNotConfigured(
            f"User {user_id!r}: 'agent' slot is not configured. "
            "Please add an Anthropic-protocol provider and assign it to "
            "the agent slot in Settings → Providers."
        )
    agent_provider = config.providers.get(agent_slot.provider_id)
    if not agent_provider:
        raise LLMConfigNotConfigured(
            f"User {user_id!r}: agent slot references provider "
            f"{agent_slot.provider_id!r} which no longer exists."
        )
    claude = ClaudeConfig(
        api_key=agent_provider.api_key,
        base_url=agent_provider.base_url,
        model=agent_slot.model,
        auth_type=agent_provider.auth_type.value if isinstance(agent_provider.auth_type, AuthType) else agent_provider.auth_type,
    )

    # ─── Helper LLM slot ─────────────────────────────────────────────
    helper_slot = config.slots.get(SlotName.HELPER_LLM) or config.slots.get("helper_llm")
    if not helper_slot:
        raise LLMConfigNotConfigured(
            f"User {user_id!r}: 'helper_llm' slot is not configured. "
            "Please add an OpenAI-protocol provider and assign it to "
            "the helper_llm slot in Settings → Providers."
        )
    helper_provider = config.providers.get(helper_slot.provider_id)
    if not helper_provider:
        raise LLMConfigNotConfigured(
            f"User {user_id!r}: helper_llm slot references provider "
            f"{helper_slot.provider_id!r} which no longer exists."
        )
    openai_cfg = OpenAIConfig(
        api_key=helper_provider.api_key,
        base_url=helper_provider.base_url,
        model=helper_slot.model,
    )

    # ─── Embedding slot ──────────────────────────────────────────────
    emb_slot = config.slots.get(SlotName.EMBEDDING) or config.slots.get("embedding")
    if not emb_slot:
        raise LLMConfigNotConfigured(
            f"User {user_id!r}: 'embedding' slot is not configured. "
            "Please add an OpenAI-protocol provider and assign it to "
            "the embedding slot in Settings → Providers."
        )
    emb_provider = config.providers.get(emb_slot.provider_id)
    if not emb_provider:
        raise LLMConfigNotConfigured(
            f"User {user_id!r}: embedding slot references provider "
            f"{emb_slot.provider_id!r} which no longer exists."
        )
    embedding = EmbeddingConfig(
        api_key=emb_provider.api_key,
        base_url=emb_provider.base_url,
        model=emb_slot.model,
    )

    return claude, openai_cfg, embedding


async def setup_mcp_llm_context(agent_id: str) -> None:
    """
    Load the agent owner's LLM config from the database and set it on
    the current asyncio task's ContextVar.

    Call this at the top of every MCP tool handler that makes embedding
    or LLM calls. It mirrors what AgentRuntime.run() does in step 0,
    ensuring per-user API keys are used even when the tool is invoked
    from a separate MCP process rather than inside an agent turn.

    Raises:
        LLMConfigNotConfigured: if the owner has not configured their
            LLM providers. The caller should surface this as a tool error.
    """
    claude, openai_cfg, embedding = await get_agent_owner_llm_configs(agent_id)
    set_user_config(claude, openai_cfg, embedding)
