"""
@file_name: self_heal.py
@author: Bin Liang
@date: 2026-05-13
@description: Reverse-validation + auto-repair for broken slot bindings.

The bug that motivated this whole thing: a user_slots row points at
``model`` X, but the corresponding ``user_providers.models`` JSON
array doesn't contain X (because the user edited the array later, or
the aggregator's catalog evolved out from under them). Every LLM call
on that slot then fails with a downstream provider error, the
user-facing exception is swallowed by an outer try/except, and the
user has no idea their PM agent stopped working.

The self-heal path makes that pattern self-recovering:

1. At resolve time, if ``slot.model NOT IN card.models``, pick a safe
   default (the platform's onboarding default for the card's source and
   slot kind when it is on the card, else the first element of
   card.models, else the catalogue head for an empty card; see
   ``derive.pick_default_model``).
2. Write the new model back to the slot.
3. Insert a row into ``user_notifications`` so the user finds out at
   their next UI interaction.
4. Use ``slot.last_auto_repaired_at`` as a 24h debounce — every LLM
   call shouldn't write a notification.

Sync-from-catalog is **not** in this path: the inverse problem
("user's models list is stale relative to ours") is a separate user-
initiated action because auto-syncing would silently undo a user who
deliberately removed a model from their card.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from loguru import logger

from narranexus.platform.agent_framework.providers.driver.base import ProviderCard
from narranexus.platform.agent_framework.providers.driver.derive import (
    is_slot_broken,
    pick_default_model,
)
from narranexus.platform.utils.timezone import utc_now

if TYPE_CHECKING:
    from narranexus.platform.utils.db.database import AsyncDatabaseClient


# Per-slot cool-down to avoid notification spam when the same broken
# slot fires many calls in a row. 24 hours matches the "developer
# notices at next login" cadence we want.
DEBOUNCE_WINDOW = timedelta(hours=24)


def _parse_dt(value) -> Optional[datetime]:
    """Tolerant datetime parser — handles ISO strings (sqlite), datetime
    objects (mysql via aiomysql), and Nones.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        # SQLite returns "2026-05-13 02:13:31.123456" or with 'T' separator
        cleaned = value.replace("T", " ").rstrip("Z")
        try:
            dt = datetime.fromisoformat(cleaned)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


async def self_heal_if_broken(
    card: ProviderCard,
    slot: dict,
    db: "AsyncDatabaseClient",
) -> tuple[ProviderCard, dict]:
    """Check if ``slot.model`` is missing from ``card.models``. If so,
    pick a default, persist it, and write a notification.

    Returns the (possibly updated) ``(card, slot)`` pair. The card never
    mutates here — only the slot's ``model`` field is updated in-place
    on the dict so downstream resolver code uses the new value.

    The function is a no-op when:

    * The slot is fine (model present in card.models).
    * The slot was already auto-repaired within the debounce window.
    * No safe default can be picked (logs an error and lets the broken
      slot through — the LLM call will fail downstream, but at least
      we don't silently rewrite to a garbage value).
    """
    # Membership is judged against the card's EFFECTIVE list, not the raw
    # stored column. For the OAuth CLI cards (claude_oauth / codex_oauth)
    # the runtime list is catalog-owned (family aliases / curated); a slot
    # pinned to a legacy full id sits happily inside the raw column and
    # would never heal if we checked that (the "local CC model name never
    # updates" bug — the UI showed aliases via get_user_config's override
    # while this path kept resolving the stale pin).
    from narranexus.platform.agent_framework.providers.model_catalog import (
        claude_family_alias,
        effective_card_models,
    )

    card_models = effective_card_models(card.source, card.models)

    slot_model = slot.get("model") or ""
    if not is_slot_broken(slot_model, card_models):
        return card, slot

    # Debounce: skip if we auto-repaired in the last 24h.
    last_repair = _parse_dt(slot.get("last_auto_repaired_at"))
    if last_repair and (utc_now() - last_repair) < DEBOUNCE_WINDOW:
        logger.debug(
            f"[self_heal] slot {slot.get('slot_name')!r} broken but within debounce window "
            f"(last_auto_repaired_at={last_repair.isoformat()})"
        )
        return card, slot

    # A pinned Claude full id keeps its own family: a sonnet user stays on
    # sonnet, not whatever happens to head the list. Only when no family is
    # recognizable (or the alias is somehow off-card) does the generic
    # list-head strategy take over.
    new_model: Optional[str] = None
    family = claude_family_alias(slot_model)
    if family and family in card_models:
        new_model = family
    if not new_model:
        new_model = pick_default_model(
            card_models, card.source, card.protocol, slot.get("slot_name")
        )
    if not new_model:
        logger.error(
            f"[self_heal] slot {slot.get('slot_name')!r} broken AND no safe default "
            f"available (card.models={card.models}, source={card.source}, "
            f"protocol={card.protocol}). Leaving slot alone so the downstream "
            f"call surfaces a real error."
        )
        return card, slot

    user_id = slot.get("user_id") or card.user_id
    slot_name = slot.get("slot_name")
    old_model = slot_model

    now = utc_now()
    now_iso = now.isoformat()

    # Persist the swap. Table-aware: a per-agent OVERRIDE row (from
    # agent_slots) carries ``agent_id`` and must heal back into agent_slots,
    # keyed by (agent_id, slot_name) — NOT user_slots, which would silently
    # rewrite the user-level default the override was shadowing. A plain
    # user_slots row carries ``user_id`` and heals in place. Both filters hit
    # the respective table's unique index, so each is a single-row write.
    override_agent_id = slot.get("agent_id")
    if override_agent_id:
        heal_table = "agent_slots"
        heal_filter = {"agent_id": override_agent_id, "slot_name": slot_name}
    else:
        heal_table = "user_slots"
        heal_filter = {"user_id": user_id, "slot_name": slot_name}
    await db.update(
        heal_table,
        heal_filter,
        {
            "model": new_model,
            "last_auto_repaired_at": now_iso,
            "updated_at": now_iso,
        },
    )

    # Write a notification row. Severity = warning because the call
    # would have failed without this swap — the user really should
    # know their config drifted.
    payload = {
        "slot_name": slot_name,
        "old_model": old_model,
        "new_model": new_model,
        "card_name": card.name,
        "card_provider_id": card.provider_id,
        "reason": "model_not_in_provider_list",
    }
    await db.insert(
        "user_notifications",
        {
            "user_id": user_id,
            "kind": "slot_auto_repaired",
            "payload": json.dumps(payload, ensure_ascii=False),
            "severity": "warning",
            "read_at": None,
            "created_at": now_iso,
        },
    )

    logger.info(
        f"[self_heal] Repaired slot {slot_name!r} for user {user_id!r}: "
        f"{old_model!r} → {new_model!r} (card={card.name!r})"
    )

    # Update the in-memory slot dict so callers see the new model
    # without re-reading.
    slot["model"] = new_model
    slot["last_auto_repaired_at"] = now_iso
    return card, slot


__all__ = ["self_heal_if_broken", "DEBOUNCE_WINDOW"]
