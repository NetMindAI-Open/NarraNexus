"""
@file_name: resolver.py
@author: Bin Liang
@date: 2026-04-16
@description: Resolve which LLM config a user's run uses.

Wired into backend.auth.auth_middleware and into every background path that
runs code on a user's behalf. Decision tree:

  0. Not cloud mode (local / desktop)
     -> request path (default): strict no-op. Local installs keep the global
        llm_config.json fallback, and clearing or overwriting the ContextVars
        would break it.
     -> background path (``own_config_when_system_disabled=True``): resolve the
        user's OWN config anyway, because the detached-helper injection clears
        the ContextVars first and a no-op would leave them empty.

  1. Cloud + the user's config is complete -> USER_OK.
  2. Cloud + config missing or incomplete  -> NoProviderConfiguredError.

That is the whole tree. It used to have a third branch — a platform "system"
tier with its own token quota, gated before the run — and that branch is gone
(2026-07-28): the free tier is now a $10 wallet on the LiteLLM gateway,
registered as an ORDINARY provider card (``providers/free_tier.py``,
``backend/integrations/free_tier/``). A user on the free tier is, from here,
indistinguishable from a user on their own NetMind key.

Two consequences worth stating, because they moved responsibility out of this
file:

  * **Exhaustion is no longer a pre-run gate.** The gateway refuses the call
    when the wallet is empty, so it surfaces as a provider error mid-run and is
    classified by ``llm/failure.py`` (``insufficient_balance``) like any other
    out-of-credit condition. Background jobs still pause via that classifier
    instead of retry-storming.
  * **No model is preempted.** There is no fixed platform model to lock users
    to, so per-agent model overrides always apply and the UI no longer needs a
    "your setting is ignored while the free tier lasts" banner.

The remaining exception carries a stable `error_code` that auth_middleware
returns verbatim; the frontend pattern-matches it to pick remediation UI.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from loguru import logger

from xyz_agent_context.agent_framework.api_config import (
    RuntimeLLMConfigs,
    clear_user_config,
    set_provider_source,
    set_user_config,
)
from xyz_agent_context.schema.provider_schema import LLMConfig


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
    disagreed with the runtime.
    """

    USER_OK = "user_ok"                  # complete config → route to it
    NO_PROVIDER = "no_provider"          # config missing/incomplete
    LOCAL_PASSTHROUGH = "local_passthrough"  # not cloud → not gated at all


def is_runnable(verdict: ProviderAvailability) -> bool:
    """True when a run for this verdict would resolve a provider. NO_PROVIDER
    is NOT runnable — the runtime would refuse, so the resume gate must too."""
    return verdict in (
        ProviderAvailability.USER_OK,
        ProviderAvailability.LOCAL_PASSTHROUGH,
    )


class ProviderResolverError(Exception):
    """Base for resolver-side LLM routing errors. auth_middleware catches
    this base class once, reads `error_code` + message, returns HTTP 402."""

    error_code: str = "PROVIDER_RESOLVER_ERROR"

    def __init__(self, user_id: str, message: str | None = None):
        super().__init__(message or f"{self.error_code} for {user_id}")
        self.user_id = user_id


class NoProviderConfiguredError(ProviderResolverError):
    """The user has no usable provider.

    On cloud this now covers what used to be two distinct states — "never got a
    free tier" and "free tier exhausted with nothing else configured" — because
    an exhausted wallet is still a perfectly well-formed provider card. Running
    dry surfaces at CALL time (the gateway refuses), not here.

    The message's leading phrase is load-bearing: ``job_trigger``'s
    ``_NO_QUOTA_ERROR_MARKERS`` substring-matches it so background jobs pause
    instead of retry-storming. ``test_no_quota_pause`` pins this.
    """

    error_code = "NO_PROVIDER_CONFIGURED"

    def __init__(self, user_id: str):
        super().__init__(
            user_id,
            "No provider configured. Add a provider in Settings → Providers "
            "to continue — or subscribe to a NetMind.AI plan and link it in "
            "Settings → Account & Subscription.",
        )


