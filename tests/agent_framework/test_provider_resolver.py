"""
@file_name: test_provider_resolver.py
@author: Bin Liang
@date: 2026-04-23
@description: ProviderResolver decision tree, aligned with business-layer
`get_user_llm_configs` (api_config.py) so quota-exhausted users are blocked
at the middleware layer with a clear, actionable error_code.

Decision tree:

  0. SystemProviderService.is_enabled() == False
     -> request path (default): strict no-op.
     -> background path (own_config_when_system_disabled=True): fall through to
        the user's own config; missing own config -> NoProviderConfiguredError.
  1. quota row exists and prefer_system_override=True (default for new users)
     1a. has budget  -> route "system"
     1b. no budget + has own complete config -> auto-switch (#48): compare-and-
         swap the preference OFF, route "user", winner fires a one-time notice
     1c. no budget + no own provider          -> QuotaExceededError
         (user must add a provider)
  2. prefer_system_override=False (or quota row missing = implicit opt-out)
     2a. has own complete config -> route "user" (quota NOT consulted)
     2b. no own provider          -> NoProviderConfiguredError
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.agent_framework.api_config import (
    get_provider_source,
    set_provider_source,
)
from xyz_agent_context.agent_framework.provider_resolver import (
    NoProviderConfiguredError,
    ProviderResolver,
    ProviderResolverError,
    QuotaExceededError,
)
from xyz_agent_context.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    ProviderSource,
    SlotConfig,
)


# ---------- helpers -------------------------------------------------------

def _complete_user_cfg():
    prov_anth = ProviderConfig(
        provider_id="p_a",
        name="mine-a",
        source=ProviderSource.USER,
        protocol=ProviderProtocol.ANTHROPIC,
        auth_type=AuthType.API_KEY,
        api_key="sk-user-anth",
        is_active=True,
        models=["claude-x"],
    )
    prov_oai = ProviderConfig(
        provider_id="p_o",
        name="mine-o",
        source=ProviderSource.USER,
        protocol=ProviderProtocol.OPENAI,
        auth_type=AuthType.API_KEY,
        api_key="sk-user-oai",
        is_active=True,
        models=["gpt-x", "emb-x"],
    )
    return LLMConfig(
        providers={"p_a": prov_anth, "p_o": prov_oai},
        slots={
            "agent": SlotConfig(provider_id="p_a", model="claude-x"),
            "embedding": SlotConfig(provider_id="p_o", model="emb-x"),
            "helper_llm": SlotConfig(provider_id="p_o", model="gpt-x"),
        },
    )


def _system_cfg():
    return LLMConfig(
        providers={
            "sys_a": ProviderConfig(
                provider_id="sys_a",
                name="sys-a",
                source=ProviderSource.NETMIND,
                protocol=ProviderProtocol.ANTHROPIC,
                auth_type=AuthType.BEARER_TOKEN,
                api_key="sk-system",
                is_active=True,
                models=["sys-claude"],
            ),
            "sys_o": ProviderConfig(
                provider_id="sys_o",
                name="sys-o",
                source=ProviderSource.NETMIND,
                protocol=ProviderProtocol.OPENAI,
                auth_type=AuthType.API_KEY,
                api_key="sk-system",
                is_active=True,
                models=["sys-emb", "sys-gpt"],
            ),
        },
        slots={
            "agent": SlotConfig(provider_id="sys_a", model="sys-claude"),
            "embedding": SlotConfig(provider_id="sys_o", model="sys-emb"),
            "helper_llm": SlotConfig(provider_id="sys_o", model="sys-gpt"),
        },
    )


def _mk_sys(enabled: bool, cfg=None):
    m = MagicMock()
    m.is_enabled.return_value = enabled
    if cfg is not None:
        m.get_config.return_value = cfg
    return m


def _mk_user_svc(user_cfg):
    m = MagicMock()
    m.get_user_config = AsyncMock(return_value=user_cfg)
    return m


def _mk_quota_svc(*, prefer_system: bool | None, has_budget: bool,
                  flip_wins: bool = True):
    """`prefer_system=None` means no quota row exists; True/False is the
    switch-notice LATCH state (the user-facing preference is gone —
    free-tier-first is platform behavior). `flip_wins` controls whether this
    caller wins the 1→0 CAS on exhaustion (#48) — True for the single
    winner (fires the one-time auto-switch notice), False for a concurrent
    loser."""
    m = MagicMock()
    if prefer_system is None:
        m.get = AsyncMock(return_value=None)
    else:
        quota_row = MagicMock()
        quota_row.prefer_system_override = prefer_system
        m.get = AsyncMock(return_value=quota_row)
    m.check = AsyncMock(return_value=has_budget)
    m.disable_preference_if_enabled = AsyncMock(return_value=flip_wins)
    m.rearm_switch_notice = AsyncMock()
    return m


@pytest.fixture(autouse=True)
def _reset_context():
    set_provider_source(None)
    yield
    set_provider_source(None)


@pytest.fixture(autouse=True)
def _stub_single_resolver(monkeypatch):
    """resolve()'s USER branch delegates config-building to the single-point
    driver resolver (resolve_user_runtime_llm_configs). These tests exercise
    the routing DECISION tree, not config contents, so stub the builder to a
    bare RuntimeLLMConfigs — no seeded DB needed."""
    from xyz_agent_context.agent_framework import provider_driver
    from xyz_agent_context.agent_framework.api_config import (
        ClaudeConfig,
        OpenAIConfig,
        RuntimeLLMConfigs,
    )

    async def _fake(_user_id, _db, agent_id=None):
        return RuntimeLLMConfigs(claude=ClaudeConfig(), openai=OpenAIConfig())

    monkeypatch.setattr(
        provider_driver, "resolve_user_runtime_llm_configs", _fake
    )
    yield


# ---------- Branch 0: feature disabled -----------------------------------

@pytest.mark.asyncio
async def test_system_disabled_is_strict_noop():
    user_svc = _mk_user_svc(None)
    quota_svc = _mk_quota_svc(prefer_system=True, has_budget=True)
    r = ProviderResolver(
        user_provider_svc=user_svc,
        system_provider_svc=_mk_sys(enabled=False),
        quota_svc=quota_svc,
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() is None
    # Must NOT have touched either downstream service.
    user_svc.get_user_config.assert_not_called()
    quota_svc.get.assert_not_called()
    quota_svc.check.assert_not_called()


@pytest.mark.asyncio
async def test_system_disabled_falls_through_to_own_config_when_flagged(monkeypatch):
    """Background helper injection (local/desktop mode): with SYSTEM_DISABLED,
    ``own_config_when_system_disabled=True`` must fall through to the user's OWN
    provider config instead of a strict no-op. The background path clears the
    ContextVars first, so a no-op leaves the helper config EMPTY and detached
    hooks 401 on the bare platform OpenAI endpoint. Mirrors the agent-loop path.
    """
    from xyz_agent_context.agent_framework import provider_driver
    from xyz_agent_context.agent_framework.api_config import (
        ClaudeConfig,
        OpenAIConfig,
        RuntimeLLMConfigs,
        clear_user_config,
        openai_config,
    )

    async def _own(_user_id, _db, agent_id=None):
        return RuntimeLLMConfigs(
            claude=ClaudeConfig(api_key="own-claude"),
            openai=OpenAIConfig(
                api_key="own-openai-key",
                base_url="https://api.netmind.ai/inference-api/openai/v1",
                model="deepseek",
            ),
        )

    monkeypatch.setattr(provider_driver, "resolve_user_runtime_llm_configs", _own)

    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(None),
        system_provider_svc=_mk_sys(enabled=False),
        quota_svc=_mk_quota_svc(prefer_system=True, has_budget=True),
    )
    clear_user_config()
    await r.resolve_and_set("usr_x", own_config_when_system_disabled=True)

    # The user's OWN helper config must now be live (not the empty default).
    assert openai_config.api_key == "own-openai-key"
    assert openai_config.base_url == "https://api.netmind.ai/inference-api/openai/v1"
    assert get_provider_source() == "user"


@pytest.mark.asyncio
async def test_system_disabled_flagged_no_own_config_raises_catchable_error(monkeypatch):
    """SYSTEM_DISABLED + own config missing/broken: the fall-through must raise a
    ``NoProviderConfiguredError`` (the ``ProviderResolverError`` family the
    background callers catch to fire a credential alert) — NOT the raw
    ``LLMConfigNotConfigured`` (an ``LLMResolverError``/``RuntimeError``), which
    would slip past ``except ProviderResolverError`` into a generic handler that
    continues on the cleared/global platform key (the 2026-07 incident). The
    ContextVars must also stay cleared (no silent platform-key fallback).
    """
    from xyz_agent_context.agent_framework import provider_driver
    from xyz_agent_context.agent_framework.api_config import (
        LLMConfigNotConfigured,
        _openai_ctx,
        clear_user_config,
    )

    async def _no_own(_user_id, _db, agent_id=None):
        raise LLMConfigNotConfigured("no usable provider for user")

    monkeypatch.setattr(provider_driver, "resolve_user_runtime_llm_configs", _no_own)

    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(None),
        system_provider_svc=_mk_sys(enabled=False),
        quota_svc=_mk_quota_svc(prefer_system=True, has_budget=True),
    )
    clear_user_config()
    with pytest.raises(NoProviderConfiguredError):
        await r.resolve_and_set("usr_x", own_config_when_system_disabled=True)

    # The per-user ContextVar must NOT have been written (assert on the ctxvar
    # directly, NOT via ``openai_config`` — that proxy falls back to the global
    # _holder / platform key, so it can't distinguish "cleared" from "fell back
    # to platform key"). Platform-key isolation is guaranteed by the caller
    # aborting on NoProviderConfiguredError + alerting, not by an empty read.
    assert _openai_ctx.get() is None
    assert get_provider_source() is None


# ---------- Branch 1: opted-in (prefer_system_override=True) -------------

@pytest.mark.asyncio
async def test_opted_in_with_budget_routes_system_even_when_own_config_exists():
    """Critical: user opted in to free tier honours the choice even if they
    also have a complete own config. This is how users who configured a
    provider but want to burn the free tier first keep their preference."""
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(_complete_user_cfg()),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=_mk_quota_svc(prefer_system=True, has_budget=True),
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() == "system"


@pytest.mark.asyncio
async def test_opted_in_exhausted_with_own_config_auto_migrates_to_user(monkeypatch):
    """#48: exhausted free tier + complete own config no longer 402s. The
    free-tier preference is auto-disabled (compare-and-swap) and the request
    routes to the user's own provider, so the configured key is actually used.
    The winning flip fires exactly one auto-switch notice."""
    from xyz_agent_context.agent_framework import provider_resolver as pr
    notice = AsyncMock()
    monkeypatch.setattr(pr, "_emit_free_tier_switch_notice", notice)

    quota_svc = _mk_quota_svc(prefer_system=True, has_budget=False)
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(_complete_user_cfg()),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=quota_svc,
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() == "user"
    quota_svc.disable_preference_if_enabled.assert_awaited_once_with("usr_x")
    notice.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_exhaustion_flip_notifies_only_the_winner(monkeypatch):
    """Under concurrent exhausted requests, the compare-and-swap lets exactly
    one caller flip 1→0. A loser (disable_preference_if_enabled → False) still
    routes to the user's key but must NOT emit a second notice."""
    from xyz_agent_context.agent_framework import provider_resolver as pr
    notice = AsyncMock()
    monkeypatch.setattr(pr, "_emit_free_tier_switch_notice", notice)

    quota_svc = _mk_quota_svc(prefer_system=True, has_budget=False, flip_wins=False)
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(_complete_user_cfg()),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=quota_svc,
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() == "user"
    notice.assert_not_awaited()


@pytest.mark.asyncio
async def test_opted_in_exhausted_without_own_provider_raises_quota_exceeded():
    """Frontend should direct this user to add a provider."""
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(None),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=_mk_quota_svc(prefer_system=True, has_budget=False),
    )
    with pytest.raises(QuotaExceededError) as exc_info:
        await r.resolve_and_set("usr_x")
    assert exc_info.value.user_id == "usr_x"
    assert exc_info.value.error_code == "QUOTA_EXCEEDED_NO_USER_PROVIDER"


