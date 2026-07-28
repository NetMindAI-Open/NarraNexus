"""
@file_name: test_provider_resolver.py
@author: Bin Liang
@date: 2026-04-23
@description: ProviderResolver decision tree, aligned with business-layer
`get_user_llm_configs` (api_config.py) so users with no usable provider are
blocked at the middleware layer with a clear, actionable error_code.

Decision tree:

  0. not cloud mode
     -> request path (default): strict no-op (local keeps its global config).
     -> background path (own_config_when_system_disabled=True): resolve the
        user's own config anyway; missing -> NoProviderConfiguredError.
  1. cloud + complete own config     -> route "user"
  2. cloud + missing/partial config  -> NoProviderConfiguredError

The free tier used to be a third branch with its own token budget. It is now a
provider card like any other (a wallet on the gateway), so it needs no branch —
and an EMPTY wallet is invisible here, because refusing it is the gateway's job
at call time, not a pre-run gate.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xyz_agent_context.agent_framework.api_config import (
    get_provider_source,
    set_provider_source,
)
from xyz_agent_context.agent_framework.providers.free_tier import FREE_TIER_SOURCE
from xyz_agent_context.agent_framework.providers.resolver import (
    NoProviderConfiguredError,
    ProviderResolver,
    ProviderResolverError,
)
from xyz_agent_context.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    ProviderSource,
    SlotConfig,
)

_CLOUD = "xyz_agent_context.utils.deployment_mode.is_cloud_mode"


# ---------- helpers -------------------------------------------------------

def _complete_user_cfg(source=ProviderSource.USER):
    prov_anth = ProviderConfig(
        provider_id="p_a", name="mine-a", source=source,
        protocol=ProviderProtocol.ANTHROPIC, auth_type=AuthType.API_KEY,
        api_key="sk-user-anth", is_active=True, models=["claude-x"],
    )
    prov_oai = ProviderConfig(
        provider_id="p_o", name="mine-o", source=source,
        protocol=ProviderProtocol.OPENAI, auth_type=AuthType.API_KEY,
        api_key="sk-user-oai", is_active=True, models=["gpt-x"],
    )
    return LLMConfig(
        providers={"p_a": prov_anth, "p_o": prov_oai},
        slots={
            "agent": SlotConfig(provider_id="p_a", model="claude-x"),
            "helper_llm": SlotConfig(provider_id="p_o", model="gpt-x"),
        },
    )


def _partial_user_cfg():
    cfg = _complete_user_cfg()
    del cfg.slots["helper_llm"]
    return cfg


def _mk_user_svc(user_cfg):
    m = MagicMock()
    m.get_user_config = AsyncMock(return_value=user_cfg)
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
    from xyz_agent_context.agent_framework.providers import driver as provider_driver
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


# ---------- Branch 0: local mode -----------------------------------------

@pytest.mark.asyncio
async def test_local_mode_is_strict_noop():
    """The request path must not touch the ContextVars locally — the desktop
    app's global llm_config.json is what runs there."""
    resolver = ProviderResolver(_mk_user_svc(None))
    with patch(_CLOUD, return_value=False):
        await resolver.resolve_and_set("u")
    assert get_provider_source() is None


@pytest.mark.asyncio
async def test_local_mode_falls_through_to_own_config_when_flagged():
    """Background tasks CLEAR the ContextVars first, so a no-op would leave the
    helper config empty and detached hooks would 401 on the platform key."""
    resolver = ProviderResolver(_mk_user_svc(_complete_user_cfg()))
    with patch(_CLOUD, return_value=False):
        await resolver.resolve_and_set("u", own_config_when_system_disabled=True)
    assert get_provider_source() == "user"


@pytest.mark.asyncio
async def test_local_mode_flagged_without_config_raises_catchable_error(monkeypatch):
    """The strict resolver raises LLMConfigNotConfigured — a DIFFERENT family
    from ProviderResolverError. It must be translated, or callers' `except
    ProviderResolverError` misses it and the run continues on the platform key
    (the 2026-07 incident this path exists to prevent)."""
    from xyz_agent_context.agent_framework.api_config import LLMConfigNotConfigured
    from xyz_agent_context.agent_framework.providers import driver as provider_driver

    async def _raise(_user_id, _db, agent_id=None):
        raise LLMConfigNotConfigured("nothing configured")

    monkeypatch.setattr(provider_driver, "resolve_user_runtime_llm_configs", _raise)

    resolver = ProviderResolver(_mk_user_svc(None))
    with patch(_CLOUD, return_value=False):
        with pytest.raises(ProviderResolverError):
            await resolver.resolve_and_set("u", own_config_when_system_disabled=True)


