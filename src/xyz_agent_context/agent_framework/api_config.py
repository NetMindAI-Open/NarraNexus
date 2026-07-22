"""
@file_name: api_config.py
@author: Bin Liang
@date: 2026-03-23
@description: Centralized LLM API configuration for all agent framework components

All API keys, base URLs, and model names used by the agent framework are defined
here. Components (Claude SDK, OpenAI Agents SDK, Gemini SDK) should read from
this module instead of accessing settings/os.environ directly.

Configuration priority:
    1. ~/.nexusagent/llm_config.json (managed by provider_registry)
    2. .env / settings.py (legacy fallback for existing users)

Usage:
    from xyz_agent_context.agent_framework.api_config import (
        claude_config,
        openai_config,
        gemini_config,
    )

    # Access config values
    model = openai_config.model
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
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
    """Claude API configuration (passed to Claude Code CLI subprocess)."""
    api_key: str = ""
    base_url: str = ""
    model: str = ""          # Empty = let Claude Code CLI use its default model
    auth_type: str = "api_key"  # "api_key" | "bearer_token" | "oauth"
    # Whether the provider endpoint runs Anthropic's server-side tools
    # (web_search_20250305, text_editor, computer_use, ...). Only the
    # official Anthropic API and transparent forward proxies do; most
    # aggregators (NetMind, OpenRouter, Yunwu, ...) do not. The tool
    # policy hook reads this to decide whether to permit WebSearch.
    supports_anthropic_server_tools: bool = False
    # Framework-neutral reasoning params from the agent slot
    # (SlotConfig.thinking / SlotConfig.reasoning_effort). "" = auto =
    # the adapter passes nothing and the CLI keeps its defaults. The
    # Claude-dialect mapping lives in xyz_claude_agent_sdk
    # (_resolve_reasoning_options), not here.
    thinking: str = ""
    reasoning_effort: str = ""

    def to_cli_env(self) -> dict[str, str]:
        """Build env vars dict for the Claude Code CLI subprocess.

        Returns a **complete** dict for every key we care about — including
        explicit blank strings where we want to suppress an inherited value
        from the parent process's ``os.environ``. This is critical for
        multi-tenant concurrency: the SDK merges ``{**os.environ, **options.env}``
        at subprocess spawn, so any key we omit is inherited. Leaving model
        overrides (for example) unset could leak tenant A's model into
        tenant B's agent run when both are active on the same host.

        Each invocation of this method is associated with a ``ClaudeConfig``
        captured from the current asyncio task's ContextVar, so there is no
        cross-task mutation of shared state.
        """
        env: dict[str, str] = {
            # Auth — exactly one of these should be populated; we blank the
            # other so a stray env var from the parent process can't leak in.
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_AUTH_TOKEN": "",
            "ANTHROPIC_BASE_URL": self.base_url or "",
            # Nested-session guard suppression. When the backend itself was
            # launched from inside a Claude Code session (dev workflow:
            # `bash run.sh` typed into a Claude Code terminal), the inherited
            # CLAUDECODE var makes every spawned `claude` CLI refuse to start
            # ("cannot be launched inside another Claude Code session", exit 1)
            # — killing both the agent loop and the CLI helper. We are a
            # platform spawning claude as a managed subprocess, not a human
            # nesting sessions; blank it so the child env is deterministic.
            "CLAUDECODE": "",
        }
        if self.api_key:
            if self.auth_type == "bearer_token":
                env["ANTHROPIC_AUTH_TOKEN"] = self.api_key
            else:
                env["ANTHROPIC_API_KEY"] = self.api_key

        # #7 resilience: bound a stalled request and turn on the CLI's built-in
        # retry, both from settings (.env-tunable). API_TIMEOUT_MS is a
        # per-REQUEST cap (not a run total → does not violate 铁律 #14's
        # no-agent_loop-cap); a stalled/dead request errors after it and the CLI
        # auto-retries CLAUDE_CODE_MAX_RETRIES times (transient 429/5xx/conn) on
        # the SAME provider (does not govern the user's model → 铁律 #15).
        from xyz_agent_context.settings import settings as _settings
        env["API_TIMEOUT_MS"] = str(_settings.llm_api_timeout_ms)
        env["CLAUDE_CODE_MAX_RETRIES"] = str(_settings.llm_max_retries)

        # Isolate the subprocess from the host user's personal
        # ``~/.claude/settings.json``. Claude Code applies that file's ``env``
        # block ABOVE the subprocess env we set here (it even survives
        # ``--setting-sources ""``), so a developer who runs their own Claude
        # Code with a custom ``ANTHROPIC_BASE_URL``/``ANTHROPIC_AUTH_TOKEN``
        # would have every agent_loop silently redirected to their personal
        # endpoint — the netmind config we inject loses the precedence fight.
        #  → 2026-07-08 incident: personal relay in the env block returned
        #    ``503 No available accounts`` for every frontend message.
        # Both auth kinds get a dedicated NarraNexus config dir (the CLI
        # auto-creates it) so the personal settings.json is never read:
        #   * keyed (api_key/bearer) → ``claude_cli_config_path``; the key is
        #     injected via env above, no credential file needed.
        #   * oauth → ``claude_oauth_config_path``; a SEPARATE dir into which
        #     ``_stage_claude_oauth_credentials`` (in xyz_claude_agent_sdk)
        #     copies ONLY ``.credentials.json`` before the spawn. OAuth used to
        #     point straight at ``~/.claude`` here, which re-exposed the exact
        #     hijack above AND raced the user's own Claude Code on
        #     ``~/.claude/.claude.json`` (2026-07-09 incident).
        # Always set the key (never omit) so a stray inherited
        # ``CLAUDE_CONFIG_DIR`` can't leak in via the SDK's
        # ``{**os.environ, **options.env}`` merge.
        if self.auth_type == "oauth":
            env["CLAUDE_CONFIG_DIR"] = _settings.claude_oauth_config_path
        else:
            env["CLAUDE_CONFIG_DIR"] = _settings.claude_cli_config_path

        # Redirect Claude Code's *internal* LLM calls (WebFetch summarizer,
        # subagent task dispatch, alias-to-model resolution) to the same
        # provider as the main loop. Without these, those calls fall back
        # to official Anthropic model names, hit the provider's endpoint
        # with an unknown model, and either fail or drift off-provider.
        # Docs: https://code.claude.com/docs/en/model-config
        if self.model:
            # CLI family aliases ("opus") are invalid on raw API transports —
            # normalize here so the CLI's internal calls can't 400 either
            # (same rule as the main-loop model in xyz_claude_agent_sdk).
            from xyz_agent_context.agent_framework.model_catalog import (
                is_cli_family_alias,
                resolve_cli_alias,
            )

            model = resolve_cli_alias(self.model, auth_type=self.auth_type)
            if is_cli_family_alias(model):
                # OAuth keeps family aliases verbatim ("opus") — but the
                # ANTHROPIC_DEFAULT_*_MODEL redirects may only carry CONCRETE
                # ids: pointing an alias at itself makes the CLI reject the
                # model outright ("There's an issue with the selected model",
                # exit 1 — killed every claude_oauth agent turn AND the CLI
                # helper). Blank the redirects (official backend needs no
                # anti-drift pinning; the CLI resolves aliases itself) and
                # keep only the subagent pin, which accepts aliases.
                env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = ""
                env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = ""
                env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = ""
                env["CLAUDE_CODE_SUBAGENT_MODEL"] = model
            else:
                env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = model
                env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = model
                env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = model
                env["CLAUDE_CODE_SUBAGENT_MODEL"] = model
        else:
            # No explicit model → blank these so a stale inherited value
            # from os.environ can't steer CLI behavior for this run.
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = ""
            env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = ""
            env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = ""
            env["CLAUDE_CODE_SUBAGENT_MODEL"] = ""

        return env


@dataclass(frozen=True)
class OpenAIConfig:
    """OpenAI Chat Completions API configuration (used by helper_llm slot)"""
    api_key: str = ""
    base_url: str = ""  # Empty = default https://api.openai.com/v1
    # Fallback when the helper_llm slot is set to "default" on the
    # official OpenAI endpoint AND a call site did not pass an
    # explicit model. gpt-5.4-mini won out over 5.1 in our 2026-05-12
    # benchmark: 0.85s/call vs 28s in EC2 on gpt-4o-mini, and the
    # reasoning_effort=minimal path keeps narrative judge calls under
    # a second. Per-call-site overrides still take precedence — see
    # OpenAIAgentsSDK._resolve_model.
    model: str = "gpt-5.4-mini-2026-03-17"


@dataclass(frozen=True)
class AnthropicHelperConfig:
    """Anthropic-protocol helper_llm configuration.

    Used when the helper_llm slot points at an anthropic-protocol
    provider — the single-Claude-key onboarding path. Consumed by
    AnthropicHelperSDK (Messages API), never by the agent loop.
    Parallels :class:`OpenAIConfig` for the helper role; carried
    separately because the wire protocol (Messages vs Chat
    Completions) and auth header conventions differ.
    """
    api_key: str = ""
    base_url: str = ""           # Empty = official https://api.anthropic.com
    model: str = "claude-haiku-4-5"
    auth_type: str = "api_key"   # "api_key" | "bearer_token"


@dataclass(frozen=True)
class CodexConfig:
    """OpenAI Codex CLI configuration (passed to ``codex exec`` subprocess).

    Parallels :class:`ClaudeConfig` for the Codex coding-agent path.
    Carried separately because Codex auth, env var names, and config
    surface (TOML file vs argv) differ from Claude Code despite both
    being subprocess-spawned coding agents.

    Auth model:
      - ``api_key`` empty (default) → ``to_cli_env`` blanks
        ``CODEX_API_KEY`` so the subprocess falls back to
        ``$CODEX_HOME/auth.json`` (the ``codex login`` OAuth file).
      - ``api_key`` non-empty + ``auth_type='api_key'`` → injected as
        ``CODEX_API_KEY`` env var.

    ``base_url`` + ``model`` flow into Codex's ``config.toml``
    ``[model_providers.<name>]`` table at run time, NOT env vars —
    Codex reads the endpoint from the toml file. See
    :func:`_codex_config_toml_builder.build_codex_config_toml`.
    """

    api_key: str = ""
    base_url: str = ""  # Empty = use Codex's bundled OpenAI provider
    model: str = ""     # Empty = let Codex CLI pick its default
    auth_type: str = "oauth"  # "oauth" | "api_key"
    auth_ref: str = ""  # e.g. codex-cli:~/.codex/auth.json for OAuth
    # Framework-neutral reasoning params from the agent slot — mirror of
    # ClaudeConfig's. The Codex-dialect mapping (model_reasoning_effort
    # in config.toml, with clamping) lives in _codex_config_toml_builder;
    # ``thinking`` has no Codex equivalent and is ignored there.
    thinking: str = ""
    reasoning_effort: str = ""

    def to_cli_env(self) -> dict[str, str]:
        """Build env vars dict for the ``codex exec`` subprocess.

        Mirrors :meth:`ClaudeConfig.to_cli_env` invariants:
          1. Explicit blank for the auth env var when not in use, so
             a stray ``CODEX_API_KEY`` from the parent process's
             ``os.environ`` cannot leak across tenants in a
             multi-tenant deployment.
          2. We do NOT set ``OPENAI_API_KEY`` — that's a different
             env var for the OpenAI Python SDK, not for Codex CLI.
             If the user has ``OPENAI_API_KEY`` exported globally,
             Codex CLI may still pick it up; we don't try to fight
             that, we only own our scoped env.
        """
        env: dict[str, str] = {"CODEX_API_KEY": ""}
        if self.api_key and self.auth_type == "api_key":
            env["CODEX_API_KEY"] = self.api_key
        return env


@dataclass(frozen=True)
class CliHelperConfig:
    """CLI-backed helper_llm configuration (subscription / OAuth helper).

    Used when the helper_llm slot points at a subscription provider —
    Claude Code (``claude_oauth``) or Codex (``codex_oauth``). Those OAuth
    credentials cannot make direct Messages / Chat-Completions API calls, so
    the helper's small structured-output calls run through the SAME CLI the
    subscription already authorizes (one-shot, no separate API key). This is
    what lets a single subscription login cover BOTH the agent slot and the
    helper_llm slot. Consumed by :class:`CliHelperSDK`, never by the agent
    loop.

    ``framework`` selects the CLI backend: "claude_code" (via
    ``claude_agent_sdk.query()``) or "codex_cli" (via ``codex exec``).
    ``model`` is the model to request. ``auth_type`` / ``api_key`` /
    ``base_url`` mirror the agent config so the SDK can reuse the exact
    ``ClaudeConfig``/``CodexConfig`` ``to_cli_env`` credential wiring — for
    OAuth the key is blank and the CLI reads its own credential file.
    """
    framework: str = "claude_code"  # "claude_code" | "codex_cli"
    model: str = ""
    base_url: str = ""
    auth_type: str = "oauth"  # "oauth" | "api_key"
    api_key: str = ""


@dataclass(frozen=True)
class GeminiConfig:
    """Google Gemini API configuration"""
    api_key: str = ""
    model: str = "gemini-2.5-flash"


@dataclass(frozen=True)
class RuntimeLLMConfigs:
    """All LLM configs needed for one agent turn."""

    claude: ClaudeConfig
    openai: OpenAIConfig
    codex: CodexConfig = field(default_factory=CodexConfig)
    # Set when the helper_llm slot points at an anthropic-protocol
    # provider (single-Claude-key path); None means the helper runs
    # on the OpenAI protocol via ``openai`` above.
    anthropic_helper: Optional[AnthropicHelperConfig] = None
    # Set when the helper_llm slot points at a subscription (OAuth)
    # provider — the helper runs through the same CLI as the agent. Takes
    # precedence over ``anthropic_helper`` / ``openai`` in get_helper_sdk().
    cli_helper: Optional[CliHelperConfig] = None


# =============================================================================
# Config Loading
# =============================================================================

def _load_from_llm_config() -> Optional[tuple[ClaudeConfig, OpenAIConfig]]:
    """
    Try to load configuration from ~/.nexusagent/llm_config.json.

    Returns:
        Tuple of (claude_config, openai_config) if successful,
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
            supports_anthropic_server_tools=bool(
                getattr(agent_provider, "supports_anthropic_server_tools", False)
            ),
            thinking=agent_slot.thinking,
            reasoning_effort=agent_slot.reasoning_effort,
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

    logger.info("LLM config loaded from llm_config.json")
    return claude, openai_cfg


