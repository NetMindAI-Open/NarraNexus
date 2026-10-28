"""
@file_name: test_claude_oauth_slot_self_heal.py
@author: Bin Liang
@date: 2026-08-06
@description: Slot self-heal must judge claude_oauth cards by their EFFECTIVE
model list (the CLI family aliases), not the raw stored column.

The bug (Base recvqEiNbacKWa, "local CC model name never updates"): legacy
claude_oauth cards store pinned full ids (["claude-opus-4-6", ...]) in the
``models`` column. ``get_user_config`` overrides that column with the alias
list at read time, but the RESOLVER path builds its card via
``ProviderCard.from_row`` — raw column, no override — so a slot pinned to a
stale full id passes the ``is_slot_broken`` membership test against the raw
list and never heals. The two load paths disagree; the UI shows aliases while
the run stays pinned to a dead model.

These tests nail the fix: self-heal evaluates membership against
``effective_card_models`` (single shared source of truth with
``get_user_config``), and a stale ``claude-{family}-*`` id heals to ITS OWN
family alias (sonnet stays sonnet), not blindly to the list head.
"""
from collections import defaultdict

import pytest

from narranexus.platform.agent_framework.providers.driver.base import ProviderCard
from narranexus.platform.agent_framework.providers.driver.self_heal import (
    self_heal_if_broken,
)
from narranexus.platform.agent_framework.providers.model_catalog import (
    effective_card_models,
    get_default_models,
)


class _FakeDB:
    """Generic in-memory table store (table -> list[dict])."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = defaultdict(list)

    async def get(self, table, filters=None):
        filters = filters or {}
        return [
            r for r in self.tables[table]
            if all(r.get(k) == v for k, v in filters.items())
        ]

    async def get_one(self, table, filters):
        rows = await self.get(table, filters)
        return rows[0] if rows else None

    async def insert(self, table, data):
        self.tables[table].append(dict(data))

    async def update(self, table, filters, data):
        rows = await self.get(table, filters)
        for r in rows:
            r.update(data)
        return len(rows)


_LEGACY_STORED = ["claude-opus-4-6", "claude-sonnet-4-6"]


def _legacy_card() -> ProviderCard:
    """A pre-alias-era claude_oauth card exactly as the resolver loads it:
    raw stored column, pinned full ids."""
    return ProviderCard.from_row({
        "provider_id": "prov_old", "user_id": "u1", "owner_user_id": "u1",
        "name": "Claude Code (OAuth)", "source": "claude_oauth",
        "protocol": "anthropic", "auth_type": "oauth", "api_key": "",
        "base_url": "", "models": _LEGACY_STORED, "is_active": 1,
        "driver_type": "claude_oauth",
    })


def _slot_row(model: str) -> dict:
    return {
        "user_id": "u1", "slot_name": "agent", "provider_id": "prov_old",
        "model": model, "last_auto_repaired_at": None,
    }


# =============================================================================
# effective_card_models — the shared source of truth
# =============================================================================

def test_effective_models_claude_oauth_is_alias_list_regardless_of_stored():
    assert effective_card_models("claude_oauth", _LEGACY_STORED) == [
        "opus", "sonnet", "haiku",
    ]
    assert effective_card_models("claude_oauth", []) == ["opus", "sonnet", "haiku"]


def test_effective_models_codex_oauth_is_curated_list():
    assert effective_card_models("codex_oauth", ["gpt-old"]) == get_default_models(
        "codex_oauth", "openai"
    )


def test_effective_models_other_sources_keep_stored_list():
    stored = ["deepseek-ai/DeepSeek-V4-Pro"]
    assert effective_card_models("netmind", stored) == stored
    assert effective_card_models("user", stored) == stored


# =============================================================================
# Self-heal on the resolver path (raw card, stale pinned slot)
# =============================================================================

@pytest.mark.asyncio
async def test_stale_pinned_slot_on_claude_oauth_card_heals_to_family_alias():
    """slot=claude-sonnet-4-6 IS in the raw stored column — but the effective
    list is aliases-only, so the slot is broken and heals to "sonnet"."""
    db = _FakeDB()
    slot = _slot_row("claude-sonnet-4-6")
    db.tables["user_slots"].append(dict(slot))

    _, healed = await self_heal_if_broken(_legacy_card(), slot, db)

    assert healed["model"] == "sonnet"
    row = await db.get_one("user_slots", {"user_id": "u1", "slot_name": "agent"})
    assert row["model"] == "sonnet"
    notes = await db.get("user_notifications", {"user_id": "u1"})
    assert len(notes) == 1 and notes[0]["kind"] == "slot_auto_repaired"


@pytest.mark.asyncio
@pytest.mark.parametrize("stale, expected", [
    ("claude-opus-4-1", "opus"),
    ("claude-haiku-4-5", "haiku"),
])
async def test_family_is_preserved_for_every_claude_family(stale, expected):
    db = _FakeDB()
    slot = _slot_row(stale)
    db.tables["user_slots"].append(dict(slot))

    _, healed = await self_heal_if_broken(_legacy_card(), slot, db)

    assert healed["model"] == expected


@pytest.mark.asyncio
async def test_alias_slot_on_claude_oauth_card_is_not_broken():
    """A slot already on a family alias is healthy — no rewrite, no spam."""
    db = _FakeDB()
    slot = _slot_row("sonnet")
    db.tables["user_slots"].append(dict(slot))

    _, kept = await self_heal_if_broken(_legacy_card(), slot, db)

    assert kept["model"] == "sonnet"
    assert await db.get("user_notifications", {"user_id": "u1"}) == []


@pytest.mark.asyncio
async def test_non_family_garbage_on_claude_oauth_heals_to_list_head():
    """A model with no recognizable claude family falls back to the effective
    list's first entry (the existing pick_default_model strategy)."""
    db = _FakeDB()
    slot = _slot_row("gpt-4.1")
    db.tables["user_slots"].append(dict(slot))

    _, healed = await self_heal_if_broken(_legacy_card(), slot, db)

    assert healed["model"] == "opus"


@pytest.mark.asyncio
async def test_non_oauth_cards_keep_raw_membership_semantics():
    """A plain user card still judges against its own stored list — a private
    model the catalog has never heard of stays untouched."""
    db = _FakeDB()
    card = ProviderCard.from_row({
        "provider_id": "p_u", "user_id": "u1", "owner_user_id": "u1",
        "name": "mine", "source": "user", "protocol": "anthropic",
        "auth_type": "api_key", "api_key": "sk", "base_url": "",
        "models": ["my-private-model"], "is_active": 1,
        "driver_type": "anthropic_api",
    })
    slot = _slot_row("my-private-model")
    db.tables["user_slots"].append(dict(slot))

    _, kept = await self_heal_if_broken(card, slot, db)

    assert kept["model"] == "my-private-model"
    assert await db.get("user_notifications", {"user_id": "u1"}) == []