# ---------- Branch 2: opted-out (prefer_system_override=False) -----------

@pytest.mark.asyncio
async def test_no_quota_row_with_own_config_routes_user_without_checking_quota():
    """No quota row = no free tier granted: strictly the user's own key,
    quota never probed (implicit-grant liability guard)."""
    quota_svc = _mk_quota_svc(prefer_system=None, has_budget=True)
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(_complete_user_cfg()),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=quota_svc,
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() == "user"
    quota_svc.check.assert_not_called()


@pytest.mark.asyncio
async def test_fired_latch_with_budget_routes_system_and_rearms():
    """Free-tier-first is platform behavior (the opt-out preference is
    gone, 2026-07-18): a replenished quota routes "system" even when the
    notice latch is still fired (0), and the latch is re-armed so the next
    exhaustion notifies again."""
    quota_svc = _mk_quota_svc(prefer_system=False, has_budget=True)
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(None),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=quota_svc,
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() == "system"
    quota_svc.rearm_switch_notice.assert_awaited_once_with("usr_x")


@pytest.mark.asyncio
async def test_armed_latch_with_budget_does_not_rewrite():
    """No redundant DB write on the hot path: the latch is only re-armed on
    the 0→1 edge."""
    quota_svc = _mk_quota_svc(prefer_system=True, has_budget=True)
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(None),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=quota_svc,
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() == "system"
    quota_svc.rearm_switch_notice.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_quota_row_behaves_as_opted_out():
    """A user whose quota row never got seeded (edge case, e.g. registration
    partially failed) must behave as opted-out — otherwise we'd grant the
    free tier implicitly, creating an unbounded liability."""
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(_complete_user_cfg()),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=_mk_quota_svc(prefer_system=None, has_budget=True),
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() == "user"