def _load_from_settings() -> tuple[ClaudeConfig, OpenAIConfig]:
    """
    Fallback: load configuration from .env / settings.py (legacy path).
    """
    from xyz_agent_context.settings import settings

    # Heuristic for the .env fallback path: server tools are supported iff
    # the base URL is empty (defaults to official Anthropic) or explicitly
    # points at api.anthropic.com. Any third-party host is assumed unable
    # to serve web_search_20250305 / text_editor / etc.
    _base = (settings.anthropic_base_url or "").lower()
    _is_official = not _base or "api.anthropic.com" in _base
    claude = ClaudeConfig(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url,
        model=settings.anthropic_model,
        supports_anthropic_server_tools=_is_official,
    )

    openai_cfg = OpenAIConfig(
        api_key=settings.openai_api_key,
    )

    return claude, openai_cfg


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
        self._gemini: Optional[GeminiConfig] = None
        # Codex defaults to an empty config — user-scoped overrides
        # arrive via the ``_codex_ctx`` ContextVar at agent_loop time.
        # No .env / llm_config.json source for now (Codex auth flows
        # through ``codex login`` rather than NarraNexus config).
        self._codex: Optional[CodexConfig] = None
        # Anthropic helper has no global source either — it is only
        # ever set per-task by the resolver. The holder keeps a benign
        # empty default so the proxy's fallback path never raises.
        self._anthropic_helper: Optional[AnthropicHelperConfig] = None
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self.reload()

    def reload(self) -> None:
        """Reload config from llm_config.json + .env fallback."""
        json_result = _load_from_llm_config()
        env_claude, env_openai = _load_from_settings()

        if json_result is not None:
            json_claude, json_openai = json_result
            self._claude = json_claude if (json_claude.api_key or json_claude.auth_type == "oauth") else env_claude
            self._openai = json_openai if json_openai.api_key else env_openai
        else:
            self._claude, self._openai = env_claude, env_openai

        self._gemini = _load_gemini_config()
        # Codex defaults to an empty config — per-user resolver
        # populates the ContextVar when a Codex agent run begins.
        self._codex = CodexConfig()
        self._anthropic_helper = AnthropicHelperConfig()
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
            f"key={_mask(self._openai.api_key)}"
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
    def gemini(self) -> GeminiConfig:
        self._ensure_loaded()
        return self._gemini  # type: ignore

    @property
    def codex(self) -> CodexConfig:
        self._ensure_loaded()
        return self._codex  # type: ignore

    @property
    def anthropic_helper(self) -> AnthropicHelperConfig:
        self._ensure_loaded()
        return self._anthropic_helper  # type: ignore

    @property
    def cli_helper(self) -> "CliHelperConfig":
        # No global/desktop source for a CLI helper — it is only ever derived
        # from an OAuth provider onto the per-task ContextVar. This default
        # (never a real config) exists so the ``cli_helper_config`` proxy's
        # fall-through (ContextVar is None) returns a benign object instead of
        # raising AttributeError if something reads it off the helper path.
        return CliHelperConfig()


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
_codex_ctx: ContextVar[Optional[CodexConfig]] = ContextVar("codex_config", default=None)
_anthropic_helper_ctx: ContextVar[Optional[AnthropicHelperConfig]] = ContextVar(
    "anthropic_helper_config", default=None
)
_cli_helper_ctx: ContextVar[Optional[CliHelperConfig]] = ContextVar(
    "cli_helper_config", default=None
)


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
gemini_config: GeminiConfig = _ConfigProxy("gemini")  # type: ignore
codex_config: CodexConfig = _ConfigProxy("codex", _codex_ctx)  # type: ignore
anthropic_helper_config: AnthropicHelperConfig = _ConfigProxy(
    "anthropic_helper", _anthropic_helper_ctx
)  # type: ignore
cli_helper_config: CliHelperConfig = _ConfigProxy(
    "cli_helper", _cli_helper_ctx
)  # type: ignore


