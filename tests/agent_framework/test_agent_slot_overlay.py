"""
@file_name: test_agent_slot_overlay.py
@author: rujing.yan
@date: 2026-07-09
@description: Per-agent slot OVERRIDES overlay onto the owner's user_slots.

An agent inherits the owner's user-level slots by default; an ``agent_slots``
row for that agent wins on runs of THAT agent. Both the agent slot (framework
+ model) and the helper_llm slot (helper follows its agent) may be overridden,
independently. Without an ``agent_id`` — or with no override rows — resolution
is byte-identical to the user-only path.
"""
from __future__ import annotations

from collections import defaultdict

import pytest

from xyz_agent_context.agent_framework.agent_slot_service import AgentSlotService
from xyz_agent_context.agent_framework.provider_driver import (
    resolve_user_runtime_llm_configs,
)
from xyz_agent_context.agent_framework.provider_driver.self_heal import (
    self_heal_if_broken,
)
from xyz_agent_context.agent_framework.provider_driver.base import ProviderCard
from xyz_agent_context.agent_framework.user_provider_service import (
    UserProviderService,
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

    async def delete(self, table, filters):
        before = len(self.tables[table])
        self.tables[table] = [
            r for r in self.tables[table]
            if not all(r.get(k) == v for k, v in filters.items())
        ]
        return before - len(self.tables[table])


_ANTH_MODELS = ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"]


async def _seed_owner_defaults(db) -> tuple[UserProviderService, str, str]:
    """u1 owns agent ag1; agent+helper default to a shared anthropic key."""
    await db.insert("agents", {"agent_id": "ag1", "created_by": "u1"})
    svc = UserProviderService(db)
    _, default_ids = await svc.add_provider(
        user_id="u1", card_type="anthropic", api_key="sk-ant-default",
        models=_ANTH_MODELS,
    )
    await svc.set_slot("u1", "agent", default_ids[0], "claude-opus-4-8", actor_is_staff=None)
    await svc.set_slot("u1", "helper_llm", default_ids[0], "claude-haiku-4-5", actor_is_staff=None)
    return svc, "u1", default_ids[0]


@pytest.mark.asyncio
async def test_agent_override_wins_on_agent_slot_helper_still_inherits():
    db = _FakeDB()
    svc, _uid, _default_pid = await _seed_owner_defaults(db)
    _, override_ids = await svc.add_provider(
        user_id="u1", card_type="anthropic", api_key="sk-ant-override",
        models=_ANTH_MODELS,
    )
    await AgentSlotService(db).set_agent_slot(
        "ag1", "agent", override_ids[0], "claude-sonnet-4-6"
    , actor_is_staff=None)

    # No agent_id → owner default.
    base = await resolve_user_runtime_llm_configs("u1", db)
    assert base.claude.api_key == "sk-ant-default"
    assert base.claude.model == "claude-opus-4-8"

    # With agent_id → agent slot overridden; helper NOT overridden → inherits.
    over = await resolve_user_runtime_llm_configs("u1", db, agent_id="ag1")
    assert over.claude.api_key == "sk-ant-override"
    assert over.claude.model == "claude-sonnet-4-6"
    assert over.anthropic_helper is not None
    assert over.anthropic_helper.api_key == "sk-ant-default"
    assert over.anthropic_helper.model == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_helper_override_wins_agent_still_inherits():
    db = _FakeDB()
    svc, _uid, _default_pid = await _seed_owner_defaults(db)
    _, helper_ids = await svc.add_provider(
        user_id="u1", card_type="openai", api_key="sk-oai-helper",
        models=["gpt-5.4-mini"],
    )
    await AgentSlotService(db).set_agent_slot(
        "ag1", "helper_llm", helper_ids[0], "gpt-5.4-mini"
    , actor_is_staff=None)

    over = await resolve_user_runtime_llm_configs("u1", db, agent_id="ag1")
    # helper overridden to the openai key → openai helper path, not anthropic.
    assert over.anthropic_helper is None
    assert over.openai.api_key == "sk-oai-helper"
    assert over.openai.model == "gpt-5.4-mini"
    # agent slot NOT overridden → still the default anthropic key/model.
    assert over.claude.api_key == "sk-ant-default"
    assert over.claude.model == "claude-opus-4-8"


@pytest.mark.asyncio
async def test_no_override_rows_is_identical_to_user_path():
    db = _FakeDB()
    await _seed_owner_defaults(db)
    base = await resolve_user_runtime_llm_configs("u1", db)
    over = await resolve_user_runtime_llm_configs("u1", db, agent_id="ag1")
    assert over.claude.api_key == base.claude.api_key == "sk-ant-default"
    assert over.claude.model == base.claude.model == "claude-opus-4-8"


@pytest.mark.asyncio
async def test_empty_provider_override_row_is_ignored():
    """A framework-only stub (no provider_id) must NOT shadow the user
    default — the overlay skips it (matches _resolve_agent_framework_name)."""
    db = _FakeDB()
    await _seed_owner_defaults(db)
    # Hand-write a stub override row with an empty provider_id.
    await db.insert("agent_slots", {
        "agent_id": "ag1", "slot_name": "agent",
        "provider_id": "", "model": "", "agent_framework": "codex_cli",
    })
    over = await resolve_user_runtime_llm_configs("u1", db, agent_id="ag1")
    assert over.claude.api_key == "sk-ant-default"
    assert over.claude.model == "claude-opus-4-8"


@pytest.mark.asyncio
async def test_self_heal_writes_back_to_agent_slots():
    """An overridden slot whose model drifted out of its provider list heals
    back into agent_slots — NOT user_slots (the user default is untouched)."""
    db = _FakeDB()
    # Provider whose models list does NOT contain the override model.
    card = ProviderCard.from_row({
        "provider_id": "p_x", "user_id": "u1", "owner_user_id": "u1",
        "name": "n", "source": "user", "protocol": "anthropic",
        "auth_type": "api_key", "api_key": "sk", "base_url": "",
        "models": ["claude-opus-4-8"], "is_active": 1,
        "driver_type": "anthropic_api",
    })
    override_slot = {
        "agent_id": "ag1", "slot_name": "agent",
        "provider_id": "p_x", "model": "gone-model",
    }
    user_slot = {
        "user_id": "u1", "slot_name": "agent",
        "provider_id": "p_x", "model": "gone-model",
    }
    await db.insert("agent_slots", dict(override_slot))
    await db.insert("user_slots", dict(user_slot))

    await self_heal_if_broken(card, override_slot, db)

    healed_override = await db.get_one(
        "agent_slots", {"agent_id": "ag1", "slot_name": "agent"}
    )
    healed_user = await db.get_one(
        "user_slots", {"user_id": "u1", "slot_name": "agent"}
    )
    assert healed_override["model"] == "claude-opus-4-8"   # repaired in place
    assert healed_user["model"] == "gone-model"            # untouched
