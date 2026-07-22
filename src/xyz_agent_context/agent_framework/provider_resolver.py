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
     -> request path (default): strict no-op; local mode / disabled env leaves
        every ContextVar untouched, agent code paths keep the existing
        llm_config.json global fallback.
     -> background path (resolve_and_set(..., own_config_when_system_disabled=
        True)): fall through to the user's OWN provider config, because the
        detached-helper injection clears the ContextVars first. See
        resolve_and_set / resolve_and_set_provider_for_user.

  1. quota row exists (a free tier was granted). "Free tier first" is
     PLATFORM BEHAVIOR, not a user preference (2026-07-18 — the old
     prefer_system toggle was removed; everyone draws the free tier
     first while it lasts):
     1a. quota has budget  -> route "system" (cost_tracker deducts post-call)
     1b. no budget + has complete own config -> route "user" on their own
         key. The one-time "switched to your own key" notice is deduped via
         the prefer_system_override column, repurposed as a NOTICE LATCH:
         armed (1) while on the free tier, CAS 1→0 on the first exhausted
         run (that caller emits the notice, #48), re-armed on the next run
         with budget. NOTE: the free tier is a one-time registration grant
         with no periodic refresh — budget only comes back via a manual
         staff grant, at which point the user auto-returns to the free tier.
     1c. no budget + no own provider         -> QuotaExceededError
         (user must add a provider before the app becomes usable again)

  2. no quota row at all (no free tier granted):
     2a. has complete own config -> route "user" (quota NOT consulted)
     2b. own config missing / incomplete -> NoProviderConfiguredError

All three exceptions carry a stable `error_code` class attribute that
auth_middleware returns verbatim to the client; the frontend
pattern-matches on this string to decide which remediation UI to show.
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Optional

from loguru import logger

from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    OpenAIConfig,
    RuntimeLLMConfigs,
    clear_user_config,
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
    USER_OK = "user_ok"                        # complete own config → route user
    QUOTA_EXCEEDED = "quota_exceeded"          # free tier exhausted, no own provider
    NO_PROVIDER = "no_provider"                # no free tier, own config missing/incomplete
    SYSTEM_DISABLED = "system_disabled"        # feature off (local mode) → not gated, passthrough


def is_runnable(verdict: ProviderAvailability) -> bool:
    """True when a run for this verdict would resolve a provider. The two
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
    """The free tier is exhausted AND the user has no own provider
    configured — to continue they must either add one, or subscribe to a
    NetMind.AI plan AND re-login so the auto-link mints a key for them."""

    error_code = "QUOTA_EXCEEDED_NO_USER_PROVIDER"

    def __init__(self, user_id: str):
        # Two things are load-bearing in this string.
        #
        # 1. The leading "Free quota exhausted." phrase: job_trigger's
        #    _NO_QUOTA_ERROR_MARKERS substring-matches it as its legacy third
        #    detection layer, so background jobs pause instead of
        #    retry-storming. test_no_quota_pause pins this.
        # 2. The "link it in Settings" step is not padding. Subscribing does
        #    NOT by itself produce a provider — ensure_netmind_provider runs
        #    on the login path and on POST /providers/use-subscription, which
        #    the Account & Subscription panel's "Link it now" button calls
        #    (first frontend caller, 2026-07-20). Without that step the user
        #    pays and retries into the same 402.
        super().__init__(
            user_id,
            "Free quota exhausted. Add your own provider in Settings → "
            "Providers to continue — or subscribe to a NetMind.AI plan and "
            "link it in Settings → Account & Subscription.",
        )


class NoProviderConfiguredError(ProviderResolverError):
    """No free tier was ever granted (no quota row) and the own provider is
    missing or incomplete. No silent fallback to the free tier — the row IS
    the grant (implicit-grant liability guard)."""

    error_code = "NO_PROVIDER_CONFIGURED"

    def __init__(self, user_id: str):
        super().__init__(
            user_id,
            "No provider configured. Add a provider in Settings to continue.",
        )


async def _emit_free_tier_switch_notice(db, user_id: str) -> None:
    """One-time inbox notice that the free tier ran out and NarraNexus
    auto-switched the user to their own configured provider (#48).

    Best-effort: the notice is a courtesy, never load-bearing. A failure to
    write it must NOT break the run that triggered the switch, so we swallow
    and log rather than propagate (the run still succeeds on the user's key).
    Callers gate this on ``disable_preference_if_enabled`` returning True, so
    it fires at most once per exhaustion episode; a fresh ``message_id`` per
    call is therefore safe (no concurrent double-write to dedup against).
    """
    try:
        from xyz_agent_context.repository.inbox_repository import InboxRepository
        from xyz_agent_context.schema.inbox_schema import (
            InboxMessageType,
            MessageSource,
        )

        await InboxRepository(db).create_message(
            user_id=user_id,
            message_id=f"freeswitch_{uuid.uuid4().hex[:16]}",
            title="Switched to your own provider",
            content=(
                "Your free-tier quota is used up. NarraNexus has automatically "
                "switched to the API provider you configured — new runs now use "
                "your own key. You can change this any time in Settings → Quota."
            ),
            message_type=InboxMessageType.SYSTEM_NOTICE,
            # `source.type` lets the frontend recognise this specific notice and
            # surface it as a one-time banner (App.tsx), then mark it read.
            source=MessageSource(type="free_tier_switch", id=user_id),
        )
    except Exception as e:  # noqa: BLE001 — notice is best-effort, never fatal
        logger.warning(
            f"free-tier auto-switch notice failed for {user_id}: {e}"
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

        has_own = _is_user_config_complete(
            await self.user_provider_svc.get_user_config(user_id)
        )

        if quota is not None:
            # Branch 1: a free tier was granted. Free-tier-first is platform
            # behavior, not a user choice (the prefer_system toggle was
            # removed 2026-07-18) — while there is budget, every run draws it.
            if await self.quota_svc.check(user_id):
                # Re-arm the switch-notice latch: budget is back (the free
                # tier is a one-time registration grant with no periodic
                # refresh, so in practice this means a manual staff grant),
                # and the NEXT exhaustion should notify again. Write only on
                # the 0→1 edge — once per cycle. Best-effort: the latch is
                # purely cosmetic notice-dedup, and this sits on the
                # SYSTEM_OK success path — a transient DB error here must
                # never fail a run the user has budget for (classify's
                # contract is "without raising").
                if not quota.prefer_system_override:
                    try:
                        await self.quota_svc.rearm_switch_notice(user_id)
                    except Exception as e:  # noqa: BLE001 — cosmetic write
                        logger.warning(
                            f"rearm_switch_notice failed for {user_id} "
                            f"(next exhaustion may not notify): {e}"
                        )
                return ProviderAvailability.SYSTEM_OK
            # Free tier exhausted → the user's own provider takes over
            # immediately (no 402 loop, #48). The prefer_system_override
            # column is repurposed as a notice latch: exactly one concurrent
            # caller wins the 1→0 CAS and surfaces the one-time "switched to
            # your own key" notice — no double-notify under load.
            if has_own:
                if await self.quota_svc.disable_preference_if_enabled(user_id):
                    await _emit_free_tier_switch_notice(
                        self.user_provider_svc.db, user_id
                    )
                return ProviderAvailability.USER_OK
            return ProviderAvailability.QUOTA_EXCEEDED

        # Branch 2: no free tier granted — own provider only.
        return ProviderAvailability.USER_OK if has_own else ProviderAvailability.NO_PROVIDER

    async def resolve(
        self, user_id: str, agent_id: str | None = None
    ) -> Optional[tuple[RuntimeLLMConfigs, str]]:
        """Resolve a user's effective LLM configs WITHOUT mutating ContextVars.

        Returns ``(RuntimeLLMConfigs, source)`` where ``source`` is
        ``"system"`` or ``"user"``, or ``None`` when the system-default
        feature is disabled (local mode / env not set) — in that case the
        caller keeps whatever global/desktop config is already in effect.

        The USER branch delegates to the single-point Provider Driver
        resolver (``resolve_user_runtime_llm_configs``) — the SAME builder the
        agent-loop path uses — so a codex agent or an anthropic-protocol
        helper is wired correctly here too. When ``agent_id`` is given it also
        overlays that agent's per-agent slot overrides on the USER branch.
        There is intentionally no second protocol-blind builder on this path
        (that drift was the root of the consolidation anthropic-helper bug).
        The SYSTEM/free-tier branch keeps the controlled NetMind
        (openai-protocol) shape — per-agent overrides do NOT apply there
        (scope: the free tier is a fixed one-model pool).
        """
        verdict = await self.classify(user_id)

        if verdict == ProviderAvailability.SYSTEM_DISABLED:
            return None
        if verdict == ProviderAvailability.SYSTEM_OK:
            claude, openai = _llm_config_to_dataclasses(
                self.system_provider_svc.get_config()
            )
            return RuntimeLLMConfigs(claude=claude, openai=openai), "system"
        if verdict == ProviderAvailability.USER_OK:
            from xyz_agent_context.agent_framework.provider_driver import (
                resolve_user_runtime_llm_configs,
            )

            # Use the db behind the injected user_provider_svc (DI), not a
            # global — the same dependency classify() already read from.
            cfgs = await resolve_user_runtime_llm_configs(
                user_id, self.user_provider_svc.db, agent_id=agent_id
            )
            return cfgs, "user"
        if verdict == ProviderAvailability.QUOTA_EXCEEDED:
            raise QuotaExceededError(user_id)
        raise NoProviderConfiguredError(user_id)  # NO_PROVIDER

    async def resolve_and_set(
        self,
        user_id: str,
        *,
        agent_id: str | None = None,
        own_config_when_system_disabled: bool = False,
    ) -> None:
        """Resolve the user's configs and push them onto the request ContextVars.

        Thin wrapper over :meth:`resolve` — pushes ALL FOUR configs (claude /
        openai / codex / anthropic_helper) so the helper-SDK factory and the
        codex agent slot are wired correctly off the request/background task.

        ``own_config_when_system_disabled`` governs the SYSTEM_DISABLED branch
        (``resolve`` returns None — local/desktop mode):

        - **False (default, request path)**: strict no-op. The caller keeps
          whatever global/desktop config is already in effect (the auth
          middleware never clears the ContextVars).
        - **True (background path)**: fall through to the user's OWN provider
          config. ``inject_owner_helper_credentials`` clears the ContextVars
          first, so a no-op would leave the helper config EMPTY and detached
          hooks (memory / entity / narrative) would 401 on the bare platform
          OpenAI endpoint. This mirrors the agent-loop path
          (``get_user_runtime_llm_configs`` → ``_get_user_runtime_llm_configs_strict``).

          The strict own-config resolver raises ``LLMConfigNotConfigured`` (an
          ``LLMResolverError``/``RuntimeError``) when the owner has no usable
          config — a DIFFERENT family than the ``ProviderResolverError`` this
          method's callers catch. We translate it to ``NoProviderConfiguredError``
          so the exception contract holds: callers' ``except ProviderResolverError``
          still fires the credential alert instead of the exception slipping
          into a generic ``except`` that continues on the cleared/global
          platform key (the exact 2026-07 incident this path prevents).
        """
        resolved = await self.resolve(user_id, agent_id=agent_id)
        if resolved is None:
            if not own_config_when_system_disabled:
                return
            from xyz_agent_context.agent_framework.api_config import (
                LLMConfigNotConfigured,
            )
            from xyz_agent_context.agent_framework.provider_driver import (
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


async def resolve_and_set_provider_for_user(
    user_id: str, db, agent_id: str | None = None
) -> None:
    """Wire the default services and push the user's effective LLM config
    onto this task's ContextVars — the background-job twin of the
    auth_middleware path, for callers that run OUTSIDE any HTTP request
    (memory consolidation worker; future lifespan jobs).

    When ``agent_id`` is given, the owner's config is overlaid with that
    agent's per-agent slot overrides (helper_llm is per-agent, so a background
    helper task for agent A must use A's helper override, not the owner
    default).

    Local mode / system-provider disabled: falls through to the user's OWN
    provider config (NOT a no-op). Background callers (this + the memory
    consolidation worker) clear the ContextVars before calling, so a no-op
    would leave the helper config empty and detached LLM hooks would 401 on
    the bare platform endpoint — that was the local/desktop-mode gap in the
    detached-helper injection (#68). The agent-loop path already resolves the
    owner's own config here; this mirrors it.

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
    await resolver.resolve_and_set(
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
    billing the platform). Raises ``ProviderResolverError`` subclasses
    (quota exhausted / no provider configured) — the caller isolates the
    scope and surfaces a credential alert rather than dropping to the platform
    key.
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
    """Convert a CONTROLLED LLMConfig (system / free-tier NetMind) into the
    (claude, openai) dataclasses. This is openai-protocol by construction and
    carries no codex / anthropic-helper, so the 2-config shape is correct here.

    NOTE: user configs do NOT go through this — they resolve via the
    protocol/framework-aware single-point resolver
    (``resolve_user_runtime_llm_configs``). Keeping a protocol-blind builder
    on the user path was the root of the anthropic-helper consolidation bug.
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
