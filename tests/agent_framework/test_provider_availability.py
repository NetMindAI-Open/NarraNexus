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

  0. not cloud mode                    -> LOCAL_PASSTHROUGH (not gated)
  1. cloud + complete own config       -> USER_OK
  2. cloud + config missing/incomplete -> NO_PROVIDER

The free tier used to be a third branch here with its own token budget. It is
now an ordinary provider card (a wallet on the gateway), so a free-tier user
lands in USER_OK like everyone else, and an EMPTY wallet is not visible to this
classifier at all — the gateway refuses the call at runtime and
`llm/failure.py` classifies it. Guarding that here is the point of
`test_exhausted_wallet_is_not_a_classify_concern`.

`is_runnable(verdict)` is True only for {USER_OK, LOCAL_PASSTHROUGH}.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xyz_agent_context.agent_framework.providers.free_tier import FREE_TIER_SOURCE
from xyz_agent_context.agent_framework.providers.resolver import (
    ProviderAvailability,
    ProviderResolver,
    is_runnable,
    is_user_config_complete,
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


def _cfg(*, source=ProviderSource.USER, active=True):
    prov_a = ProviderConfig(
        provider_id="p_a", name="mine-a", source=source,
        protocol=ProviderProtocol.ANTHROPIC, auth_type=AuthType.API_KEY,
        api_key="sk-a", is_active=active, models=["claude-x"],
    )
    prov_o = ProviderConfig(
        provider_id="p_o", name="mine-o", source=source,
        protocol=ProviderProtocol.OPENAI, auth_type=AuthType.API_KEY,
        api_key="sk-o", is_active=active, models=["gpt-x"],
    )
    return LLMConfig(
        providers={"p_a": prov_a, "p_o": prov_o},
        slots={
            "agent": SlotConfig(provider_id="p_a", model="claude-x"),
            "helper_llm": SlotConfig(provider_id="p_o", model="gpt-x"),
        },
    )


def _resolver(user_cfg):
    svc = MagicMock()
    svc.get_user_config = AsyncMock(return_value=user_cfg)
    return ProviderResolver(svc), svc


# ── classify decision matrix ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_local_mode_is_passthrough_and_lazy():
    """Local → LOCAL_PASSTHROUGH without even reading the user's providers
    (there are none per-user locally; the global config applies)."""
    resolver, svc = _resolver(_cfg())
    with patch(_CLOUD, return_value=False):
        assert await resolver.classify("u") == ProviderAvailability.LOCAL_PASSTHROUGH
    svc.get_user_config.assert_not_called()


@pytest.mark.asyncio
async def test_cloud_with_complete_config_is_user_ok():
    resolver, _ = _resolver(_cfg())
    with patch(_CLOUD, return_value=True):
        assert await resolver.classify("u") == ProviderAvailability.USER_OK


@pytest.mark.asyncio
async def test_free_tier_card_is_just_a_provider():
    """A user whose only card is the free-tier wallet is USER_OK — the whole
    point of the redesign is that this needs no special branch."""
    resolver, _ = _resolver(_cfg(source=FREE_TIER_SOURCE))
    with patch(_CLOUD, return_value=True):
        assert await resolver.classify("u") == ProviderAvailability.USER_OK


@pytest.mark.asyncio
async def test_cloud_without_config_is_no_provider():
    resolver, _ = _resolver(None)
    with patch(_CLOUD, return_value=True):
        assert await resolver.classify("u") == ProviderAvailability.NO_PROVIDER


@pytest.mark.asyncio
async def test_inactive_provider_does_not_count_as_configured():
    resolver, _ = _resolver(_cfg(active=False))
    with patch(_CLOUD, return_value=True):
        assert await resolver.classify("u") == ProviderAvailability.NO_PROVIDER


@pytest.mark.asyncio
async def test_exhausted_wallet_is_not_a_classify_concern():
    """An empty wallet is still a well-formed provider card, so classify says
    USER_OK. Exhaustion surfaces at CALL time (the gateway refuses) and is
    handled by the self-serviceable classifier — deliberately NOT a pre-run
    gate any more."""
    resolver, svc = _resolver(_cfg(source=FREE_TIER_SOURCE))
    with patch(_CLOUD, return_value=True):
        assert await resolver.classify("u") == ProviderAvailability.USER_OK
    # No balance lookup happens on the run path — that would put a network call
    # in front of every request.
    assert svc.mock_calls == [("get_user_config", ("u",), {})]


# ── is_runnable ─────────────────────────────────────────────────────────────

def test_is_runnable_matrix():
    assert is_runnable(ProviderAvailability.USER_OK)
    assert is_runnable(ProviderAvailability.LOCAL_PASSTHROUGH)
    assert not is_runnable(ProviderAvailability.NO_PROVIDER)


# ── is_user_config_complete ─────────────────────────────────────────────────

def test_config_completeness_requires_every_required_slot():
    cfg = _cfg()
    assert is_user_config_complete(cfg)
    del cfg.slots["helper_llm"]
    assert not is_user_config_complete(cfg)


def test_config_completeness_rejects_none_and_empty():
    assert not is_user_config_complete(None)
    assert not is_user_config_complete(LLMConfig(providers={}, slots={}))


def test_config_completeness_rejects_a_slot_pointing_at_a_missing_provider():
    cfg = _cfg()
    cfg.slots["agent"] = SlotConfig(provider_id="ghost", model="claude-x")
    assert not is_user_config_complete(cfg)
