"""
@file_name: backfill.py
@author: NetMind.AI
@date: 2026-06-09
@description: Re-project operational rows into the unified-memory SEARCH INDEXES.

The live writers (crud._index_narrative / step_4 interaction / create_job /
send_message) only index data they CREATE. Data that predates the unified-memory
search layer — whether sitting in a long-lived DB or carried in by a bundle
import (which raw-inserts) — has no index and is invisible to `remember`.

This module rebuilds those indexes, composing the SAME searchable text as each
live writer. Two entry points share one implementation:
  - bundle import calls `backfill_agent_search_indexes` per freshly imported agent
  - the one-shot data migration calls it for every agent in the DB, plus
    `migrate_legacy_entities` to move the retired instance_social_entities table
    into memory_entity.

Everything here is IDEMPOTENT (deterministic record_ids → upsert) and RESILIENT
(a single bad row is logged and skipped, never aborting the rest), so it is safe
to run on every import and on every startup until the migration ledger records it.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


def _jload(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value or {}


async def backfill_agent_search_indexes(db: "AsyncDatabaseClient", agent_id: str) -> int:
    """Re-project ONE agent's narrative / job / bus / interaction rows into
    memory_<kind>, with the same searchable text + source_ref pointer the live
    writers produce. entity is handled separately (migrate_legacy_entities /
    save_entity); observation is LLM-derived and cannot be reconstructed.

    Returns the number of index records written.
    """
    from xyz_agent_context.memory import MemoryEngine

    eng = MemoryEngine(db, agent_id)
    indexed = 0

    async def _idx(kind: str, source_id: str, text: str, **kw) -> None:
        nonlocal indexed
        if not source_id or not (text or "").strip():
            return
        try:
            await eng.index(kind, source_id, text, scope_type="agent", **kw)
            indexed += 1
        except Exception as e:  # noqa: BLE001 — one bad row must not sink the rest
            logger.warning(f"backfill index {kind}:{source_id} failed: {e}")

    # narrative — same surface as crud._index_narrative / narrative routing
    for n in await db.get("narratives", {"agent_id": agent_id}):
        info = _jload(n.get("narrative_info"))
        kws = _jload(n.get("topic_keywords"))
        kws = kws if isinstance(kws, list) else []
        text = "\n".join(p for p in [
            info.get("name") or "",
            info.get("current_summary") or "",
            info.get("description") or "",
            " ".join(str(k) for k in kws),
        ] if p)
        await _idx("narrative", n.get("narrative_id"), text, tags=kws)

    # job — title + description (matches job_repository.create_job)
    for j in await db.get("instance_jobs", {"agent_id": agent_id}):
        text = "\n".join(p for p in [j.get("title") or "", j.get("description") or ""] if p)
        await _idx("job", j.get("job_id"), text)

    # bus — messages this agent SENT (matches local_bus.send_message: from_agent)
    for m in await db.get("bus_messages", {"from_agent": agent_id}):
        await _idx("bus", m.get("message_id"), m.get("content") or "")

    # interaction — one index per event (matches step_4: user input + final_output)
    for e in await db.get("events", {"agent_id": agent_id}):
        env = _jload(e.get("env_context"))
        user_in = env.get("input") if isinstance(env, dict) else ""
        text = "\n".join(p for p in [user_in or "", e.get("final_output") or ""] if p)
        await _idx("event", e.get("event_id"), text)

    return indexed


async def migrate_legacy_entities(db: "AsyncDatabaseClient") -> int:
    """Move entities stranded in the retired `instance_social_entities` table
    into `memory_entity` via the repo's current scheme (deterministic record_id,
    derived searchable content_text). NEW entities already live in memory_entity;
    only pre-refactor rows need this. Idempotent (save_entity upserts).

    Returns the number of entities migrated.
    """
    from xyz_agent_context.repository import SocialNetworkRepository
    from xyz_agent_context.schema.entity_schema import SocialNetworkEntity

    # instance_id → agent_id (entities are instance-scoped; recall needs agent_id)
    inst2agent = {
        r["instance_id"]: r["agent_id"]
        for r in await db.get("module_instances")
        if r.get("agent_id") and r.get("instance_id")
    }

    try:
        rows = await db.get("instance_social_entities")
    except Exception as e:  # noqa: BLE001 — table may not exist on a fresh DB
        logger.info(f"migrate_legacy_entities: no legacy table ({e})")
        return 0

    migrated = 0
    for r in rows:
        agent_id = inst2agent.get(r.get("instance_id"))
        if not agent_id:
            continue  # orphan entity (its instance is gone) — nothing to attach it to
        try:
            entity = SocialNetworkEntity(
                entity_id=r.get("entity_id"),
                entity_type=r.get("entity_type") or "user",
                instance_id=r.get("instance_id"),
                entity_name=r.get("entity_name"),
                aliases=_jload(r.get("aliases")) or [],
                entity_description=r.get("entity_description"),
                identity_info=_jload(r.get("identity_info")) or {},
                contact_info=_jload(r.get("contact_info")) or {},
                familiarity=r.get("familiarity") or "known_of",
                relationship_strength=r.get("relationship_strength") or 0.0,
                interaction_count=r.get("interaction_count") or 0,
                last_interaction_time=r.get("last_interaction_time"),
                keywords=_jload(r.get("tags")) or [],
                expertise_domains=_jload(r.get("expertise_domains")) or [],
                related_job_ids=_jload(r.get("related_job_ids")) or [],
                persona=r.get("persona"),
                extra_data=_jload(r.get("extra_data")) or {},
            )
            await SocialNetworkRepository(db, agent_id).save_entity(entity)
            migrated += 1
        except Exception as e:  # noqa: BLE001 — one bad row must not sink the rest
            logger.warning(f"migrate_legacy_entities {r.get('entity_id')} failed: {e}")

    return migrated
