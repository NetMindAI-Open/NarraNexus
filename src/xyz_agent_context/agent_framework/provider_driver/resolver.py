"""
@file_name: resolver.py
@author: Bin Liang
@date: 2026-05-13
@description: Single-point Resolver — user_id → (ClaudeConfig,
              OpenAIConfig).

Replaces the two parallel paths that used to do this:

* HTTP request path: ``provider_resolver.ProviderResolver.resolve_and_set``
* Background trigger path: ``api_config._get_user_llm_configs_strict``

Both paths re-implemented "look up the 3 user_slots rows, fetch their
provider cards, build configs" with subtly different completeness
checks. This module folds them into one function. The legacy entry
points become thin shells that delegate here, so we get drop-in
compatibility while the call-site cleanup catches up.

Pipeline per call:

1. Load the user's quota row (still needed for the existing
   prefer_system_override branching during the migration window).
2. For each slot (agent / helper_llm):
   a. Look up the user_slots row.
   b. Look up the corresponding user_providers card by provider_id.
   c. Run self_heal_if_broken to auto-repair if slot.model ∉ card.models.
   d. Look up the Driver class via card.driver_type.
   e. Call the appropriate ``build_*_config`` on the Driver.
3. Return the two configs.

Visibility rule: a slot can point at a card owned by the user OR at
a system-shared card (owner_user_id IS NULL). Anything else is an
attempt to use someone else's credentials — fail with an explicit
error rather than silently building a config.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    LLMConfigNotConfigured,
    OpenAIConfig,
)
from xyz_agent_context.agent_framework.provider_driver.base import (
    ProviderCard,
)
from xyz_agent_context.agent_framework.provider_driver.registry import (
    get_driver_class,
)
from xyz_agent_context.agent_framework.provider_driver.self_heal import (
    self_heal_if_broken,
)

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


# Slot name → (build method name, config builder mapping). Single source
# of truth so we can iterate cleanly below.
_SLOT_BUILDERS = {
    "agent": "build_claude_config",
    "helper_llm": "build_openai_config",
}


def _is_visible(card: ProviderCard, user_id: str) -> bool:
    """Cards are visible if owned by this user, or system-shared
    (owner_user_id IS NULL, cloud only).

    Legacy rows from before Phase 0 backfill have ``owner_user_id``
    still null — for those we fall back to ``card.user_id`` matching.
    """
    if card.owner_user_id is None:
        # Two cases:
        #   1. System-shared card (cloud only) — accept regardless of user.
        #   2. Legacy row not yet backfilled — fall back to user_id match.
        if card.user_id and card.user_id != user_id:
            return False
        return True
    return card.owner_user_id == user_id


async def resolve_user_llm_configs(
    user_id: str,
    db: "AsyncDatabaseClient",
) -> tuple[ClaudeConfig, OpenAIConfig]:
    """Resolve a user's agent + helper_llm LLM configs in one shot.

    Raises ``LLMConfigNotConfigured`` if any required piece is missing
    or invisible. The caller is responsible for any further handling —
    e.g. AgentRuntime currently catches this and surfaces a friendly
    error message to the user.
    """
    # Pull all slot rows for this user.
    slot_rows = await db.get("user_slots", {"user_id": user_id})
    by_slot_name = {r.get("slot_name"): r for r in slot_rows or []}

    required = set(_SLOT_BUILDERS.keys())
    missing_slots = required - by_slot_name.keys()
    if missing_slots:
        raise LLMConfigNotConfigured(
            f"User {user_id!r} is missing the following slot bindings: "
            f"{sorted(missing_slots)}. Configure them in Settings → Providers."
        )

    # For each slot: card lookup → visibility check → self-heal → Driver
    # dispatch → build_*_config.
    cfgs: dict[str, object] = {}
    for slot_name, builder_method in _SLOT_BUILDERS.items():
        slot = by_slot_name[slot_name]

        provider_id = slot.get("provider_id")
        if not provider_id:
            raise LLMConfigNotConfigured(
                f"User {user_id!r} slot {slot_name!r} has no provider_id."
            )

        row = await db.get_one("user_providers", {"provider_id": provider_id})
        if not row:
            raise LLMConfigNotConfigured(
                f"User {user_id!r} slot {slot_name!r} → provider {provider_id!r} "
                f"not found. The provider may have been deleted."
            )

        card = ProviderCard.from_row(row)

        if not _is_visible(card, user_id):
            raise LLMConfigNotConfigured(
                f"User {user_id!r} slot {slot_name!r} → provider {provider_id!r} "
                f"is not visible (owned by {card.owner_user_id!r})."
            )

        if not card.is_active:
            raise LLMConfigNotConfigured(
                f"User {user_id!r} slot {slot_name!r} → provider {provider_id!r} "
                f"is inactive."
            )

        # Self-heal: rewrites slot in-place if model ∉ card.models, with
        # 24h debounce + notification. card is unchanged.
        card, slot = await self_heal_if_broken(card, slot, db)

        # Phase 0 graceful fallback: if a row was added before backfill
        # ran, its driver_type is still null. Try to derive on the fly
        # so the resolve path still works during the migration window.
        driver_type = card.driver_type
        if not driver_type:
            from xyz_agent_context.agent_framework.provider_driver.derive import (
                derive_driver_type,
            )
            driver_type = derive_driver_type(
                card.source, card.auth_type, card.protocol
            )
            if driver_type:
                # Best-effort persist so we don't re-derive every call.
                try:
                    await db.update(
                        "user_providers",
                        {"provider_id": card.provider_id},
                        {"driver_type": driver_type},
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"[resolver] Failed to persist on-the-fly driver_type "
                        f"for provider {card.provider_id!r}: {e}"
                    )

        if not driver_type:
            raise LLMConfigNotConfigured(
                f"User {user_id!r} slot {slot_name!r} → provider {provider_id!r}: "
                f"cannot determine driver_type from (source={card.source!r}, "
                f"auth_type={card.auth_type!r}, protocol={card.protocol!r})."
            )

        driver_cls = get_driver_class(driver_type)
        if driver_cls is None:
            raise LLMConfigNotConfigured(
                f"User {user_id!r} slot {slot_name!r} → unknown driver_type "
                f"{driver_type!r}. (Are you running cloud-only driver in local mode?)"
            )

        driver = driver_cls(card)
        builder = getattr(driver, builder_method)
        try:
            cfgs[slot_name] = builder(slot["model"])
        except NotImplementedError as e:
            raise LLMConfigNotConfigured(
                f"User {user_id!r} slot {slot_name!r}: driver "
                f"{driver_type!r} cannot satisfy this slot ({e})."
            ) from e

    return (
        cfgs["agent"],          # type: ignore[return-value]
        cfgs["helper_llm"],     # type: ignore[return-value]
    )


__all__ = ["resolve_user_llm_configs"]
