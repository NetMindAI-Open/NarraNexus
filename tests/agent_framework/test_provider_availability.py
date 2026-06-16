"""
@file_name: test_provider_availability.py
@author: Bin Liang
@date: 2026-06-01
@description: Single source of truth for "can this user resolve a usable
provider right now". `ProviderResolver.classify` returns a verdict enum that
BOTH the HTTP path (`resolve`) and the job resume gate (`_user_can_run`) share,
so they can never disagree again (root cause of the 2026-05-31 pause/resume
oscillation: the resume gate reimplemented the decision tree and drifted).

Decision tree (identical to `resolve`, just verdict-only):

  0. system disabled                                  -> SYSTEM_DISABLED (not gated)
  1. prefer_system_override=True:
     1a. has budget                                   -> SYSTEM_OK
     1b. no budget + complete own config              -> auto-disable the
         free-tier preference and route USER_OK (#48)
     1c. no budget + no own provider                  -> QUOTA_EXCEEDED
  2. prefer_system_override=False (or no quota row):
     2a. complete own config                          -> USER_OK
     2b. no own provider                              -> NO_PROVIDER

`is_runnable(verdict)` is True only for {SYSTEM_OK, USER_OK, SYSTEM_DISABLED}.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.agent_framework.provider_resolver import (
    ProviderResolver,
    ProviderAvailability,
    is_runnable,
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
    prov_a = ProviderConfig(
        provider_id="p_a", name="mine-a", source=ProviderSource.USER,
        protocol=ProviderProtocol.ANTHROPIC, auth_type=AuthType.API_KEY,
        api_key="sk-a", is_active=True, models=["claude-x"],
    )
    prov_o = ProviderConfig(
        provider_id="p_o", name="mine-o", source=ProviderSource.USER,
        protocol=ProviderProtocol.OPENAI, auth_type=AuthType.API_KEY,
        api_key="sk-o", is_active=True, models=["gpt-x", "emb-x"],
    )
    return LLMConfig(
        providers={"p_a": prov_a, "p_o": prov_o},
        slots={
            "agent": SlotConfig(provider_id="p_a", model="claude-x"),
            "embedding": SlotConfig(provider_id="p_o", model="emb-x"),
            "helper_llm": SlotConfig(provider_id="p_o", model="gpt-x"),
        },
    )


def _mk_sys(enabled: bool):
    m = MagicMock()
    m.is_enabled.return_value = enabled
    return m


def _mk_user_svc(user_cfg):
    m = MagicMock()
    m.get_user_config = AsyncMock(return_value=user_cfg)
    return m


def _mk_quota_svc(*, prefer_system, has_budget):
    m = MagicMock()
    if prefer_system is None:
        m.get = AsyncMock(return_value=None)
    else:
        row = MagicMock()
        row.prefer_system_override = prefer_system
        m.get = AsyncMock(return_value=row)
    m.check = AsyncMock(return_value=has_budget)
    # classify() auto-disables the free-tier preference on exhaustion (#48).
    m.set_preference = AsyncMock()
    return m


def _resolver(user_cfg, *, enabled, prefer_system, has_budget):
    return ProviderResolver(
        user_provider_svc=_mk_user_svc(user_cfg),
        system_provider_svc=_mk_sys(enabled),
        quota_svc=_mk_quota_svc(prefer_system=prefer_system, has_budget=has_budget),
    )


# ── classify decision matrix ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_is_passthrough_and_lazy():
    """System off → SYSTEM_DISABLED, and must not touch quota / user services
    (preserves the existing strict-no-op laziness)."""
    user_svc = _mk_user_svc(None)
    quota_svc = _mk_quota_svc(prefer_system=True, has_budget=True)
    r = ProviderResolver(user_svc, _mk_sys(False), quota_svc)
    assert await r.classify("u") == ProviderAvailability.SYSTEM_DISABLED
    user_svc.get_user_config.assert_not_called()
    quota_svc.get.assert_not_called()
    quota_svc.check.assert_not_called()


@pytest.mark.asyncio
async def test_opted_in_with_budget_is_system_ok_even_with_own_config():
    r = _resolver(_complete_user_cfg(), enabled=True, prefer_system=True, has_budget=True)
    assert await r.classify("u") == ProviderAvailability.SYSTEM_OK


@pytest.mark.asyncio
async def test_opted_in_exhausted_with_own_config_auto_migrates_to_user_ok():
    """#48: pref=1 + exhausted + own provider. Instead of dead-ending on the
    exhausted free tier, the free-tier preference is auto-disabled and the user
    routes to their own key — so the verdict is USER_OK and IS runnable."""
    r = _resolver(_complete_user_cfg(), enabled=True, prefer_system=True, has_budget=False)
    verdict = await r.classify("u")
    assert verdict == ProviderAvailability.USER_OK
    assert is_runnable(verdict) is True
    # the preference flip was persisted (toggle visibly unchecks)
    r.quota_svc.set_preference.assert_awaited_once_with("u", False)


@pytest.mark.asyncio
async def test_opted_in_exhausted_without_own_provider_is_quota_exceeded():
    r = _resolver(None, enabled=True, prefer_system=True, has_budget=False)
    assert await r.classify("u") == ProviderAvailability.QUOTA_EXCEEDED


@pytest.mark.asyncio
async def test_opted_out_with_own_config_is_user_ok_without_checking_quota():
    r = _resolver(_complete_user_cfg(), enabled=True, prefer_system=False, has_budget=True)
    assert await r.classify("u") == ProviderAvailability.USER_OK
    r.quota_svc.check.assert_not_called()


@pytest.mark.asyncio
async def test_opted_out_without_own_config_is_no_provider():
    r = _resolver(None, enabled=True, prefer_system=False, has_budget=True)
    assert await r.classify("u") == ProviderAvailability.NO_PROVIDER


@pytest.mark.asyncio
async def test_no_quota_row_behaves_as_opted_out():
    r = _resolver(_complete_user_cfg(), enabled=True, prefer_system=None, has_budget=True)
    assert await r.classify("u") == ProviderAvailability.USER_OK


# ── is_runnable helper ──────────────────────────────────────────────────────

def test_is_runnable_truth_table():
    assert is_runnable(ProviderAvailability.SYSTEM_OK) is True
    assert is_runnable(ProviderAvailability.USER_OK) is True
    assert is_runnable(ProviderAvailability.SYSTEM_DISABLED) is True
    assert is_runnable(ProviderAvailability.FREE_TIER_EXHAUSTED) is False
    assert is_runnable(ProviderAvailability.QUOTA_EXCEEDED) is False
    assert is_runnable(ProviderAvailability.NO_PROVIDER) is False
