"""
@file_name: removal_policy.py
@author: Bin Liang
@date: 2026-07-28
@description: May this user delete their free-tier card?

Not while it still has money — and the reason is stronger than policy.

The card's ``api_key`` IS the wallet key, and the gateway hands that secret out
exactly once at mint time. We never store it anywhere else. So deleting the card
does not delete the wallet: it deletes the only copy of the key that can reach
it, and whatever balance is left becomes permanently unspendable. There is no
recovery path short of an operator deleting the gateway key and re-provisioning
from zero.

That is why this guard fails CLOSED. If we cannot reach the wallet service we
refuse the delete, because "I could not check" and "it is empty" must not have
the same consequence when the consequence is irreversible.

Once the wallet IS spent, deletion is allowed — nothing of value is behind the
card any more, and a user who wants their provider list tidy should be able to
tidy it.
"""
from __future__ import annotations

from loguru import logger

from xyz_agent_context.agent_framework.providers.free_tier import FREE_TIER_SOURCE
from xyz_agent_context.integrations.free_tier.wallet_client import (
    WalletClient,
    WalletError,
    WalletMissing,
)


class FreeTierCardProtected(Exception):
    """The free-tier card cannot be deleted right now.

    Carries a user-facing message: routes return it verbatim, so it has to read
    like an explanation rather than an error code.
    """


async def ensure_free_tier_card_removable(user_id: str, provider_row: dict | None) -> None:
    """Raise :class:`FreeTierCardProtected` if this row is a free-tier card with
    balance left. No-op for every other provider.

    ``provider_row`` is the row the caller already fetched — passing it in keeps
    this from re-reading the DB and makes the guard trivially testable.
    """
    if (provider_row or {}).get("source") != FREE_TIER_SOURCE:
        return

    client = WalletClient.from_settings()
    if client is None:
        # Free tier is not configured in this deployment, so a `netmind_free`
        # row here is a leftover, not a live wallet — let it go.
        return

    try:
        balance = await client.balance(user_id)
    except WalletMissing:
        # The card outlived its wallet (operator deleted the gateway key).
        # Nothing to protect.
        return
    except WalletError as e:
        logger.warning(
            f"[free-tier] refusing card delete for {user_id}: balance "
            f"unverifiable ({e!r})"
        )
        raise FreeTierCardProtected(
            "Cannot verify your free credit right now, so this card is "
            "protected from deletion. Deleting it would make any remaining "
            "credit unreachable. Please try again shortly."
        ) from e

    if not balance.exhausted:
        raise FreeTierCardProtected(
            f"Your free tier still has {balance.remaining:.2f} "
            f"{balance.currency} left. This card holds the only key to that "
            f"credit, so deleting it would make the balance unreachable. "
            f"You don't need to delete it to use your own provider — add "
            f"yours and switch the Agent / Helper slots to it in "
            f"Settings → Providers; the free card just sits there unused."
        )
