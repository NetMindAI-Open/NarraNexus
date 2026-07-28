"""
@file_name: test_provisioner.py
@description: Turning a wallet into an ordinary provider card, on first login.

The invariants worth guarding are all about NOT giving money away twice, and
about never hijacking a user who already configured their own provider.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import backend.integrations.free_tier.provisioner as mod
from xyz_agent_context.agent_framework.providers.free_tier import FREE_TIER_SOURCE
from xyz_agent_context.integrations.free_tier.wallet_client import (
    ProvisionedWallet,
    WalletBalance,
    WalletUnavailable,
)


def _wallet(created=True, api_key="sk-wallet"):
    return ProvisionedWallet(
        created=created,
        api_key=api_key,
        balance=WalletBalance(
            currency="USD", max_budget=10.0, spend=0.0,
            remaining=10.0, exhausted=False,
        ),
    )


@pytest.fixture
def wiring(monkeypatch):
    """Stub the four collaborators: the flag, the wallet service, the db, and
    the provider service."""
    from xyz_agent_context.agent_framework.providers import resolver as resolver_mod
    from xyz_agent_context.agent_framework.providers import user_service as us_mod
    from xyz_agent_context.utils.db import db_factory

    state = {
        "existing_row": None,
        "config_complete": False,
        "onboarded": [],
        "wallet": _wallet(),
    }

    monkeypatch.setattr(mod, "is_free_tier_enabled", lambda: True)

    client = MagicMock()
    client.provision = AsyncMock(side_effect=lambda uid: state["wallet"])
    client.served_models = AsyncMock(
        side_effect=lambda: state.get("served_models", ["m-a", "m-b"])
    )
    monkeypatch.setattr(
        mod.WalletClient, "from_settings", classmethod(lambda cls: client)
    )
    state["client"] = client

    db = MagicMock()
    db.get_one = AsyncMock(side_effect=lambda *_a, **_k: state["existing_row"])
    monkeypatch.setattr(db_factory, "get_db_client", AsyncMock(return_value=db))

    monkeypatch.setattr(
        resolver_mod, "is_user_config_complete", lambda cfg: state["config_complete"]
    )

    def _svc(_db):
        m = MagicMock()
        m.get_user_config = AsyncMock(return_value=None)
        m.onboard_one_key = AsyncMock(
            side_effect=lambda uid, key, **kw: state["onboarded"].append((uid, key, kw))
        )
        return m

    monkeypatch.setattr(us_mod, "UserProviderService", _svc)
    # Locks are module state; a fresh one per test keeps them independent.
    mod._locks.clear()
    return state


@pytest.mark.asyncio
async def test_provisions_and_activates_a_fresh_user(wiring):
    assert await mod.ensure_free_tier_provider("u1") is True

    uid, key, kw = wiring["onboarded"][0]
    assert (uid, key) == ("u1", "sk-wallet")
    assert kw["provider_type"] == FREE_TIER_SOURCE
    # Nothing else is configured → bind the slots so the user can actually run.
    assert kw["activate"] is True
    # The card advertises what the GATEWAY routes, not the upstream catalogue.
    assert kw["models"] == ["m-a", "m-b"]


@pytest.mark.asyncio
async def test_catalogue_lookup_failure_does_not_block_provisioning(wiring):
    """A dropdown is cosmetic; a wallet is not. Fall back to the inherited
    list rather than leave the user with no free tier at all."""
    from xyz_agent_context.integrations.free_tier.wallet_client import (
        WalletUnavailable,
    )

    wiring["client"].served_models = AsyncMock(side_effect=WalletUnavailable("down"))

    assert await mod.ensure_free_tier_provider("u1") is True
    assert wiring["onboarded"][0][2]["models"] is None


@pytest.mark.asyncio
async def test_existing_card_is_a_no_op_and_never_calls_the_wallet(wiring):
    """Dedup must happen BEFORE the wallet call: a second login must not open a
    second budget, and must not even ask."""
    wiring["existing_row"] = {"provider_id": "p1", "source": FREE_TIER_SOURCE}

    assert await mod.ensure_free_tier_provider("u1") is False
    wiring["client"].provision.assert_not_called()
    assert wiring["onboarded"] == []


@pytest.mark.asyncio
async def test_a_user_with_their_own_provider_is_registered_not_hijacked(wiring):
    """The card becomes available to switch to; their working setup is left
    alone."""
    wiring["config_complete"] = True

    assert await mod.ensure_free_tier_provider("u1") is True
    _, _, kw = wiring["onboarded"][0]
    assert kw["activate"] is False


@pytest.mark.asyncio
async def test_keyless_wallet_is_reported_not_onboarded(wiring):
    """A wallet that exists with no provider row means an earlier attempt
    crashed between the two steps. The secret is unrecoverable, so writing a
    card with no key would produce a card that 401s on every run."""
    wiring["wallet"] = _wallet(created=False, api_key=None)

    assert await mod.ensure_free_tier_provider("u1") is False
    assert wiring["onboarded"] == []


@pytest.mark.asyncio
async def test_disabled_free_tier_touches_nothing(monkeypatch, wiring):
    monkeypatch.setattr(mod, "is_free_tier_enabled", lambda: False)
    assert await mod.ensure_free_tier_provider("u1") is False
    wiring["client"].provision.assert_not_called()


@pytest.mark.asyncio
async def test_unconfigured_wallet_service_is_a_no_op(monkeypatch, wiring):
    monkeypatch.setattr(
        mod.WalletClient, "from_settings", classmethod(lambda cls: None)
    )
    assert await mod.ensure_free_tier_provider("u1") is False


@pytest.mark.asyncio
async def test_wallet_outage_propagates_so_the_next_login_retries(wiring):
    wiring["client"].provision = AsyncMock(side_effect=WalletUnavailable("down"))
    with pytest.raises(WalletUnavailable):
        await mod.ensure_free_tier_provider("u1")
    assert wiring["onboarded"] == []
