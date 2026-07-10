"""
@file_name: test_llm_config_fallback.py
@author: Bin Liang
@date: 2026-04-20
@description: System-default (free-tier) branch of the agent-run config
resolver, exercised end-to-end through `get_user_runtime_llm_configs`.

History: this used to test a private `_use_system_default_strict` helper. The
#48 convergence removed that divergent second decision tree — the agent-run
path now delegates to the single `ProviderResolver`. These tests therefore
drive the public entry point with the real resolver (only the outer wiring —
db factory, user-provider service, quota service — is stubbed) and pin:

  - opted in + budget → system config returned, ContextVars tagged "system"
  - opted in + exhausted + no own provider → SystemDefaultUnavailable
  - free tier disabled → NO hard error; falls through to the own-config path
    (behavior change from the old strict helper, which raised here)

Broader decision-tree tests live in `test_provider_resolver.py`; the #48
auto-switch + notice specifics live in `test_free_tier_auto_switch.py`.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

import xyz_agent_context.agent_framework.api_config as api_config
from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    OpenAIConfig,
    RuntimeLLMConfigs,
    SystemDefaultUnavailable,
    get_current_user_id,
    get_provider_source,
    get_user_runtime_llm_configs,
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


def _mk_quota_svc(*, prefer_system: bool, has_budget: bool) -> MagicMock:
    svc = MagicMock()
    quota_row = MagicMock()
    quota_row.prefer_system_override = prefer_system
    svc.get = AsyncMock(return_value=quota_row)
    svc.check = AsyncMock(return_value=has_budget)
    svc.disable_preference_if_enabled = AsyncMock(return_value=True)
    return svc


def _wire(monkeypatch, quota_svc, *, user_cfg=None):
    """Stub the outer wiring get_user_runtime_llm_configs constructs, leaving
    the real ProviderResolver decision tree in play. SystemProviderService is
    stubbed via its singleton (_stub_sys)."""
    from xyz_agent_context.utils import db_factory
    from xyz_agent_context.agent_framework import user_provider_service

    monkeypatch.setattr(api_config, "_ensure_quota_service",
                        AsyncMock(return_value=quota_svc))
    monkeypatch.setattr(db_factory, "get_db_client",
                        AsyncMock(return_value=MagicMock()))

    user_svc = MagicMock()
    user_svc.get_user_config = AsyncMock(return_value=user_cfg)
    monkeypatch.setattr(user_provider_service, "UserProviderService",
                        lambda _db: user_svc)


@pytest.mark.asyncio
async def test_system_disabled_falls_through_to_own_config(monkeypatch):
    """Behavior change (#48 convergence): a disabled free tier is no longer a
    hard error for an opted-in user — resolve() returns None (SYSTEM_DISABLED)
    and we fall through to the strict own-config path."""
    _stub_sys(enabled=False)
    _wire(monkeypatch, _mk_quota_svc(prefer_system=True, has_budget=True))
    sentinel = RuntimeLLMConfigs(claude=ClaudeConfig(), openai=OpenAIConfig())
    monkeypatch.setattr(api_config, "_get_user_runtime_llm_configs_strict",
                        AsyncMock(return_value=sentinel))

    cfg = await get_user_runtime_llm_configs("usr_x")
    assert cfg is sentinel  # fell through, did not raise


@pytest.mark.asyncio
async def test_raises_when_quota_exhausted_and_no_own_provider(monkeypatch):
    _stub_sys(enabled=True, cfg=_valid_system_cfg())
    _wire(monkeypatch, _mk_quota_svc(prefer_system=True, has_budget=False),
          user_cfg=None)
    with pytest.raises(SystemDefaultUnavailable, match="quota"):
        await get_user_runtime_llm_configs("usr_x")


@pytest.mark.asyncio
async def test_success_sets_context_vars_and_returns_dataclasses(monkeypatch):
    _stub_sys(enabled=True, cfg=_valid_system_cfg())
    _wire(monkeypatch, _mk_quota_svc(prefer_system=True, has_budget=True))

    cfg = await get_user_runtime_llm_configs("usr_y")

    assert cfg.claude.api_key == "sk-system"
    assert cfg.claude.model == "claude-sonnet-4-5"
    assert cfg.openai.api_key == "sk-system"
    assert cfg.openai.model == "gpt-sys"

    # ContextVars tagged so cost_tracker's post-call hook deducts to the
    # right user's quota.
    assert get_provider_source() == "system"
    assert get_current_user_id() == "usr_y"
