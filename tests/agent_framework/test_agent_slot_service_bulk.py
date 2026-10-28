"""
@file_name: test_agent_slot_service_bulk.py
@author:
@date: 2026-08-26
@description: Owner-scoped bulk ops on AgentSlotService — override stats,
    clear-to-inherit across all of an owner's agents, and effective-model
    overview. Mirrors the existing test_agent_slot_service.py _FakeDB idiom
    (equality-filter in-memory store), which matches the db surface these
    methods use (get / get_one / insert / delete).
"""
from __future__ import annotations


import pytest

from tests.fake_db import FakeDB

from narranexus.platform.agent_framework.providers.slot_service import AgentSlotService


_FakeDB = FakeDB
async def _mk_agent(db, agent_id, owner):
    await db.insert("agents", {"agent_id": agent_id, "created_by": owner, "name": agent_id})


async def _mk_override(db, agent_id, slot_name, model="m-x"):
    await db.insert(
        "agent_slots",
        {"agent_id": agent_id, "slot_name": slot_name, "provider_id": "p1",
         "model": model, "params_json": "{}",
         "created_at": "2026-08-26T00:00:00+00:00",
         "updated_at": "2026-08-26T00:00:00+00:00"},
    )


async def _mk_user_slot(db, owner, slot_name, model):
    await db.insert(
        "user_slots",
        {"user_id": owner, "slot_name": slot_name, "provider_id": "p1",
         "model": model, "params_json": "{}", "agent_framework": "nexus_power"},
    )


@pytest.mark.asyncio
async def test_count_owner_overrides_counts_per_slot_own_agents_only():
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await _mk_agent(db, "a2", "owner1")
    await _mk_agent(db, "a3", "owner1")   # inherits (no override row)
    await _mk_agent(db, "b1", "owner2")   # someone else's
    await _mk_override(db, "a1", "agent")
    await _mk_override(db, "a2", "agent")
    await _mk_override(db, "a2", "helper_llm")
    await _mk_override(db, "b1", "agent")  # must NOT be counted for owner1

    stats = await AgentSlotService(db).count_owner_overrides("owner1")
    assert stats == {"agent": 2, "helper_llm": 1, "total_agents": 3}


@pytest.mark.asyncio
async def test_clear_owner_agents_slot_clears_only_that_slot_own_agents():
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await _mk_agent(db, "a2", "owner1")
    await _mk_agent(db, "b1", "owner2")
    await _mk_override(db, "a1", "agent")
    await _mk_override(db, "a1", "helper_llm")
    await _mk_override(db, "a2", "agent")
    await _mk_override(db, "b1", "agent")

    cleared = (await AgentSlotService(db).clear_owner_agents_slots("owner1", ["agent"]))["agent"]
    assert cleared == 2  # a1.agent + a2.agent

    # a1.helper_llm untouched
    assert await db.get_one("agent_slots", {"agent_id": "a1", "slot_name": "helper_llm"}) is not None
    # a1.agent / a2.agent gone
    assert await db.get_one("agent_slots", {"agent_id": "a1", "slot_name": "agent"}) is None
    assert await db.get_one("agent_slots", {"agent_id": "a2", "slot_name": "agent"}) is None
    # other owner's agent untouched
    assert await db.get_one("agent_slots", {"agent_id": "b1", "slot_name": "agent"}) is not None


@pytest.mark.asyncio
async def test_clear_owner_agents_slot_rejects_bad_slot():
    db = _FakeDB()
    with pytest.raises(ValueError):
        await AgentSlotService(db).clear_owner_agents_slots("owner1", ["nope"])


@pytest.mark.asyncio
async def test_owner_agents_overview_effective_and_inheriting():
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await _mk_agent(db, "a2", "owner1")
    await _mk_agent(db, "b1", "owner2")
    await _mk_user_slot(db, "owner1", "agent", "default-agent-model")
    await _mk_user_slot(db, "owner1", "helper_llm", "default-helper-model")
    await _mk_override(db, "a1", "agent", model="pinned-agent-model")  # a1 overrides agent

    overview = await AgentSlotService(db).owner_agents_overview("owner1")

    assert set(overview.keys()) == {"a1", "a2"}          # own agents only
    assert overview["a1"]["agent"] == {
        "model": "pinned-agent-model", "inheriting": False, "agent_framework": "nexus_power",
    }
    assert overview["a1"]["helper_llm"] == {"model": "default-helper-model", "inheriting": True}
    assert overview["a2"]["agent"] == {
        "model": "default-agent-model", "inheriting": True, "agent_framework": "nexus_power",
    }


async def _mk_stub_override(db, agent_id, slot_name):
    # An empty-provider framework-only stub row — must NOT read as an override.
    await db.insert(
        "agent_slots",
        {"agent_id": agent_id, "slot_name": slot_name, "provider_id": "",
         "model": "", "params_json": "{}", "agent_framework": "codex_cli",
         "created_at": "2026-08-26T00:00:00+00:00",
         "updated_at": "2026-08-26T00:00:00+00:00"},
    )


@pytest.mark.asyncio
async def test_stub_override_row_is_not_counted():
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await _mk_stub_override(db, "a1", "agent")  # empty provider_id
    stats = await AgentSlotService(db).count_owner_overrides("owner1")
    assert stats == {"agent": 0, "helper_llm": 0, "total_agents": 1}


