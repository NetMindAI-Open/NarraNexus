"""
@file_name: test_m0001_unified_memory_backfill.py
@author: Bin Liang
@date: 2026-06-09
@description: Migration 0001 — rebuilds search indexes for ALL agents' existing
data and moves legacy instance_social_entities into memory_entity.
"""
import json

import pytest

from backend.migrations.m0001_unified_memory_backfill import MIGRATION
import xyz_agent_context.memory.specs  # noqa: F401 — registers kinds

AGENT = "agent_mig01"
INST = "inst_mig01"


async def _seed_legacy(db):
    await db.insert("agents", {
        "agent_id": AGENT, "agent_name": "Mig Agent", "created_by": "u",
        "agent_type": "default",
    })
    await db.insert("module_instances", {
        "instance_id": INST, "module_class": "SocialNetworkModule",
        "agent_id": AGENT, "user_id": "u", "status": "active",
    })
    await db.insert("narratives", {
        "narrative_id": "nar_mig01", "type": "chat", "agent_id": AGENT,
        "narrative_info": json.dumps({"name": "雨崩徒步装备", "current_summary": "冲锋衣 登山杖 防高反"}),
        "topic_keywords": json.dumps(["徒步", "雨崩"]), "round_counter": 0,
    })
    await db.insert("instance_jobs", {
        "instance_id": INST, "job_id": "job_mig01", "agent_id": AGENT, "user_id": "u",
        "title": "核对对账", "description": "逐笔比对台账", "job_type": "scheduled",
        "status": "active",
    })
    await db.insert("events", {
        "event_id": "evt_mig01", "trigger": "chat", "trigger_source": "u",
        "agent_id": AGENT, "user_id": "u", "narrative_id": "nar_mig01",
        "final_output": "已记录雨崩徒步装备清单。",
    })
    # legacy entity in the RETIRED table (no memory_entity row yet)
    await db.insert("instance_social_entities", {
        "instance_id": INST, "entity_id": "ent_zhang", "entity_type": "user",
        "entity_name": "张伟", "entity_description": "CFO 负责对账",
        "tags": json.dumps(["CFO", "对账"]),
    })


@pytest.mark.asyncio
async def test_m0001_backfills_indexes_and_migrates_entities(db_client):
    await _seed_legacy(db_client)
    # precondition: indexes empty, entity only in legacy table
    assert not await db_client.get("memory_narrative", {"agent_id": AGENT})
    assert not await db_client.get("memory_entity", {"agent_id": AGENT})

    stats = await MIGRATION.apply(db_client)

    assert stats["agents"] >= 1
    assert stats["indexes_backfilled"] >= 3, "narrative + job + event"
    assert stats["legacy_entities_migrated"] >= 1

    for kind in ("narrative", "job", "event"):
        assert await db_client.get(f"memory_{kind}", {"agent_id": AGENT}), f"memory_{kind} empty"
    ent = await db_client.get("memory_entity", {"agent_id": AGENT})
    assert ent, "legacy entity not migrated into memory_entity"
    # entity is findable by NAME (derived content_text), the whole point
    assert any("张伟" in (r.get("content_text") or "") for r in ent)


@pytest.mark.asyncio
async def test_m0001_is_idempotent(db_client):
    await _seed_legacy(db_client)
    first = await MIGRATION.apply(db_client)
    second = await MIGRATION.apply(db_client)
    assert first["indexes_backfilled"] == second["indexes_backfilled"]
    # deterministic record_ids ⇒ no duplicate index rows
    assert len(await db_client.get("memory_narrative", {"agent_id": AGENT})) == 1
    assert len(await db_client.get("memory_entity", {"agent_id": AGENT})) == 1


@pytest.mark.asyncio
async def test_m0001_on_empty_db_is_safe_noop(db_client):
    stats = await MIGRATION.apply(db_client)
    assert stats == {"agents": 0, "indexes_backfilled": 0, "legacy_entities_migrated": 0}
