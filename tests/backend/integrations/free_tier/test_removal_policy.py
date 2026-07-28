"""The free-tier card must survive as long as its wallet has money in it.

The card's api_key IS the wallet key and the gateway shows that secret once, so
deleting the card strands the balance for good. Everything here is about making
that irreversible step hard to take by accident.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import backend.integrations.free_tier.removal_policy as mod
from xyz_agent_context.agent_framework.providers.free_tier import FREE_TIER_SOURCE
from xyz_agent_context.integrations.free_tier.wallet_client import (
    WalletBalance,
    WalletMissing,
    WalletUnavailable,
)

FREE_ROW = {"provider_id": "p1", "source": FREE_TIER_SOURCE}
OWN_ROW = {"provider_id": "p2", "source": "netmind"}


def _wallet(monkeypatch, **kwargs):
    client = MagicMock()
    client.balance = AsyncMock(**kwargs)
    monkeypatch.setattr(
        mod.WalletClient, "from_settings", classmethod(lambda cls: client)
    )
    return client


def _balance(remaining: float, exhausted: bool) -> WalletBalance:
    return WalletBalance(
        currency="USD", max_budget=10.0, spend=10.0 - remaining,
        remaining=remaining, exhausted=exhausted,
    )


@pytest.mark.asyncio
async def test_card_with_credit_left_is_protected(monkeypatch):
    _wallet(monkeypatch, return_value=_balance(7.5, False))
    with pytest.raises(mod.FreeTierCardProtected) as e:
        await mod.ensure_free_tier_card_removable("u1", FREE_ROW)
    # The message must tell the user what to do instead — they usually want to
    # switch providers, not destroy credit.
    assert "7.50" in str(e.value)
    assert "switch" in str(e.value).lower()


@pytest.mark.asyncio
async def test_spent_card_may_be_deleted(monkeypatch):
    _wallet(monkeypatch, return_value=_balance(0.0, True))
    await mod.ensure_free_tier_card_removable("u1", FREE_ROW)  # no raise


@pytest.mark.asyncio
async def test_other_providers_are_untouched(monkeypatch):
    client = _wallet(monkeypatch, return_value=_balance(7.5, False))
    await mod.ensure_free_tier_card_removable("u1", OWN_ROW)
    await mod.ensure_free_tier_card_removable("u1", None)
    # Their own card's deletion must not even consult the wallet service.
    client.balance.assert_not_called()


@pytest.mark.asyncio
async def test_unverifiable_balance_fails_closed(monkeypatch):
    """'I could not check' and 'it is empty' must not have the same
    consequence when that consequence cannot be undone."""
    _wallet(monkeypatch, side_effect=WalletUnavailable("service down"))
    with pytest.raises(mod.FreeTierCardProtected):
        await mod.ensure_free_tier_card_removable("u1", FREE_ROW)


@pytest.mark.asyncio
async def test_card_whose_wallet_is_gone_may_be_deleted(monkeypatch):
    """Operator deleted the gateway key — the card is a leftover, not a purse."""
    _wallet(monkeypatch, side_effect=WalletMissing("no wallet"))
    await mod.ensure_free_tier_card_removable("u1", FREE_ROW)


@pytest.mark.asyncio
async def test_deployment_without_a_free_tier_does_not_block(monkeypatch):
    monkeypatch.setattr(
        mod.WalletClient, "from_settings", classmethod(lambda cls: None)
    )
    await mod.ensure_free_tier_card_removable("u1", FREE_ROW)
