"""
@file_name: test_self_heal_onboard_default.py
@description: Self-heal must repair a broken slot to the platform's onboarding
default for that source + slot kind, NOT to ``card.models[0]``.

The bug: NetMind cards carry the synced catalogue, whose first entry is
``Qwen/Qwen3.5-Omni-Flash``. ``pick_default_model`` took the list head, so a
mass heal (34 helper slots on 2026-09-11) silently re-pointed slots at an
omni/multimodal model. The repair order is now:

1. onboarding default for (source, slot kind) -- when on the card
2. first model on the card
3. catalogue head (empty card only)

The catalogue head is deliberately NOT preferred over the card head: it is the
alphabetical head of a ledger snapshot, not a recommendation.
"""
from collections import defaultdict

import pytest

from narranexus.platform.agent_framework.providers import model_catalog
from narranexus.platform.agent_framework.providers.driver.base import ProviderCard
from narranexus.platform.agent_framework.providers.driver.derive import (
    pick_default_model,
)
from narranexus.platform.agent_framework.providers.driver.self_heal import (
    self_heal_if_broken,
)

FLASH = "deepseek-ai/DeepSeek-V4.1-Flash"
OLD_FLASH = "deepseek-ai/DeepSeek-V4-Flash"
OMNI = "Qwen/Qwen3.5-Omni-Flash"


class _FakeDB:
    def __init__(self):
        self.tables: dict[str, list[dict]] = defaultdict(list)

    async def get(self, table, filters=None):
        filters = filters or {}
        return [
            r for r in self.tables[table]
            if all(r.get(k) == v for k, v in filters.items())
        ]

    async def insert(self, table, data):
        self.tables[table].append(dict(data))

    async def update(self, table, filters, data):
        rows = await self.get(table, filters)
        for r in rows:
            r.update(data)
        return len(rows)


def _card(source: str, models: list[str], protocol: str = "openai") -> ProviderCard:
    return ProviderCard.from_row({
        "provider_id": "p1", "user_id": "u1", "owner_user_id": "u1",
        "name": source, "source": source, "protocol": protocol,
        "auth_type": "api_key", "api_key": "sk", "base_url": "",
        "models": models, "is_active": 1, "driver_type": "openai_api",
    })


def _slot(slot_name: str, model: str = "retired-model") -> dict:
    return {
        "user_id": "u1", "slot_name": slot_name, "provider_id": "p1",
        "model": model, "last_auto_repaired_at": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["netmind", "netmind_free"])
@pytest.mark.parametrize("slot_name", ["agent", "helper_llm"])
async def test_netmind_slot_heals_to_flash_not_list_head(source, slot_name, monkeypatch):
    for var in ("FREE_TIER_AGENT_MODEL", "FREE_TIER_HELPER_MODEL"):
        monkeypatch.delenv(var, raising=False)
    db = _FakeDB()
    slot = _slot(slot_name)
    db.tables["user_slots"].append(dict(slot))
    card = _card(source, [OMNI, "Qwen/Qwen3-32B", FLASH, "deepseek-ai/DeepSeek-V4-Pro"])

    _, healed = await self_heal_if_broken(card, slot, db)

    assert healed["model"] == FLASH
    rows = await db.get("user_slots", {"user_id": "u1", "slot_name": slot_name})
    assert rows[0]["model"] == FLASH


@pytest.mark.asyncio
@pytest.mark.parametrize("slot_name, env_var", [
    ("agent", "FREE_TIER_AGENT_MODEL"),
    ("helper_llm", "FREE_TIER_HELPER_MODEL"),
])
async def test_netmind_free_heal_follows_the_ops_env_knob(slot_name, env_var, monkeypatch):
    """The free wallet's model is an ops decision (FREE_TIER_*_MODEL). A heal
    must land where a fresh free-tier onboarding would, not on the code constant."""
    ops_pick = "Qwen/Qwen3-32B"
    monkeypatch.setenv(env_var, ops_pick)
    db = _FakeDB()
    slot = _slot(slot_name)
    db.tables["user_slots"].append(dict(slot))
    card = _card("netmind_free", [OMNI, ops_pick, FLASH])

    _, healed = await self_heal_if_broken(card, slot, db)

    assert healed["model"] == ops_pick


