"""
@file_name: provider_resolver.py
@author: Bin Liang
@date: 2026-04-16
@description: Per-request routing between a user's own LLM config and the
system-default NetMind config, with quota gating on the system branch.

Wired into backend.auth.auth_middleware. Decision tree (aligned with
business-layer `api_config.get_user_llm_configs` so the two entry points
cannot disagree):

  0. SystemProviderService.is_enabled() == False
     -> strict no-op; local mode / disabled env leaves every ContextVar
        untouched. Agent code paths continue to use the existing
        llm_config.json global fallback.

  1. quota row exists AND prefer_system_override=True (the default for
     newly registered users — they start on the free tier):
     1a. quota has budget  -> route "system" (cost_tracker deducts post-call)
     1b. no budget + has complete own config -> FreeTierExhaustedError
         (user can uncheck the Settings toggle to switch to their own key)
     1c. no budget + no own provider         -> QuotaExceededError
         (user must add a provider before the app becomes usable again)

  2. prefer_system_override=False, OR no quota row at all (implicit opt-out):
     2a. has complete own config -> route "user" (quota NOT consulted)
     2b. own config missing / incomplete -> NoProviderConfiguredError
         (no silent fallback to the free tier: opt-out must be honoured)

All three exceptions carry a stable `error_code` class attribute that
auth_middleware returns verbatim to the client; the frontend
pattern-matches on this string to decide which remediation UI to show.
"""
from __future__ import annotations

from typing import Optional

from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    EmbeddingConfig,
    OpenAIConfig,
    set_provider_source,
    set_user_config,
)
from xyz_agent_context.agent_framework.quota_service import QuotaService
from xyz_agent_context.agent_framework.system_provider_service import (
    SystemProviderService,
)
from xyz_agent_context.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
)


_REQUIRED_SLOTS = ("agent", "embedding", "helper_llm")


class ProviderResolverError(Exception):
    """Base for resolver-side LLM routing errors. auth_middleware catches
    this base class once, reads `error_code` + message, returns HTTP 402."""

    error_code: str = "PROVIDER_RESOLVER_ERROR"

    def __init__(self, user_id: str, message: str | None = None):
        super().__init__(message or f"{self.error_code} for {user_id}")
        self.user_id = user_id


class QuotaExceededError(ProviderResolverError):
    """Opted in to the free tier but the budget is gone AND the user has no
    own provider configured — they must add one to continue."""

    error_code = "QUOTA_EXCEEDED_NO_USER_PROVIDER"

    def __init__(self, user_id: str):
        super().__init__(
            user_id,
            "Free quota exhausted. Configure your own provider to continue.",
        )


class FreeTierExhaustedError(ProviderResolverError):
    """Opted in to the free tier but the budget is gone; the user HAS a
    complete own provider they could switch to by unchecking the 'Use
    free quota' toggle in Settings."""

    error_code = "FREE_TIER_EXHAUSTED_DISABLE_TOGGLE"

    def __init__(self, user_id: str):
        super().__init__(
            user_id,
            "Free quota exhausted. Disable 'Use free quota' in Settings "
            "to switch to your own provider.",
        )


class NoProviderConfiguredError(ProviderResolverError):
    """Opted out of the free tier but own provider is missing or incomplete.
    No silent fallback to the free tier — the user's opt-out must stand."""

    error_code = "NO_PROVIDER_CONFIGURED"

    def __init__(self, user_id: str):
        super().__init__(
            user_id,
            "No provider configured. Add a provider in Settings, or enable "
            "'Use free quota' to use the free tier.",
        )