@pytest.mark.asyncio
async def test_stub_override_row_reads_as_inheriting_in_overview():
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await _mk_user_slot(db, "owner1", "agent", "default-agent-model")
    await _mk_stub_override(db, "a1", "agent")
    overview = await AgentSlotService(db).owner_agents_overview("owner1")
    # stub → inheriting, model comes from owner default (matches runtime/card),
    # and the stub's codex_cli framework does NOT win either: the agent runs
    # on the owner default, so the directory must not show the Codex brand.
    assert overview["a1"]["agent"] == {
        "model": "default-agent-model", "inheriting": True, "agent_framework": "nexus_power",
    }


@pytest.mark.asyncio
async def test_overview_framework_follows_the_owner_default_when_set():
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await db.insert(
        "user_slots",
        {"user_id": "owner1", "slot_name": "agent", "provider_id": "p1",
         "model": "def-a", "params_json": "{}", "agent_framework": "codex_cli"},
    )
    overview = await AgentSlotService(db).owner_agents_overview("owner1")
    assert overview["a1"]["agent"]["agent_framework"] == "codex_cli"


@pytest.mark.asyncio
async def test_overview_framework_defaults_to_nexus_power_with_no_rows_at_all():
    # A brand-new owner: no user_slots row, no override. The platform default
    # (2026-08-20) is nexus_power — never a literal claude_code anywhere.
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    overview = await AgentSlotService(db).owner_agents_overview("owner1")
    assert overview["a1"]["agent"] == {"model": "", "inheriting": True, "agent_framework": "nexus_power"}


@pytest.mark.asyncio
async def test_overview_framework_from_a_rebinding_override():
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await _mk_user_slot(db, "owner1", "agent", "def-a")
    await db.insert(
        "agent_slots",
        {"agent_id": "a1", "slot_name": "agent", "provider_id": "p9",
         "model": "gpt-5", "params_json": "{}", "agent_framework": "codex_cli",
         "created_at": "x", "updated_at": "x"},
    )
    overview = await AgentSlotService(db).owner_agents_overview("owner1")
    assert overview["a1"]["agent"] == {"model": "gpt-5", "inheriting": False, "agent_framework": "codex_cli"}


@pytest.mark.asyncio
async def test_clear_snapshots_deleted_rows_to_audit():
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await _mk_override(db, "a1", "agent", model="pinned")
    cleared = (await AgentSlotService(db).clear_owner_agents_slots("owner1", ["agent"]))["agent"]
    assert cleared == 1
    audit = await db.get("agent_slot_clear_audit", {"agent_id": "a1"})
    assert len(audit) == 1
    assert audit[0]["model"] == "pinned"
    assert audit[0]["user_id"] == "owner1"
    assert audit[0]["slot_name"] == "agent"


@pytest.mark.asyncio
async def test_audit_preserves_null_agent_framework_and_params():
    # A source row with agent_framework/params_json = NULL must snapshot as
    # NULL (not ""), else "inherit"/"all auto" become indistinguishable from
    # an empty string on restore.
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await db.insert(
        "agent_slots",
        {"agent_id": "a1", "slot_name": "agent", "provider_id": "p1",
         "model": "m", "params_json": None, "agent_framework": None,
         "created_at": "x", "updated_at": "x"},
    )
    await AgentSlotService(db).clear_owner_agents_slots("owner1", ["agent"])
    audit = await db.get_one("agent_slot_clear_audit", {"agent_id": "a1"})
    assert audit["agent_framework"] is None
    assert audit["params_json"] is None


@pytest.mark.asyncio
async def test_clear_owner_agents_slots_plural_validates_and_dedups():
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await _mk_override(db, "a1", "agent")
    # dedup: ["agent","agent"] clears once.
    out = await AgentSlotService(db).clear_owner_agents_slots("owner1", ["agent", "agent"])
    assert out == {"agent": 1}


@pytest.mark.asyncio
async def test_clear_owner_agents_slots_plural_fail_closed_bad_slot():
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await _mk_override(db, "a1", "agent")
    with pytest.raises(ValueError):
        await AgentSlotService(db).clear_owner_agents_slots("owner1", ["agent", "bogus"])
    # fail-closed: the valid slot was NOT cleared.
    assert await db.get_one("agent_slots", {"agent_id": "a1", "slot_name": "agent"}) is not None


@pytest.mark.asyncio
async def test_clear_also_removes_stub_rows_but_still_snapshots():
    # clear deletes ALL rows for (agent,slot), stubs included (they exist to be
    # cleared) — only counting/display skip stubs, not the clear itself.
    db = _FakeDB()
    await _mk_agent(db, "a1", "owner1")
    await _mk_stub_override(db, "a1", "agent")
    cleared = (await AgentSlotService(db).clear_owner_agents_slots("owner1", ["agent"]))["agent"]
    assert cleared == 1
    assert await db.get_one("agent_slots", {"agent_id": "a1", "slot_name": "agent"}) is None


@pytest.mark.asyncio
async def test_overview_agent_ids_filter_never_leaks_a_foreign_agent():
    # The directory passes the ids it is about to list; a public agent owned
    # by someone else must come back ABSENT, not resolved from its owner's slots.
    db = _FakeDB()
    await _mk_agent(db, "mine", "owner1")
    await _mk_agent(db, "theirs", "owner2")
    await _mk_user_slot(db, "owner2", "agent", "their-secret-model")
    overview = await AgentSlotService(db).owner_agents_overview("owner1", agent_ids=["mine", "theirs"])
    assert set(overview) == {"mine"}
    # and narrowing really narrows
    await _mk_agent(db, "mine2", "owner1")
    assert set(await AgentSlotService(db).owner_agents_overview("owner1", agent_ids=["mine2"])) == {"mine2"}