# ---------- Completeness check on own config -----------------------------

@pytest.mark.asyncio
async def test_no_quota_row_with_partial_own_config_still_raises():
    cfg = _complete_user_cfg()
    cfg.slots.pop("helper_llm")
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(cfg),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=_mk_quota_svc(prefer_system=None, has_budget=True),
    )
    with pytest.raises(NoProviderConfiguredError):
        await r.resolve_and_set("usr_x")


@pytest.mark.asyncio
async def test_no_quota_row_with_inactive_provider_still_raises():
    cfg = _complete_user_cfg()
    cfg.providers["p_a"].is_active = False
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(cfg),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=_mk_quota_svc(prefer_system=None, has_budget=True),
    )
    with pytest.raises(NoProviderConfiguredError):
        await r.resolve_and_set("usr_x")


# ---------- Exception hierarchy ------------------------------------------

def test_exception_hierarchy_shares_base():
    assert issubclass(QuotaExceededError, ProviderResolverError)
    assert issubclass(NoProviderConfiguredError, ProviderResolverError)


def test_error_codes_are_stable_strings():
    """Frontend pattern-matches on these; they're part of the API contract."""
    assert QuotaExceededError("u").error_code == "QUOTA_EXCEEDED_NO_USER_PROVIDER"
    assert NoProviderConfiguredError("u").error_code == "NO_PROVIDER_CONFIGURED"