def reload_llm_config() -> None:
    """Reload LLM config from disk. Call after llm_config.json changes."""
    _holder.reload()


def set_user_config(
    claude: ClaudeConfig,
    openai: OpenAIConfig,
    codex: CodexConfig | None = None,
    anthropic_helper: AnthropicHelperConfig | None = None,
    cli_helper: CliHelperConfig | None = None,
) -> None:
    """
    Set per-user LLM config for the CURRENT asyncio task only.

    This uses ContextVar so concurrent tasks from different users cannot
    see each other's config. Call this at the start of an agent turn
    after loading the owner's config from the database.

    ``anthropic_helper`` is None unless the helper_llm slot resolved to
    an anthropic-protocol provider; ``get_helper_sdk`` keys off this
    ContextVar to pick the helper implementation for the task.

    The setting automatically goes out of scope when the task finishes.
    """
    _claude_ctx.set(claude)
    _openai_ctx.set(openai)
    _codex_ctx.set(codex or CodexConfig())
    _anthropic_helper_ctx.set(anthropic_helper)
    _cli_helper_ctx.set(cli_helper)


def snapshot_user_config() -> dict[str, Optional[object]]:
    """Return the CURRENT task's provider configs (the ContextVar values).

    Used by the remote agent-loop executor seam: the orchestrator (which
    has the DB + resolver) snapshots the resolved configs here and ships
    them to the executor service, which re-applies them via
    ``set_user_config`` before running the loop. Returns the raw config
    objects (or None when unset); serialization is the caller's job.
    """
    return {
        "claude": _claude_ctx.get(),
        "openai": _openai_ctx.get(),
        "codex": _codex_ctx.get(),
        "anthropic_helper": _anthropic_helper_ctx.get(),
        "cli_helper": _cli_helper_ctx.get(),
    }