# ---------- Branch 1: cloud, config present ------------------------------

@pytest.mark.asyncio
async def test_cloud_with_own_config_routes_user():
    resolver = ProviderResolver(_mk_user_svc(_complete_user_cfg()))
    with patch(_CLOUD, return_value=True):
        cfgs, source = await resolver.resolve("u")
    assert source == "user"
    assert cfgs is not None


@pytest.mark.asyncio
async def test_free_tier_card_needs_no_special_branch():
    """A user whose only card is the free-tier wallet resolves through exactly
    the same path as a bring-your-own-key user. That equivalence IS the
    feature."""
    resolver = ProviderResolver(_mk_user_svc(_complete_user_cfg(FREE_TIER_SOURCE)))
    with patch(_CLOUD, return_value=True):
        _, source = await resolver.resolve("u")
    assert source == "user"


@pytest.mark.asyncio
async def test_resolve_and_set_tags_the_context_for_cost_attribution():
    resolver = ProviderResolver(_mk_user_svc(_complete_user_cfg()))
    with patch(_CLOUD, return_value=True):
        await resolver.resolve_and_set("u")
    assert get_provider_source() == "user"


@pytest.mark.asyncio
async def test_agent_id_is_threaded_to_the_single_point_builder(monkeypatch):
    """Per-agent slot overrides must reach the builder — on the free tier too,
    now that nothing preempts them."""
    seen = {}
    from xyz_agent_context.agent_framework.providers import driver as provider_driver
    from xyz_agent_context.agent_framework.api_config import (
        ClaudeConfig, OpenAIConfig, RuntimeLLMConfigs,
    )

    async def _capture(user_id, _db, agent_id=None):
        seen["agent_id"] = agent_id
        return RuntimeLLMConfigs(claude=ClaudeConfig(), openai=OpenAIConfig())

    monkeypatch.setattr(provider_driver, "resolve_user_runtime_llm_configs", _capture)

    resolver = ProviderResolver(_mk_user_svc(_complete_user_cfg()))
    with patch(_CLOUD, return_value=True):
        await resolver.resolve("u", agent_id="ag_1")
    assert seen["agent_id"] == "ag_1"


# ---------- Branch 2: cloud, config missing ------------------------------

@pytest.mark.asyncio
async def test_cloud_without_any_config_raises():
    resolver = ProviderResolver(_mk_user_svc(None))
    with patch(_CLOUD, return_value=True):
        with pytest.raises(NoProviderConfiguredError):
            await resolver.resolve("u")


@pytest.mark.asyncio
async def test_cloud_with_partial_config_raises():
    """A half-configured user is NOT silently topped up from a platform key."""
    resolver = ProviderResolver(_mk_user_svc(_partial_user_cfg()))
    with patch(_CLOUD, return_value=True):
        with pytest.raises(NoProviderConfiguredError):
            await resolver.resolve("u")


@pytest.mark.asyncio
async def test_cloud_with_inactive_provider_raises():
    cfg = _complete_user_cfg()
    cfg.providers["p_a"].is_active = False
    resolver = ProviderResolver(_mk_user_svc(cfg))
    with patch(_CLOUD, return_value=True):
        with pytest.raises(NoProviderConfiguredError):
            await resolver.resolve("u")


# ---------- error contract ------------------------------------------------

def test_exception_hierarchy_shares_base():
    assert issubclass(NoProviderConfiguredError, ProviderResolverError)


def test_error_code_is_a_stable_string():
    """auth_middleware returns this verbatim and the frontend switches on it."""
    assert NoProviderConfiguredError("u").error_code == "NO_PROVIDER_CONFIGURED"


def test_error_message_keeps_the_job_pause_marker():
    """job_trigger's message-substring layer matches on this phrase so a
    background job PAUSES instead of retry-storming (see test_no_quota_pause)."""
    assert "no provider configured" in str(NoProviderConfiguredError("u")).lower()