class ProviderResolver:
    """Arbitrates which LLMConfig feeds the current request's ContextVar."""

    def __init__(self, user_provider_svc):  # UserProviderService (duck-typed)
        self.user_provider_svc = user_provider_svc

    async def classify(self, user_id: str) -> ProviderAvailability:
        """Decide WHICH provider a run for this user would resolve, WITHOUT
        building any config, mutating ContextVars, or raising.

        This is the single source of truth for the decision tree; ``resolve``
        maps the verdict to a config/exception and the job resume gate maps it
        via ``is_runnable``.
        """
        from xyz_agent_context.utils.deployment_mode import is_cloud_mode

        if not is_cloud_mode():
            return ProviderAvailability.LOCAL_PASSTHROUGH

        has_own = is_user_config_complete(
            await self.user_provider_svc.get_user_config(user_id)
        )
        return (
            ProviderAvailability.USER_OK
            if has_own
            else ProviderAvailability.NO_PROVIDER
        )

    async def resolve(
        self, user_id: str, agent_id: str | None = None
    ) -> Optional[tuple[RuntimeLLMConfigs, str]]:
        """Resolve a user's effective LLM configs WITHOUT mutating ContextVars.

        Returns ``(RuntimeLLMConfigs, source)``, or ``None`` in local mode —
        in that case the caller keeps whatever global/desktop config is in
        effect.

        Delegates to the single-point Provider Driver resolver
        (``resolve_user_runtime_llm_configs``) — the SAME builder the agent-loop
        path uses — so a codex agent or an anthropic-protocol helper is wired
        correctly here too, and per-agent slot overrides are applied when
        ``agent_id`` is given. There is intentionally no second, protocol-blind
        builder on this path: that drift was the root of the consolidation
        anthropic-helper bug.
        """
        verdict = await self.classify(user_id)

        if verdict == ProviderAvailability.LOCAL_PASSTHROUGH:
            return None
        if verdict == ProviderAvailability.USER_OK:
            from xyz_agent_context.agent_framework.providers.driver import (
                resolve_user_runtime_llm_configs,
            )

            # Use the db behind the injected user_provider_svc (DI), not a
            # global — the same dependency classify() already read from.
            cfgs = await resolve_user_runtime_llm_configs(
                user_id, self.user_provider_svc.db, agent_id=agent_id
            )
            return cfgs, "user"
        raise NoProviderConfiguredError(user_id)  # NO_PROVIDER

    async def resolve_and_set(
        self,
        user_id: str,
        *,
        agent_id: str | None = None,
        own_config_when_system_disabled: bool = False,
    ) -> None:
        """Resolve the user's configs and push them onto this task's ContextVars.

        Thin wrapper over :meth:`resolve` — pushes ALL FOUR configs (claude /
        openai / codex / anthropic_helper) so the helper-SDK factory and the
        codex agent slot are wired correctly off the request/background task.

        ``own_config_when_system_disabled`` governs the local-mode branch
        (``resolve`` returns None):

        - **False (default, request path)**: strict no-op. The caller keeps
          whatever global/desktop config is already in effect (the auth
          middleware never clears the ContextVars).
        - **True (background path)**: resolve the user's OWN config anyway.
          ``inject_owner_helper_credentials`` clears the ContextVars first, so a
          no-op would leave the helper config EMPTY and detached hooks (memory /
          entity / narrative) would 401 on the bare platform OpenAI endpoint.

          The strict own-config resolver raises ``LLMConfigNotConfigured`` (an
          ``LLMResolverError``/``RuntimeError``) when the owner has no usable
          config — a DIFFERENT family than the ``ProviderResolverError`` this
          method's callers catch. We translate it so the exception contract
          holds: callers' ``except ProviderResolverError`` still fires the
          credential alert instead of the exception slipping into a generic
          ``except`` that continues on the cleared/global platform key (the
          exact 2026-07 incident this path prevents).
        """
        resolved = await self.resolve(user_id, agent_id=agent_id)
        if resolved is None:
            if not own_config_when_system_disabled:
                return
            from xyz_agent_context.agent_framework.api_config import (
                LLMConfigNotConfigured,
            )
            from xyz_agent_context.agent_framework.providers.driver import (
                resolve_user_runtime_llm_configs,
            )

            try:
                cfgs = await resolve_user_runtime_llm_configs(
                    user_id, self.user_provider_svc.db, agent_id=agent_id
                )
            except LLMConfigNotConfigured as e:
                raise NoProviderConfiguredError(user_id) from e
            source = "user"
        else:
            cfgs, source = resolved

        set_user_config(
            cfgs.claude, cfgs.openai, cfgs.codex, cfgs.anthropic_helper,
            cfgs.cli_helper,
        )
        set_provider_source(source)