# =============================================================================
# Quota-routing ContextVars (system-default free-tier feature)
# =============================================================================
#
# Two auxiliary ContextVars set by auth_middleware and read by cost_tracker:
#
# - provider_source: "user" | "system" | None
#     Tagged by ProviderResolver to indicate which branch produced the
#     active user_config. cost_tracker reads this to decide whether to
#     deduct the system-default quota after an LLM call.
#
# - current_user_id:
#     Tagged by auth_middleware once the JWT is parsed. cost_tracker uses
#     it to attribute token usage without having to thread user_id through
#     every layer of the LLM call stack.
#
# Both default to None so existing code paths (and local mode) are
# unaffected — cost_tracker's quota hook is a no-op when either is unset.

_provider_source_ctx: ContextVar[Optional[str]] = ContextVar(
    "provider_source", default=None
)
_current_user_id_ctx: ContextVar[Optional[str]] = ContextVar(
    "current_user_id", default=None
)



def clear_user_config() -> None:
    """Reset this task's per-user config ContextVars to the global fallback.

    Sequential multi-tenant loops (memory consolidation worker) MUST call
    this before resolving each scope: without it, a scope whose resolution
    is skipped (deleted agent, missing owner row) silently inherits the
    PREVIOUS tenant's credentials still sitting in the task's ContextVars.

    ALL FOUR config ContextVars are reset. Resetting only claude/openai
    (the historical behavior) left ``_codex_ctx`` and ``_anthropic_helper_ctx``
    carrying the previous tenant's credentials — a cross-tenant leak that the
    helper-SDK factory keys off (a stale ``_anthropic_helper_ctx`` would route
    tenant B's helper to tenant A's Claude key).
    """
    _claude_ctx.set(None)
    _openai_ctx.set(None)
    _codex_ctx.set(CodexConfig())
    _anthropic_helper_ctx.set(None)
    _cli_helper_ctx.set(None)

