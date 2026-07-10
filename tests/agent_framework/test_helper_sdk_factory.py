"""
@file_name: test_helper_sdk_factory.py
@author: NarraNexus
@date: 2026-06-17
@description: Helper-SDK factory + single-resolver priming regression tests.

Covers the defect family fixed on 2026-06-17:
  * get_helper_sdk is a registry keyed on the resolved helper protocol.
  * clear_user_config resets ALL FOUR config ContextVars (no cross-tenant
    codex/anthropic_helper leak — LATENT-3).
  * ProviderResolver.resolve_and_set (the HTTP request + memory-consolidation
    worker priming path) wires anthropic_helper so an anthropic-protocol
    helper routes to AnthropicHelperSDK — the original consolidation bug.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.agent_framework import provider_driver
from xyz_agent_context.agent_framework.api_config import (
    AnthropicHelperConfig,
    ClaudeConfig,
    CodexConfig,
    OpenAIConfig,
    RuntimeLLMConfigs,
    _anthropic_helper_ctx,
    _claude_ctx,
    _codex_ctx,
    _openai_ctx,
    clear_user_config,
    set_user_config,
)
from xyz_agent_context.agent_framework.helper_sdk import get_helper_sdk
from xyz_agent_context.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    ProviderSource,
    SlotConfig,
)


@pytest.fixture(autouse=True)
def _clean_ctx():
    clear_user_config()
    yield
    clear_user_config()


def _complete_cfg() -> LLMConfig:
    return LLMConfig(
        providers={
            "p_a": ProviderConfig(
                provider_id="p_a", name="a", source=ProviderSource.USER,
                protocol=ProviderProtocol.ANTHROPIC, auth_type=AuthType.API_KEY,
                api_key="k", is_active=True, models=["claude-x"],
            ),
            "p_h": ProviderConfig(
                provider_id="p_h", name="h", source=ProviderSource.USER,
                protocol=ProviderProtocol.ANTHROPIC, auth_type=AuthType.API_KEY,
                api_key="k", is_active=True, models=["claude-haiku-4-5"],
            ),
        },
        slots={
            "agent": SlotConfig(provider_id="p_a", model="claude-x"),
            "helper_llm": SlotConfig(provider_id="p_h", model="claude-haiku-4-5"),
        },
    )


# ---------------- factory dispatch ----------------------------------------


def test_factory_returns_anthropic_when_helper_config_set():
    set_user_config(
        ClaudeConfig(), OpenAIConfig(), CodexConfig(),
        AnthropicHelperConfig(api_key="sk-ant", model="claude-haiku-4-5"),
    )
    from xyz_agent_context.agent_framework.anthropic_helper_sdk import (
        AnthropicHelperSDK,
    )
    assert isinstance(get_helper_sdk(), AnthropicHelperSDK)


def test_factory_returns_openai_by_default():
    set_user_config(ClaudeConfig(), OpenAIConfig(api_key="sk", model="gpt-x"))
    from xyz_agent_context.agent_framework.openai_agents_sdk import OpenAIAgentsSDK
    assert isinstance(get_helper_sdk(), OpenAIAgentsSDK)


# ---------------- LATENT-3: clear resets all four ctxvars ------------------


def test_clear_user_config_resets_all_four_ctxvars():
    set_user_config(
        ClaudeConfig(api_key="A"), OpenAIConfig(api_key="A"),
        CodexConfig(api_key="A"),
        AnthropicHelperConfig(api_key="A", model="claude-haiku-4-5"),
    )
    clear_user_config()
    assert _claude_ctx.get() is None
    assert _openai_ctx.get() is None
    assert _codex_ctx.get() == CodexConfig()       # reset to empty default
    assert _anthropic_helper_ctx.get() is None     # NOT the previous tenant's


# ---------------- the original bug: priming wires anthropic_helper ---------


@pytest.mark.asyncio
async def test_resolve_and_set_wires_anthropic_helper(monkeypatch):
    """The consolidation/request priming path (ProviderResolver.resolve_and_set)
    must route an anthropic-protocol helper to AnthropicHelperSDK. Before the
    fix it dropped anthropic_helper (2-arg set_user_config) → OpenAIAgentsSDK
    against anthropic creds."""
    from xyz_agent_context.agent_framework.provider_resolver import ProviderResolver
    from xyz_agent_context.agent_framework.anthropic_helper_sdk import (
        AnthropicHelperSDK,
    )

    async def _fake_resolve(_uid, _db, agent_id=None):
        return RuntimeLLMConfigs(
            claude=ClaudeConfig(),
            openai=OpenAIConfig(),
            anthropic_helper=AnthropicHelperConfig(
                api_key="sk-ant", model="claude-haiku-4-5"
            ),
        )

    monkeypatch.setattr(
        provider_driver, "resolve_user_runtime_llm_configs", _fake_resolve
    )

    user_svc = MagicMock()
    user_svc.get_user_config = AsyncMock(return_value=_complete_cfg())
    sys_svc = MagicMock()
    sys_svc.is_enabled.return_value = True
    quota_svc = MagicMock()
    quota_svc.get = AsyncMock(return_value=None)   # no quota row → opt-out → USER
    quota_svc.check = AsyncMock(return_value=True)

    await ProviderResolver(user_svc, sys_svc, quota_svc).resolve_and_set("u")

    assert isinstance(get_helper_sdk(), AnthropicHelperSDK)
    assert _anthropic_helper_ctx.get() is not None
