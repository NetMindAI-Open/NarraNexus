"""
@file_name: test_llm_config_fallback.py
@author: Bin Liang
@date: 2026-04-20
@description: Unit tests for the strict system-default branch of the
provider resolver (`_use_system_default_strict`).

The old `_try_system_default_fallback` returned ``None`` silently when the
feature was off or the quota was exhausted. That let callers chain a
"then fall back to the user's own provider" — i.e. the system-default
free tier was an opt-out safety net.

The new contract (Bug 2 design) is explicit: when a user opts in to the
system free tier we strictly use it or raise `SystemDefaultUnavailable`.
No silent fallback in either direction. These tests pin that contract.

Broader decision-tree tests live in `test_provider_resolution.py`.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.agent_framework.api_config import (
    SystemDefaultUnavailable,
    _use_system_default_strict,
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
    SystemProviderService._instance = SystemProviderService(
        enabled=enabled, config=cfg
    )


def _stub_quota(has_budget: bool) -> MagicMock:
    svc = MagicMock()
    svc.check = AsyncMock(return_value=has_budget)
    QuotaService.set_default(svc)
    return svc


@pytest.mark.asyncio
async def test_raises_when_system_disabled():
    _stub_sys(enabled=False)
    svc = _stub_quota(True)
    with pytest.raises(SystemDefaultUnavailable, match="(disabled|administrator)"):
        await _use_system_default_strict("usr_x", svc)


@pytest.mark.asyncio
async def test_raises_when_quota_exhausted():
    _stub_sys(enabled=True, cfg=_valid_system_cfg())
    svc = _stub_quota(has_budget=False)
    with pytest.raises(SystemDefaultUnavailable, match="quota"):
        await _use_system_default_strict("usr_x", svc)


@pytest.mark.asyncio
async def test_success_sets_context_vars_and_returns_dataclasses():
    _stub_sys(enabled=True, cfg=_valid_system_cfg())
    svc = _stub_quota(has_budget=True)
    claude, openai_cfg, embedding = await _use_system_default_strict("usr_y", svc)

    assert claude.api_key == "sk-system"
    assert claude.model == "claude-sonnet-4-5"
    assert openai_cfg.api_key == "sk-system"
    assert openai_cfg.model == "gpt-sys"
    assert embedding.api_key == "sk-system"
    assert embedding.model == "emb-sys"

    # ContextVars tagged so cost_tracker's post-call hook deducts to the
    # right user's quota.
    assert get_provider_source() == "system"
    assert get_current_user_id() == "usr_y"