def set_provider_source(src: Optional[str]) -> None:
    _provider_source_ctx.set(src)


def get_provider_source() -> Optional[str]:
    return _provider_source_ctx.get()


def set_current_user_id(uid: Optional[str]) -> None:
    _current_user_id_ctx.set(uid)


def get_current_user_id() -> Optional[str]:
    return _current_user_id_ctx.get()


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
# the helper-LLM clients, etc.). Blast radius is ~20 files, mostly
# mechanical changes to function signatures.
#
# Blocked by: none — just time.
# Priority: medium (current design is safe thanks to fail-fast in
# get_user_llm_configs, so this is cleanup not a bug fix).


class LLMResolverError(RuntimeError):
    """Base class for failures when resolving LLM provider config for a user.

    Two concrete subclasses — callers can handle both together via
    ``except LLMResolverError`` when they want "any resolution failure",
    or differentiate via ``except LLMConfigNotConfigured``/
    ``except SystemDefaultUnavailable`` when the UX differs.
    """


class LLMConfigNotConfigured(LLMResolverError):
    """Raised when a user has no free-tier grant (no quota row) and their
    own provider/slot configuration is missing or broken.

    No silent fallback to the system free tier here — the quota row IS
    the grant (implicit-grant liability guard; the old opt-out preference
    was removed 2026-07-18). The error message tells them exactly what to
    fix (add provider, assign slot).
    """


