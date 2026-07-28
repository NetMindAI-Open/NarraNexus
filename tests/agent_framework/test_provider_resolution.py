"""
@file_name: test_provider_resolution.py
@author: Bin Liang
@date: 2026-04-20
@description: The agent-run config resolver, end-to-end through
``get_user_runtime_llm_configs``.

This is the entry point every run path shares (HTTP, job trigger, bus trigger,
MCP runner). What it must guarantee:

  - cloud + a usable provider  → configs returned, ContextVars tagged for cost
    attribution;
  - cloud + nothing usable     → ``LLMConfigNotConfigured``, never a silent
    fallback to the platform's own key (the liability guard);
  - local mode                 → the strict own-config path, unchanged.

The free tier is NOT a branch here: it is a provider card, so a free-tier user
travels the same line as a bring-your-own-key user. That is exactly what
``test_free_tier_user_travels_the_ordinary_path`` pins.

Broader decision-tree tests live in ``test_provider_resolver.py``.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    LLMConfigNotConfigured,
    LLMResolverError,
    OpenAIConfig,
    RuntimeLLMConfigs,
    get_current_user_id,
    get_provider_source,
    get_user_runtime_llm_configs,
    set_current_user_id,
    set_provider_source,
)
from xyz_agent_context.agent_framework.providers.free_tier import FREE_TIER_SOURCE
from xyz_agent_context.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    ProviderSource,
    SlotConfig,
)

_CLOUD = "xyz_agent_context.utils.deployment_mode.is_cloud_mode"


def _cfg(source=ProviderSource.USER, *, key="sk-own"):
    prov_a = ProviderConfig(
        provider_id="p_a", name="a", source=source,
        protocol=ProviderProtocol.ANTHROPIC, auth_type=AuthType.API_KEY,
        api_key=key, is_active=True, models=["claude-x"],
    )
    prov_o = ProviderConfig(
        provider_id="p_o", name="o", source=source,
        protocol=ProviderProtocol.OPENAI, auth_type=AuthType.API_KEY,
        api_key=key, is_active=True, models=["gpt-x"],
    )
    return LLMConfig(
        providers={"p_a": prov_a, "p_o": prov_o},
        slots={
            "agent": SlotConfig(provider_id="p_a", model="claude-x"),
            "helper_llm": SlotConfig(provider_id="p_o", model="gpt-x"),
        },
    )


@pytest.fixture(autouse=True)
def _reset_context():
    set_provider_source(None)
    set_current_user_id(None)
    yield
    set_provider_source(None)
    set_current_user_id(None)


@pytest.fixture
def wiring(monkeypatch):
    """Stub the outer wiring (db factory + user provider service + the
    single-point config builder) and let the REAL resolver decide."""
    from xyz_agent_context.agent_framework.providers import driver as driver_mod
    from xyz_agent_context.agent_framework.providers import user_service as us_mod
    from xyz_agent_context.utils.db import db_factory

    state = {"cfg": _cfg(), "built": None, "agent_id": "unset"}

    monkeypatch.setattr(
        db_factory, "get_db_client", AsyncMock(return_value=MagicMock())
    )

    def _svc(_db):
        m = MagicMock()
        m.db = _db
        m.get_user_config = AsyncMock(side_effect=lambda _uid: state["cfg"])
        return m

    monkeypatch.setattr(us_mod, "UserProviderService", _svc)

    async def _build(_uid, _db, agent_id=None):
        state["agent_id"] = agent_id
        state["built"] = RuntimeLLMConfigs(
            claude=ClaudeConfig(api_key="built-claude"),
            openai=OpenAIConfig(api_key="built-openai"),
        )
        return state["built"]

    monkeypatch.setattr(driver_mod, "resolve_user_runtime_llm_configs", _build)
    return state


@pytest.mark.asyncio
async def test_cloud_with_own_provider_returns_configs_and_tags_context(wiring):
    with patch(_CLOUD, return_value=True):
        cfgs = await get_user_runtime_llm_configs("u1")

    assert cfgs.claude.api_key == "built-claude"
    # cost_tracker reads both to attribute the row.
    assert get_provider_source() == "user"
    assert get_current_user_id() == "u1"


@pytest.mark.asyncio
async def test_free_tier_user_travels_the_ordinary_path(wiring):
    """The wallet card resolves exactly like a bring-your-own-key card — no
    separate branch, no fixed model, no preempted overrides."""
    wiring["cfg"] = _cfg(FREE_TIER_SOURCE, key="sk-wallet")
    with patch(_CLOUD, return_value=True):
        cfgs = await get_user_runtime_llm_configs("u1", agent_id="ag_9")

    assert cfgs.claude.api_key == "built-claude"
    assert get_provider_source() == "user"
    # The per-agent override is threaded through even on the free tier.
    assert wiring["agent_id"] == "ag_9"


@pytest.mark.asyncio
async def test_cloud_without_a_usable_provider_raises_never_falls_back(wiring):
    """No implicit platform-funded fallback: a user with nothing configured
    must fail loudly rather than quietly spend the operator's key."""
    wiring["cfg"] = None
    with patch(_CLOUD, return_value=True):
        with pytest.raises(LLMConfigNotConfigured):
            await get_user_runtime_llm_configs("u1")


@pytest.mark.asyncio
async def test_resolver_errors_stay_in_the_llm_resolver_family(wiring):
    """job_trigger / lark_trigger catch LLMResolverError; a resolver-family
    escape would slip past them into a generic except."""
    wiring["cfg"] = None
    with patch(_CLOUD, return_value=True):
        with pytest.raises(LLMResolverError):
            await get_user_runtime_llm_configs("u1")


@pytest.mark.asyncio
async def test_local_mode_delegates_to_the_strict_own_config_path(monkeypatch, wiring):
    from xyz_agent_context.agent_framework import api_config as api_mod

    sentinel = RuntimeLLMConfigs(
        claude=ClaudeConfig(api_key="strict"), openai=OpenAIConfig()
    )
    monkeypatch.setattr(
        api_mod,
        "_get_user_runtime_llm_configs_strict",
        AsyncMock(return_value=sentinel),
    )
    with patch(_CLOUD, return_value=False):
        cfgs = await get_user_runtime_llm_configs("u1")

    assert cfgs.claude.api_key == "strict"
