"""
@file_name: test_llm_config_fallback.py
@author: Bin Liang
@date: 2026-04-16
@description: `get_user_llm_configs` system-default fallback.

When a user has no complete provider config of their own, the function
should fall back to the system-default free-tier config if the feature
is enabled AND the user has budget. This enables:
  - HTTP-path quota (auth_middleware's ContextVar gets overwritten by
    AgentRuntime calling get_agent_owner_llm_configs; the fallback is
    the real quota injection point)
  - Background-trigger quota (jobs/bus/lark never hit auth_middleware;
    the fallback is their ONLY quota injection point)
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.agent_framework.api_config import (
    LLMConfigNotConfigured,
    _try_system_default_fallback,
    get_current_user_id,
    get_provider_source,
    set_current_user_id,
    set_provider_source,
)
from xyz_agent_context.agent_framework.quota_service import QuotaService
from xyz_agent_context.agent_framework.system_provider_service import (
    SystemProviderService,
)
from xyz_agent_context.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    ProviderSource,
    SlotConfig,
)


def _valid_system_cfg() -> LLMConfig:
    return LLMConfig(
        providers={
            "system_default_anthropic": ProviderConfig(
                provider_id="system_default_anthropic",
                name="sys-a",
                source=ProviderSource.NETMIND,
                protocol=ProviderProtocol.ANTHROPIC,
                auth_type=AuthType.BEARER_TOKEN,
                api_key="sk-system",
                is_active=True,
                models=["claude-sonnet-4-5"],
            ),
            "system_default_openai": ProviderConfig(
                provider_id="system_default_openai",
                name="sys-o",
                source=ProviderSource.NETMIND,
                protocol=ProviderProtocol.OPENAI,
                auth_type=AuthType.API_KEY,
                api_key="sk-system",
                is_active=True,
                models=["emb-sys", "gpt-sys"],
            ),
        },
        slots={
            "agent": SlotConfig(provider_id="system_default_anthropic", model="claude-sonnet-4-5"),
            "embedding": SlotConfig(provider_id="system_default_openai", model="emb-sys"),
            "helper_llm": SlotConfig(provider_id="system_default_openai", model="gpt-sys"),
        },
    )


@pytest.fixture(autouse=True)
def _reset_state():
    SystemProviderService._instance = None
    QuotaService._default = None
    set_provider_source(None)
    set_current_user_id(None)
    yield
    SystemProviderService._instance = None
    QuotaService._default = None
    set_provider_source(None)
    set_current_user_id(None)


def _stub_sys(enabled: bool, cfg: LLMConfig | None = None):
    """Force-seed the SystemProviderService singleton with a stub."""
    SystemProviderService._instance = SystemProviderService(
        enabled=enabled, config=cfg
    )


def _stub_quota(has_budget: bool):
    svc = MagicMock()
    svc.check = AsyncMock(return_value=has_budget)
    QuotaService.set_default(svc)


@pytest.mark.asyncio
async def test_fallback_returns_none_when_feature_disabled():
    _stub_sys(enabled=False)
    _stub_quota(True)
    assert await _try_system_default_fallback("usr_x") is None


@pytest.mark.asyncio
async def test_fallback_returns_none_when_quota_service_not_initialised():
    _stub_sys(enabled=True, cfg=_valid_system_cfg())
    # deliberately DO NOT call _stub_quota → QuotaService.default() raises
    assert await _try_system_default_fallback("usr_x") is None


@pytest.mark.asyncio
async def test_fallback_returns_none_when_no_budget():
    _stub_sys(enabled=True, cfg=_valid_system_cfg())
    _stub_quota(has_budget=False)
    assert await _try_system_default_fallback("usr_x") is None


@pytest.mark.asyncio
async def test_fallback_success_sets_context_vars_and_returns_dataclasses():
    _stub_sys(enabled=True, cfg=_valid_system_cfg())
    _stub_quota(has_budget=True)
    result = await _try_system_default_fallback("usr_y")
    assert result is not None
    claude, openai_cfg, embedding = result
    assert claude.api_key == "sk-system"
    assert claude.model == "claude-sonnet-4-5"
    assert openai_cfg.api_key == "sk-system"
    assert openai_cfg.model == "gpt-sys"
    assert embedding.api_key == "sk-system"
    assert embedding.model == "emb-sys"
    # ContextVars tagged so cost_tracker's post-call hook fires correctly.
    assert get_provider_source() == "system"
    assert get_current_user_id() == "usr_y"