class SystemDefaultUnavailable(LLMResolverError):
    """Raised when a user has opted in to the system-default free tier
    but it can't serve the request — either the operator has disabled
    it (``SYSTEM_DEFAULT_LLM_ENABLED!=true``) or the user's quota is
    exhausted.

    No silent fallback to the user's own provider here either — the
    user's opt-in is a deliberate preference and we don't override it.
    The error message directs them to either turn the toggle off and
    configure their own provider, or to ask the operator for more quota.
    """


async def get_agent_owner_llm_configs(
    agent_id: str,
) -> tuple[ClaudeConfig, OpenAIConfig]:
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
    # Bill to the owner, but resolve with THIS agent's per-agent overrides
    # overlaid on the owner's user-level defaults.
    return await get_user_llm_configs(owner_user_id, agent_id=agent_id)


async def get_agent_owner_runtime_llm_configs(
    agent_id: str,
) -> RuntimeLLMConfigs:
    """Load every LLM config needed for an agent turn based on owner.

    Billed to the owner (``agents.created_by``); the agent slot + helper slot
    are resolved with this agent's per-agent overrides (``agent_slots``)
    overlaid on the owner's user-level defaults, falling back to the defaults
    for any slot the agent hasn't overridden.
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
    return await get_user_runtime_llm_configs(owner_user_id, agent_id=agent_id)


async def get_user_llm_configs(
    user_id: str, agent_id: str | None = None
) -> tuple[ClaudeConfig, OpenAIConfig]:
    """
    Resolve the (claude, openai) config pair for a specific user.

    Thin wrapper over :func:`get_user_runtime_llm_configs`, which routes the
    decision through the single ``ProviderResolver`` tree. Decision summary:

      1. free tier granted + budget → system free tier (tags
         ``provider_source="system"`` so cost_tracker deducts quota).
         Free-tier-first is platform behavior — the old prefer_system
         toggle was removed 2026-07-18.
      2. free tier exhausted + complete own provider → the user's own key
         takes over immediately (#48); a one-time notice is surfaced
         (deduped via the repurposed prefer_system_override latch).
      3. free tier exhausted + no own provider →
         ``SystemDefaultUnavailable`` (add a provider / ask for more quota).
      4. no quota row (no free tier granted) → strictly the user's own
         providers; if misconfigured → ``LLMConfigNotConfigured``.

    QuotaService is lazily bootstrapped via ``_ensure_quota_service``, so every
    entry point (backend.main, job_trigger, bus_trigger, run_lark_trigger,
    standalone MCP runner) works without calling ``bootstrap_quota_subsystem``.

    Raises:
        SystemDefaultUnavailable: free tier gone, no own provider.
        LLMConfigNotConfigured: no free tier, own config missing/incomplete.
    """
    cfg = await get_user_runtime_llm_configs(user_id, agent_id=agent_id)
    return cfg.claude, cfg.openai


async def get_user_runtime_llm_configs(
    user_id: str, agent_id: str | None = None
) -> RuntimeLLMConfigs:
    """Resolve all runtime LLM configs (agent + helper + codex) for a user.

    ``agent_id`` (optional) overlays that agent's per-agent slot overrides
    (``agent_slots``) on the owner's user-level defaults — used by the
    agent-run + MCP-tool paths so each agent can pin its own framework/model
    (agent slot) and helper model. The cloud SYSTEM free-tier branch ignores
    it (fixed one-model pool).

    Delegates to the ONE provider decision tree — ``ProviderResolver`` — the
    same classifier the HTTP quota gate and background workers use, so every
    run path shares a single source of truth. In particular the agent-run path
    now inherits the #48 auto-switch: an opted-in user whose free tier is
    exhausted but who has a complete own provider is flipped to their own key
    here too. Previously this path kept a divergent strict copy of the tree
    that 402'd on exhaustion, ignoring the configured key on any run that did
    not first pass through the HTTP middleware (background job/bus triggers).

    ``resolve()`` returns ``(configs, source)``; we tag ``provider_source`` /
    ``current_user_id`` so ``cost_tracker`` deducts the free-tier quota only on
    the system branch. ``None`` means the free tier is disabled (local/desktop
    mode) — fall back to the strict own-config resolution unchanged.

    Raises (ProviderResolverError translated into the LLMResolverError family
    the agent runtime + job/lark triggers already handle):
        SystemDefaultUnavailable: opted in, free tier gone, no own provider.
        LLMConfigNotConfigured: opted out, own provider missing/incomplete.
    """
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.agent_framework.provider_resolver import (
        NoProviderConfiguredError,
        ProviderResolver,
        ProviderResolverError,
    )
    from xyz_agent_context.agent_framework.system_provider_service import (
        SystemProviderService,
    )
    from xyz_agent_context.agent_framework.user_provider_service import (
        UserProviderService,
    )

    db = await get_db_client()
    resolver = ProviderResolver(
        user_provider_svc=UserProviderService(db),
        system_provider_svc=SystemProviderService.instance(),
        quota_svc=await _ensure_quota_service(),
    )
    try:
        resolved = await resolver.resolve(user_id, agent_id=agent_id)
    except NoProviderConfiguredError as e:
        # No free-tier grant + own config missing/broken — same UX the
        # strict own-config path raised before.
        raise LLMConfigNotConfigured(str(e)) from e
    except ProviderResolverError as e:
        # Free tier gone, no own provider (QuotaExceededError), plus
        # any other gate. Keep the SystemDefaultUnavailable *type* so triggers
        # that string-match the class name (job_trigger, lark_trigger) are
        # unaffected by the convergence.
        raise SystemDefaultUnavailable(str(e)) from e

    if resolved is None:
        # SYSTEM_DISABLED — local/desktop mode, free tier not in play.
        return await _get_user_runtime_llm_configs_strict(user_id, agent_id=agent_id)

    cfgs, source = resolved
    set_provider_source(source)
    set_current_user_id(user_id)
    return cfgs


async def _ensure_quota_service():
    """Return ``QuotaService.default()``, bootstrapping it on first use.

    Every process that calls ``AgentRuntime.run()`` needs a live
    QuotaService to resolve the free-tier branch. Instead of requiring
    each entry point to call ``bootstrap_quota_subsystem`` at startup
    (one was missed: ``run_lark_trigger``), we make the first access
    self-bootstrap using the shared ``get_db_client()`` factory. The
    operation is idempotent.
    """
    from xyz_agent_context.agent_framework.quota_service import (
        QuotaService,
        bootstrap_quota_subsystem,
    )
    try:
        return QuotaService.default()
    except RuntimeError:
        from xyz_agent_context.utils.db_factory import get_db_client
        db = await get_db_client()
        return await bootstrap_quota_subsystem(db)




async def _get_user_llm_configs_strict(
    user_id: str, agent_id: str | None = None
) -> tuple[ClaudeConfig, OpenAIConfig]:
    """Strict version: raises LLMConfigNotConfigured on any missing
    slot / broken provider. The public `get_user_llm_configs` wraps
    this with a system-default fallback.
    """
    cfg = await _get_user_runtime_llm_configs_strict(user_id, agent_id=agent_id)
    return cfg.claude, cfg.openai


async def _get_user_runtime_llm_configs_strict(
    user_id: str, agent_id: str | None = None
) -> RuntimeLLMConfigs:
    """Resolve agent + helper (+ codex) configs via the single-point
    Provider Driver resolver. There is deliberately NO second
    hand-rolled fallback path: a young project keeps one resolver and
    lets any unexpected error surface (iron rule #2 / #5) rather than
    silently re-deriving configs through a stale parallel copy — the old
    fallback predated Codex and would mis-wire a codex agent into a
    ClaudeConfig.

    ``agent_id`` (optional) overlays that agent's per-agent slot overrides.
    """
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.agent_framework.provider_driver import (
        resolve_user_runtime_llm_configs,
    )

    db = await get_db_client()
    return await resolve_user_runtime_llm_configs(user_id, db, agent_id=agent_id)


async def setup_mcp_llm_context(agent_id: str) -> None:
    """
    Load the agent owner's LLM config from the database and set it on
    the current asyncio task's ContextVar.

    Call this at the top of every MCP tool handler that makes LLM calls.
    It mirrors what AgentRuntime.run() does in step 0, ensuring per-user
    API keys are used even when the tool is invoked from a separate MCP
    process rather than inside an agent turn.

    Raises:
        LLMConfigNotConfigured: if the owner has not configured their
            LLM providers. The caller should surface this as a tool error.
    """
    cfg = await get_agent_owner_runtime_llm_configs(agent_id)
    set_user_config(cfg.claude, cfg.openai, cfg.codex, cfg.anthropic_helper, cfg.cli_helper)
