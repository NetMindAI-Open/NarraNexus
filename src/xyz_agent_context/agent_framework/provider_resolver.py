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

from enum import Enum
from typing import Optional

from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
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


_REQUIRED_SLOTS = ("agent", "helper_llm")


class ProviderAvailability(str, Enum):
    """Verdict of the provider-resolution decision tree, WITHOUT building any
    config or raising. The single source of truth shared by every caller that
    needs to know "can this user resolve a usable provider right now":

    - the HTTP path (`ProviderResolver.resolve` maps each verdict to a config
      or a `ProviderResolverError`),
    - the job resume gate (`JobTrigger._user_can_run` → `is_runnable`).

    Having one classifier eliminates the drift that caused the 2026-05-31
    pause/resume oscillation, where the resume gate reimplemented the tree and
    disagreed with the runtime (it ignored `prefer_system_override`).
    """

    SYSTEM_OK = "system_ok"                    # free tier has budget → route system
    USER_OK = "user_ok"                        # opted out + complete own config → route user
    FREE_TIER_EXHAUSTED = "free_tier_exhausted"  # opted in, no budget, but has own config
    QUOTA_EXCEEDED = "quota_exceeded"          # opted in, no budget, no own provider
    NO_PROVIDER = "no_provider"                # opted out, own config missing/incomplete
    SYSTEM_DISABLED = "system_disabled"        # feature off (local mode) → not gated, passthrough


def is_runnable(verdict: ProviderAvailability) -> bool:
    """True when a run for this verdict would resolve a provider. The three
    exhaustion/missing verdicts are NOT runnable — the runtime would refuse,
    so the resume gate must refuse too."""
    return verdict in (
        ProviderAvailability.SYSTEM_OK,
        ProviderAvailability.USER_OK,
        ProviderAvailability.SYSTEM_DISABLED,
    )


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

    async def classify(self, user_id: str) -> ProviderAvailability:
        """Decide WHICH provider a run for this user would resolve, WITHOUT
        building any config, mutating ContextVars, or raising.

        This is the single source of truth for the decision tree. ``resolve``
        maps the verdict to a config/exception; the job resume gate maps it via
        ``is_runnable``. Service-call order (is_enabled → quota.get →
        get_user_config → quota.check, the last only on the opted-in branch) is
        deliberate: it preserves the strict-no-op laziness on the disabled path
        and never probes quota on the opt-out path.
        """
        # Branch 0: feature disabled (local mode / env not set) — strict no-op.
        if not self.system_provider_svc.is_enabled():
            return ProviderAvailability.SYSTEM_DISABLED

        quota = await self.quota_svc.get(user_id)
        prefer_system = quota is not None and quota.prefer_system_override

        has_own = _is_user_config_complete(
            await self.user_provider_svc.get_user_config(user_id)
        )

        if prefer_system:
            # Branch 1: user opted in to the free tier — honour it even if they
            # also configured an own provider (the runtime will NOT fall back
            # to it; that asymmetry is what the resume gate must respect).
            if await self.quota_svc.check(user_id):
                return ProviderAvailability.SYSTEM_OK
            return (
                ProviderAvailability.FREE_TIER_EXHAUSTED if has_own
                else ProviderAvailability.QUOTA_EXCEEDED
            )

        # Branch 2: opted out (or no quota row) — own provider only.
        return ProviderAvailability.USER_OK if has_own else ProviderAvailability.NO_PROVIDER

    async def resolve(
        self, user_id: str
    ) -> Optional[tuple[ClaudeConfig, OpenAIConfig, str]]:
        """Resolve a user's effective LLM configs WITHOUT mutating ContextVars.

        Returns ``(claude, openai, source)`` where ``source`` is
        ``"system"`` or ``"user"``, or ``None`` when the system-default
        feature is disabled (local mode / env not set) — in that case the
        caller keeps whatever global/desktop config is already in effect.

        Thin mapping over :meth:`classify`: verdict → config or
        ``ProviderResolverError``. Background jobs that need a provider outside
        the request path call this and build their
        own client from the returned config.
        """
        verdict = await self.classify(user_id)

        if verdict == ProviderAvailability.SYSTEM_DISABLED:
            return None
        if verdict == ProviderAvailability.SYSTEM_OK:
            claude, openai = _llm_config_to_dataclasses(
                self.system_provider_svc.get_config()
            )
            return claude, openai, "system"
        if verdict == ProviderAvailability.USER_OK:
            user_cfg = await self.user_provider_svc.get_user_config(user_id)
            claude, openai = _llm_config_to_dataclasses(user_cfg)
            return claude, openai, "user"
        if verdict == ProviderAvailability.FREE_TIER_EXHAUSTED:
            raise FreeTierExhaustedError(user_id)
        if verdict == ProviderAvailability.QUOTA_EXCEEDED:
            raise QuotaExceededError(user_id)
        raise NoProviderConfiguredError(user_id)  # NO_PROVIDER

    async def resolve_and_set(self, user_id: str) -> None:
        """Resolve the user's configs and push them onto the request ContextVars.

        Thin wrapper over :meth:`resolve` — the HTTP request path uses this so
        downstream LLM clients pick up the right provider implicitly.
        """
        resolved = await self.resolve(user_id)
        if resolved is None:
            return
        claude, openai, source = resolved
        set_user_config(claude, openai)
        set_provider_source(source)


async def classify_provider_for_user(user_id: str, db) -> ProviderAvailability:
    """Wire the default services and return the classification verdict, for
    non-HTTP callers that don't already hold a ``ProviderResolver`` — the job
    resume gate and the (future) edge-recovery hooks. Keeps every caller on the
    one decision tree.
    """
    from xyz_agent_context.agent_framework.user_provider_service import (
        UserProviderService,
    )
    resolver = ProviderResolver(
        user_provider_svc=UserProviderService(db),
        system_provider_svc=SystemProviderService.instance(),
        quota_svc=QuotaService.default(),
    )
    return await resolver.classify(user_id)


async def resolve_and_set_provider_for_user(user_id: str, db) -> None:
    """Wire the default services and push the user's effective LLM config
    onto this task's ContextVars — the background-job twin of the
    auth_middleware path, for callers that run OUTSIDE any HTTP request
    (memory consolidation worker; future lifespan jobs).

    Local mode / system-provider disabled: strict no-op, the global
    llm_config.json / .env fallback stays in effect (iron rule #7).
    Quota / no-provider verdicts raise the same ProviderResolverError
    subclasses the request path uses — callers isolate, never drop data.
    """
    from xyz_agent_context.agent_framework.user_provider_service import (
        UserProviderService,
    )
    resolver = ProviderResolver(
        user_provider_svc=UserProviderService(db),
        system_provider_svc=SystemProviderService.instance(),
        quota_svc=QuotaService.default(),
    )
    await resolver.resolve_and_set(user_id)


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
) -> tuple[ClaudeConfig, OpenAIConfig]:
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

    return claude, openai
