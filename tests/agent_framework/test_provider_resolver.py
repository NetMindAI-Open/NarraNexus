"""
@file_name: test_provider_resolver.py
@author: Bin Liang
@date: 2026-04-16
@description: ProviderResolver four-branch decision tree.

Branches:
  A. Feature disabled (local mode / env not set) -> strict no-op
  B. User has complete own config -> route "user", quota untouched
  C. User incomplete + system enabled + has budget -> route "system"
  D. User incomplete + system enabled + no budget -> QuotaExceededError
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.agent_framework.api_config import (
    get_provider_source,
    set_provider_source,
)
from xyz_agent_context.agent_framework.provider_resolver import (
    ProviderResolver,
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


def _complete_user_cfg():
    prov_anthropic = ProviderConfig(
        provider_id="p_a",
        name="mine-a",
        source=ProviderSource.USER,
        protocol=ProviderProtocol.ANTHROPIC,
        auth_type=AuthType.API_KEY,
        api_key="sk-user-anth",
        is_active=True,
        models=["claude-x"],
    )
    prov_openai = ProviderConfig(
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
        providers={"p_a": prov_anthropic, "p_o": prov_openai},
        slots={
            "agent": SlotConfig(provider_id="p_a", model="claude-x"),
            "embedding": SlotConfig(provider_id="p_o", model="emb-x"),
            "helper_llm": SlotConfig(provider_id="p_o", model="gpt-x"),
        },
    )


def _system_cfg_from(service_class):
    """Use SystemProviderService factory logic to get a valid system LLMConfig."""
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


def _mk_quota_svc(has_budget: bool):
    m = MagicMock()
    m.check = AsyncMock(return_value=has_budget)
    return m


@pytest.fixture(autouse=True)
def _reset_context():
    set_provider_source(None)
    yield
    set_provider_source(None)


@pytest.mark.asyncio
async def test_branch_A_disabled_is_strict_noop():
    user_svc = _mk_user_svc(None)
    r = ProviderResolver(
        user_provider_svc=user_svc,
        system_provider_svc=_mk_sys(enabled=False),
        quota_svc=_mk_quota_svc(True),
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() is None
    # Must NOT have touched user_provider_svc at all.
    user_svc.get_user_config.assert_not_called()


@pytest.mark.asyncio
async def test_branch_B_user_complete_config_routes_user():
    user_svc = _mk_user_svc(_complete_user_cfg())
    quota_svc = _mk_quota_svc(True)
    r = ProviderResolver(
        user_provider_svc=user_svc,
        system_provider_svc=_mk_sys(enabled=True),
        quota_svc=quota_svc,
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() == "user"
    # Quota must NOT be checked when user has own config.
    quota_svc.check.assert_not_called()


@pytest.mark.asyncio
async def test_branch_C_no_user_cfg_with_budget_routes_system():
    sys_cfg = _system_cfg_from(None)
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(None),
        system_provider_svc=_mk_sys(enabled=True, cfg=sys_cfg),
        quota_svc=_mk_quota_svc(True),
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() == "system"


@pytest.mark.asyncio
async def test_branch_D_no_user_cfg_no_budget_raises():
    sys_cfg = _system_cfg_from(None)
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(None),
        system_provider_svc=_mk_sys(enabled=True, cfg=sys_cfg),
        quota_svc=_mk_quota_svc(False),
    )
    with pytest.raises(QuotaExceededError):
        await r.resolve_and_set("usr_x")
    assert get_provider_source() is None


@pytest.mark.asyncio
async def test_partial_user_cfg_falls_through_to_system():
    """Any one slot missing or any one referenced provider inactive -> treat as incomplete."""
    # Take a complete cfg and drop the helper_llm slot
    cfg = _complete_user_cfg()
    cfg.slots.pop("helper_llm")
    sys_cfg = _system_cfg_from(None)
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(cfg),
        system_provider_svc=_mk_sys(enabled=True, cfg=sys_cfg),
        quota_svc=_mk_quota_svc(True),
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() == "system"


@pytest.mark.asyncio
async def test_inactive_provider_treated_as_incomplete():
    cfg = _complete_user_cfg()
    cfg.providers["p_a"].is_active = False
    sys_cfg = _system_cfg_from(None)
    r = ProviderResolver(
        user_provider_svc=_mk_user_svc(cfg),
        system_provider_svc=_mk_sys(enabled=True, cfg=sys_cfg),
        quota_svc=_mk_quota_svc(True),
    )
    await r.resolve_and_set("usr_x")
    assert get_provider_source() == "system"