class ProviderResolver:
    """Arbitrates which LLMConfig feeds the current request's ContextVar."""

    def __init__(
        self,
        user_provider_svc,  # UserProviderService (duck-typed)
        system_provider_svc: SystemProviderService,
        quota_svc: QuotaService,
    ):
        self.user_provider_svc = user_provider_svc
        self.system_provider_svc = system_provider_svc
        self.quota_svc = quota_svc

    async def resolve(
        self, user_id: str
    ) -> Optional[tuple[ClaudeConfig, OpenAIConfig, EmbeddingConfig, str]]:
        """Resolve a user's effective LLM configs WITHOUT mutating ContextVars.

        Returns ``(claude, openai, embedding, source)`` where ``source`` is
        ``"system"`` or ``"user"``, or ``None`` when the system-default
        feature is disabled (local mode / env not set) — in that case the
        caller keeps whatever global/desktop config is already in effect.

        Raises the same gating errors as :meth:`resolve_and_set`
        (``FreeTierExhaustedError`` / ``QuotaExceededError`` /
        ``NoProviderConfiguredError``). Background jobs that need a provider
        outside the request path (e.g. the embedding rebuild) call this and
        build their own client from the returned config, instead of relying
        on a ContextVar that only exists inside the HTTP request task.
        """
        # Branch 0: feature disabled (local mode or env not set).
        if not self.system_provider_svc.is_enabled():
            return None

        quota = await self.quota_svc.get(user_id)
        prefer_system = quota is not None and quota.prefer_system_override

        user_cfg = await self.user_provider_svc.get_user_config(user_id)
        has_own = _is_user_config_complete(user_cfg)

        if prefer_system:
            # Branch 1: user opted in to free tier.
            if await self.quota_svc.check(user_id):
                claude, openai, embedding = _llm_config_to_dataclasses(
                    self.system_provider_svc.get_config()
                )
                return claude, openai, embedding, "system"
            if has_own:
                raise FreeTierExhaustedError(user_id)
            raise QuotaExceededError(user_id)

        # Branch 2: opted out (or no quota row).
        if has_own:
            claude, openai, embedding = _llm_config_to_dataclasses(user_cfg)
            return claude, openai, embedding, "user"
        raise NoProviderConfiguredError(user_id)

    async def resolve_and_set(self, user_id: str) -> None:
        """Resolve the user's configs and push them onto the request ContextVars.

        Thin wrapper over :meth:`resolve` — the HTTP request path uses this so
        downstream LLM/embedding clients pick up the right provider implicitly.
        """
        resolved = await self.resolve(user_id)
        if resolved is None:
            return
        claude, openai, embedding, source = resolved
        set_user_config(claude, openai, embedding)
        set_provider_source(source)


def _is_user_config_complete(cfg: LLMConfig | None) -> bool:
    """All three slots present, each with a non-empty model, each pointing
    to an active provider that exists in `cfg.providers`.
    """
    if cfg is None:
        return False
    providers = getattr(cfg, "providers", None)
    slots = getattr(cfg, "slots", None)
    if not providers or not slots:
        return False
    for slot_name in _REQUIRED_SLOTS:
        slot = slots.get(slot_name)
        if slot is None or not slot.provider_id or not slot.model:
            return False
        prov = providers.get(slot.provider_id)
        if prov is None or not prov.is_active:
            return False
    return True


def _llm_config_to_dataclasses(
    cfg: LLMConfig,
) -> tuple[ClaudeConfig, OpenAIConfig, EmbeddingConfig]:
    """Convert an LLMConfig (slot-addressed) into the three dataclasses
    set_user_config expects. Assumes the caller already verified completeness
    via `_is_user_config_complete` (or that the system config is valid).
    """
    agent_slot = cfg.slots["agent"]
    agent_prov: ProviderConfig = cfg.providers[agent_slot.provider_id]
    claude = ClaudeConfig(
        api_key=agent_prov.api_key,
        base_url=agent_prov.base_url,
        model=agent_slot.model,
        auth_type=(
            agent_prov.auth_type.value
            if isinstance(agent_prov.auth_type, AuthType)
            else agent_prov.auth_type
        ),
        supports_anthropic_server_tools=bool(
            getattr(agent_prov, "supports_anthropic_server_tools", False)
        ),
    )

    helper_slot = cfg.slots["helper_llm"]
    helper_prov = cfg.providers[helper_slot.provider_id]
    openai = OpenAIConfig(
        api_key=helper_prov.api_key,
        base_url=helper_prov.base_url,
        model=helper_slot.model,
    )

    emb_slot = cfg.slots["embedding"]
    emb_prov = cfg.providers[emb_slot.provider_id]
    embedding = EmbeddingConfig(
        api_key=emb_prov.api_key,
        base_url=emb_prov.base_url,
        model=emb_slot.model,
    )

    return claude, openai, embedding
