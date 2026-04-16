"""
@file_name: provider_resolver.py
@author: Bin Liang
@date: 2026-04-16
@description: Per-request routing between a user's own LLM config and the
system-default NetMind config, with quota gating on the system branch.

Wired into backend.auth.auth_middleware. Four branches:

  A. SystemProviderService.is_enabled() == False
     -> strict no-op; local mode / disabled env leaves every ContextVar
        untouched. Agent code paths continue to use the existing
        llm_config.json global fallback.

  B. User has a complete own config (all three slots + active providers)
     -> convert LLMConfig to the three dataclasses set_user_config expects,
        set_user_config(claude, openai, embedding), tag provider_source="user".
        Quota is NOT consulted on this branch.

  C. User incomplete AND system enabled AND quota available
     -> convert SystemProviderService's LLMConfig to three dataclasses,
        set_user_config, tag provider_source="system". cost_tracker will
        deduct quota post-call.

  D. User incomplete AND system enabled AND no quota
     -> raise QuotaExceededError; auth_middleware translates to HTTP 402.
"""
from __future__ import annotations

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


class QuotaExceededError(Exception):
    """Raised when the system-default branch is required but budget is gone."""

    def __init__(self, user_id: str):
        super().__init__(f"quota exceeded for {user_id}")
        self.user_id = user_id


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

    async def resolve_and_set(self, user_id: str) -> None:
        # Branch A: feature disabled (local mode or env not set).
        # Strict no-op — must not query user_providers, must not touch any
        # ContextVar. AgentRuntime's own set_user_config path handles
        # local-mode provider loading via llm_config.json.
        if not self.system_provider_svc.is_enabled():
            return

        user_cfg = await self.user_provider_svc.get_user_config(user_id)
        if _is_user_config_complete(user_cfg):
            claude, openai, embedding = _llm_config_to_dataclasses(user_cfg)
            set_user_config(claude, openai, embedding)
            set_provider_source("user")
            return

        if await self.quota_svc.check(user_id):
            sys_cfg = self.system_provider_svc.get_config()
            claude, openai, embedding = _llm_config_to_dataclasses(sys_cfg)
            set_user_config(claude, openai, embedding)
            set_provider_source("system")
            return

        raise QuotaExceededError(user_id)


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
