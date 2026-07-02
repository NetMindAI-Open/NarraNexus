"""
@file_name: test_provider_resolver.py
@author: Bin Liang
@date: 2026-04-23
@description: ProviderResolver decision tree, aligned with business-layer
`get_user_llm_configs` (api_config.py) so quota-exhausted users are blocked
at the middleware layer with a clear, actionable error_code.

Decision tree:

  0. SystemProviderService.is_enabled() == False -> strict no-op.
  1. quota row exists and prefer_system_override=True (default for new users)
     1a. has budget  -> route "system"
     1b. no budget + has own complete config -> FreeTierExhaustedError
         (user can disable the Settings toggle to switch to own provider)
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
    FreeTierExhaustedError,
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


def _mk_quota_svc(*, prefer_system: bool | None, has_budget: bool):
    """`prefer_system=None` means no quota row exists."""
    m = MagicMock()
    if prefer_system is None:
        m.get = AsyncMock(return_value=None)
    else:
        quota_row = MagicMock()
        quota_row.prefer_system_override = prefer_system
        m.get = AsyncMock(return_value=quota_row)
    m.check = AsyncMock(return_value=has_budget)
    # classify() auto-disables the free-tier preference on exhaustion (#48).
    m.set_preference = AsyncMock()
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

    async def _fake(_user_id, _db):
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
async def test_opted_in_exhausted_with_own_config_auto_migrates_to_user():
    """#48: exhausted free tier + complete own config no longer 402s. The
    free-tier preference is auto-disabled and the request routes to the user's
    own provider, so the configured key is actually used."""
    quota_svc = _mk_quota_svc(prefer_system=True, has_budget=False)
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(_complete_user_cfg()),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=quota_svc,
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() == "user"
    quota_svc.set_preference.assert_awaited_once_with("usr_x", False)


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
async def test_opted_out_with_own_config_routes_user_without_checking_quota():
    quota_svc = _mk_quota_svc(prefer_system=False, has_budget=True)
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(_complete_user_cfg()),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=quota_svc,
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() == "user"
    # Opt-out path must not probe quota — the user pays with their own key.
    quota_svc.check.assert_not_called()


@pytest.mark.asyncio
async def test_opted_out_without_own_config_raises_no_provider_configured():
    """Even if quota has budget, opted-out users must not silently fall
    back to the free tier."""
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(None),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=_mk_quota_svc(prefer_system=False, has_budget=True),
    )
    with pytest.raises(NoProviderConfiguredError) as exc_info:
        await r.resolve_and_set("usr_x")
    assert exc_info.value.user_id == "usr_x"
    assert exc_info.value.error_code == "NO_PROVIDER_CONFIGURED"


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
async def test_opted_out_with_partial_own_config_still_raises():
    cfg = _complete_user_cfg()
    cfg.slots.pop("helper_llm")
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(cfg),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=_mk_quota_svc(prefer_system=False, has_budget=True),
    )
    with pytest.raises(NoProviderConfiguredError):
        await r.resolve_and_set("usr_x")


@pytest.mark.asyncio
async def test_opted_out_with_inactive_provider_still_raises():
    cfg = _complete_user_cfg()
    cfg.providers["p_a"].is_active = False
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(cfg),
        system_provider_svc=_mk_sys(enabled=True, cfg=_system_cfg()),
        quota_svc=_mk_quota_svc(prefer_system=False, has_budget=True),
    )
    with pytest.raises(NoProviderConfiguredError):
        await r.resolve_and_set("usr_x")


# ---------- Exception hierarchy ------------------------------------------

def test_exception_hierarchy_shares_base():
    assert issubclass(QuotaExceededError, ProviderResolverError)
    assert issubclass(FreeTierExhaustedError, ProviderResolverError)
    assert issubclass(NoProviderConfiguredError, ProviderResolverError)


def test_error_codes_are_stable_strings():
    """Frontend pattern-matches on these; they're part of the API contract."""
    assert QuotaExceededError("u").error_code == "QUOTA_EXCEEDED_NO_USER_PROVIDER"
    assert FreeTierExhaustedError("u").error_code == "FREE_TIER_EXHAUSTED_DISABLE_TOGGLE"
    assert NoProviderConfiguredError("u").error_code == "NO_PROVIDER_CONFIGURED"
