"""
@file_name: test_agent_slot_service.py
@author: rujing.yan
@date: 2026-07-09
@description: AgentSlotService — per-agent override CRUD + reused binding rules.

The per-agent override writer must enforce the SAME provider↔slot binding
rules as the user-level writer (via the shared ``validate_slot_binding``): the
codex_cli agent slot accepts ANY openai-protocol provider (only the protocol is
gated, not the source), and the helper slot accepts OAuth providers. Ownership
resolution and upsert/get/clear round-trip are covered here too.
"""
from __future__ import annotations

from collections import defaultdict

import pytest

from xyz_agent_context.agent_framework.agent_slot_service import AgentSlotService
from xyz_agent_context.agent_framework.user_provider_service import (
    UserProviderService,
)


class _FakeDB:
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


async def _owned_agent(db, agent_id="ag1", owner="u1"):
    await db.insert("agents", {"agent_id": agent_id, "created_by": owner})
    return UserProviderService(db)


@pytest.mark.asyncio
async def test_set_get_clear_roundtrip():
    db = _FakeDB()
    svc = await _owned_agent(db)
    _, ids = await svc.add_provider(
        user_id="u1", card_type="anthropic", api_key="sk-ant",
        models=["claude-opus-4-8", "claude-sonnet-4-6"],
    )
    aslot = AgentSlotService(db)

    row = await aslot.set_agent_slot(
        "ag1", "agent", ids[0], "claude-sonnet-4-6",
        reasoning_effort="high", agent_framework="claude_code",
        actor_is_staff=None,
    )
    assert row["provider_id"] == ids[0]
    assert row["model"] == "claude-sonnet-4-6"
    assert row["agent_framework"] == "claude_code"

    fetched = await aslot.get_agent_slots("ag1")
    assert set(fetched.keys()) == {"agent"}

    await aslot.clear_agent_slot("ag1", "agent")
    assert await aslot.get_agent_slots("ag1") == {}


@pytest.mark.asyncio
async def test_clear_all_removes_both_slots():
    db = _FakeDB()
    svc = await _owned_agent(db)
    _, anth = await svc.add_provider(
        user_id="u1", card_type="anthropic", api_key="sk-ant",
        models=["claude-opus-4-8"],
    )
    _, oai = await svc.add_provider(
        user_id="u1", card_type="openai", api_key="sk-oai",
        models=["gpt-5.4-mini"],
    )
    aslot = AgentSlotService(db)
    await aslot.set_agent_slot("ag1", "agent", anth[0], "claude-opus-4-8", actor_is_staff=None)
    await aslot.set_agent_slot("ag1", "helper_llm", oai[0], "gpt-5.4-mini", actor_is_staff=None)
    assert set((await aslot.get_agent_slots("ag1")).keys()) == {"agent", "helper_llm"}

    await aslot.clear_agent_slot("ag1", None)  # all
    assert await aslot.get_agent_slots("ag1") == {}


@pytest.mark.asyncio
async def test_codex_framework_accepts_aggregator_source():
    db = _FakeDB()
    await _owned_agent(db)
    # An openai-PROTOCOL aggregator (source=netmind). No source gate anymore
    # (restored pre-#81 behavior, binding rule #15): the codex agent slot
    # accepts any openai-protocol provider. Runtime Responses-API support is
    # the provider's concern, not policed at config time.
    await db.insert("user_providers", {
        "user_id": "u1", "provider_id": "p_nm", "name": "nm",
        "source": "netmind", "protocol": "openai", "auth_type": "api_key",
        "api_key": "sk-nm", "base_url": "https://api.netmind.ai/openai/v1",
        "models": '["gpt-5.4"]', "is_active": 1, "driver_type": "netmind",
    })
    row = await AgentSlotService(db).set_agent_slot(
        "ag1", "agent", "p_nm", "gpt-5.4", agent_framework="codex_cli",
        actor_is_staff=None,
    )
    assert row["provider_id"] == "p_nm"
    assert "agent" in (await AgentSlotService(db).get_agent_slots("ag1"))


@pytest.mark.asyncio
async def test_codex_framework_rejects_protocol_mismatch():
    db = _FakeDB()
    await _owned_agent(db)
    # The protocol gate still stands: an anthropic provider cannot back a
    # codex_cli (openai) agent slot.
    await db.insert("user_providers", {
        "user_id": "u1", "provider_id": "p_anth", "name": "anth",
        "source": "user", "protocol": "anthropic", "auth_type": "api_key",
        "api_key": "sk-a", "base_url": "", "models": '["claude-opus-4-8"]',
        "is_active": 1,
    })
    with pytest.raises(ValueError, match="protocol"):
        await AgentSlotService(db).set_agent_slot(
            "ag1", "agent", "p_anth", "claude-opus-4-8", agent_framework="codex_cli",
         actor_is_staff=None)


@pytest.mark.asyncio
async def test_helper_slot_accepts_oauth_provider():
    # OAuth now covers the helper slot: the resolver routes an OAuth helper to a
    # CliHelperConfig + CliHelperSDK (one-shot through the same CLI as the
    # agent), so validate_slot_binding no longer rejects it — and the per-agent
    # override (which shares that validator) must accept it too.
    db = _FakeDB()
    svc = await _owned_agent(db)
    _, claude_oauth = await svc.add_provider(user_id="u1", card_type="claude_oauth")
    row = await AgentSlotService(db).set_agent_slot(
        "ag1", "helper_llm", claude_oauth[0], "haiku",
        actor_is_staff=None,
    )
    assert row["provider_id"] == claude_oauth[0]
    assert "helper_llm" in (await AgentSlotService(db).get_agent_slots("ag1"))


@pytest.mark.asyncio
async def test_unknown_agent_has_no_owner():
    db = _FakeDB()
    svc = UserProviderService(db)
    _, ids = await svc.add_provider(
        user_id="u1", card_type="anthropic", api_key="sk-ant",
        models=["claude-opus-4-8"],
    )
    with pytest.raises(ValueError, match="owner"):
        await AgentSlotService(db).set_agent_slot(
            "ghost", "agent", ids[0], "claude-opus-4-8",
         actor_is_staff=None)