async def classify_provider_for_user(user_id: str, db) -> ProviderAvailability:
    """Wire the default service and return the classification verdict, for
    non-HTTP callers that don't already hold a ``ProviderResolver`` — the job
    resume gate and the edge-recovery hooks. Keeps every caller on the one
    decision tree.
    """
    from xyz_agent_context.agent_framework.providers.user_service import (
        UserProviderService,
    )
    return await ProviderResolver(UserProviderService(db)).classify(user_id)


async def resolve_and_set_provider_for_user(
    user_id: str, db, agent_id: str | None = None
) -> None:
    """Wire the default service and push the user's effective LLM config onto
    this task's ContextVars — the background-job twin of the auth_middleware
    path, for callers that run OUTSIDE any HTTP request (memory consolidation
    worker; lifespan jobs).

    When ``agent_id`` is given, the owner's config is overlaid with that agent's
    per-agent slot overrides (helper_llm is per-agent, so a background helper
    task for agent A must use A's helper override, not the owner default).

    Local mode: falls through to the user's OWN provider config (NOT a no-op) —
    see ``resolve_and_set``'s docstring for why.

    No-provider verdicts raise the same ProviderResolverError subclass the
    request path uses — callers isolate, never drop data.
    """
    from xyz_agent_context.agent_framework.providers.user_service import (
        UserProviderService,
    )
    await ProviderResolver(UserProviderService(db)).resolve_and_set(
        user_id, agent_id=agent_id, own_config_when_system_disabled=True
    )


async def inject_owner_helper_credentials(agent_id: str, db) -> Optional[str]:
    """Put the agent OWNER's effective LLM config onto this task's ContextVars.

    Call this at the top of every DETACHED background task (``asyncio.create_task``)
    that makes helper-LLM calls — the narrative updater, the Step-5 entity/memory
    hooks. Those tasks do NOT inherit the per-turn ContextVar that
    ``AgentRuntime.run`` sets (it is set inside an async generator whose context
    does not propagate to children spawned off the driver task), so without this
    they fall through ``_ConfigProxy`` to the global ``_holder`` — i.e. the
    platform ``settings.openai_api_key``. That is exactly the 2026-07 incident:
    an expired platform OpenAI key 401'd every background helper call for ~2
    weeks while long memory silently degraded. This is the background twin of
    ``AgentRuntime.run``'s ``set_user_config`` and of the memory worker's own
    injection (which now delegates here).

    ``clear_user_config`` runs first so a task that reused this coroutine for a
    different tenant (or that cannot resolve an owner) cannot inherit the
    previous tenant's credentials.

    Returns the resolved owner ``user_id``, or ``None`` when the agent row has
    no owner (creds left cleared → the strict global fallback applies, which in
    cloud mode has no usable key so the helper call fails fast rather than
    billing the platform). Raises ``ProviderResolverError`` subclasses — the
    caller isolates the scope and surfaces a credential alert rather than
    dropping to the platform key.
    """
    # Reset first: never inherit a prior tenant's creds when we bail early.
    clear_user_config()
    agent_row = await db.get_one("agents", {"agent_id": agent_id})
    owner = (agent_row or {}).get("created_by")
    if not owner:
        logger.warning(
            f"[background-llm] agent {agent_id} has no owner row — helper "
            f"credentials left cleared (global fallback)."
        )
        return None
    # Pass agent_id so the owner's helper_llm is overlaid with this agent's
    # per-agent helper override (helper follows its agent).
    await resolve_and_set_provider_for_user(owner, db, agent_id=agent_id)
    return owner


def is_user_config_complete(cfg: LLMConfig | None) -> bool:
    """All required slots present, each with a non-empty model, each pointing
    to an active provider that exists in `cfg.providers`.

    Public (it was ``_``-prefixed while pretending to be private): both
    provisioners read it to decide register-only vs activate, and that is a
    legitimate cross-package question, not an internal detail.
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