@pytest.mark.parametrize("slot_name, env_var", [
    ("agent", "FREE_TIER_AGENT_MODEL"),
    ("helper_llm", "FREE_TIER_HELPER_MODEL"),
])
def test_empty_free_tier_env_falls_back_to_the_constant(slot_name, env_var, monkeypatch):
    """An empty env string must never yield "" (a falsy target would silently
    drop the heal back to the card head)."""
    monkeypatch.setenv(env_var, "")
    assert model_catalog.get_onboard_default_model("netmind_free", slot_name) == FLASH


def test_onboard_defaults_map_covers_sources_only_and_follows_env(monkeypatch):
    """The map the /catalog endpoint ships to the UI: keyed by SOURCE (never the
    anthropic/openai protocol keys), netmind_free built from the env knob."""
    monkeypatch.setenv("FREE_TIER_AGENT_MODEL", "x/agent")
    monkeypatch.delenv("FREE_TIER_HELPER_MODEL", raising=False)
    defaults = model_catalog.get_onboard_defaults()
    assert "anthropic" not in defaults and "openai" not in defaults
    assert defaults["netmind"] == {"agent": FLASH, "helper": FLASH}
    assert defaults["netmind_free"] == {"agent": "x/agent", "helper": FLASH}
    # Intended scope = EVERY source that has an onboarding default, not a
    # NetMind-only whitelist: the UI mirrors what backend onboarding / self-heal
    # would pick for these cards.
    for source in ("yunwu", "openrouter"):
        assert defaults[source] == {"agent": "claude-opus-4-8", "helper": "gpt-5.4-mini"}


@pytest.mark.asyncio
@pytest.mark.parametrize("slot_name", ["agent", "helper_llm"])
async def test_prod_shape_card_with_v41_flash_but_no_v4_flash(slot_name):
    """Real prod shape (2026-09-11 onward): NetMind dropped V4-Flash, the card
    starts with an omni model and lists V4.1-Flash. A slot still pinned to the
    vanished V4-Flash must heal to V4.1-Flash, not the omni head."""
    db = _FakeDB()
    slot = _slot(slot_name, model=OLD_FLASH)
    db.tables["user_slots"].append(dict(slot))
    card = _card("netmind", [OMNI, "Qwen/Qwen3-32B", FLASH, "deepseek-ai/DeepSeek-V4-Pro"])

    _, healed = await self_heal_if_broken(card, slot, db)

    assert healed["model"] == FLASH


def test_falls_back_to_first_on_card_when_flash_is_absent():
    card_models = [OMNI, "Qwen/Qwen3-32B"]
    assert pick_default_model(card_models, "netmind", "openai", "agent") == OMNI


def test_catalogue_head_on_card_does_not_beat_card_head(monkeypatch):
    """The catalogue "default" is just the head of a committed ledger snapshot
    (alphabetical -- e.g. a 32k-context Qwen). Even when it is on the card it
    must NOT outrank the card's own head."""
    monkeypatch.setattr(
        model_catalog, "get_default_models",
        lambda source, protocol: ["cat-head", "cat-other"],
    )
    got = pick_default_model(["card-head", "cat-head"], "netmind", "openai", "agent")
    assert got == "card-head"


def test_catalogue_head_is_last_resort_for_empty_card(monkeypatch):
    monkeypatch.setattr(
        model_catalog, "get_default_models",
        lambda source, protocol: ["cat-default"],
    )
    assert pick_default_model([], "netmind", "openai", "agent") == "cat-default"


def test_no_slot_kind_skips_onboarding_default():
    """Unknown slot kind (e.g. embedding) must not inherit the agent default."""
    got = pick_default_model([OMNI, FLASH], "netmind", "openai", None)  # embedding etc.
    assert got == OMNI


def test_source_without_onboarding_default_keeps_card_head():
    """A plain BYOK card ('user') has no onboarding entry -> unchanged
    list-head behaviour."""
    got = pick_default_model(["a", "claude-opus-4-8"], "user", "anthropic", "agent")
    assert got == "a"


def test_slot_name_is_a_required_argument():
    """A forgotten slot kind would silently fall back to the card head -- the
    exact bug this module guards -- so there must be no default."""
    with pytest.raises(TypeError):
        pick_default_model([OMNI, FLASH], "netmind", "openai")  # type: ignore[call-arg]
