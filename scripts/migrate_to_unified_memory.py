"""
@file_name: migrate_to_unified_memory.py
@author: NetMind.AI
@date: 2026-06-03
@description: One-shot migration of legacy memory tables → the unified
             memory_* tables (design §12). Idempotent (deterministic
             record_ids → upsert), cross-dialect (uses the AsyncDatabaseClient),
             and SAFETY-GUARDED: refuses to run unless DATABASE_URL points at a
             copy (path must contain "memrefactor"), so the real DB is never
             touched. Drops all embedding columns; backfills bi-temporal
             (created_at from source, valid/invalid left open).

Run:
    DATABASE_URL="sqlite:////…/nexus_memrefactor.db" uv run python -m scripts.migrate_to_unified_memory
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from xyz_agent_context.memory import MemoryEngine, MemoryRecord, SCOPE_AGENT, SCOPE_NARRATIVE, SCOPE_USER
from xyz_agent_context.memory.record import _parse_dt
from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils.schema_registry import auto_migrate


def _guard() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if "memrefactor" not in url:
        raise SystemExit(
            f"REFUSING to migrate: DATABASE_URL must point at a *copy* "
            f"(path containing 'memrefactor'). Got: {url!r}\n"
            f"Copy first: cp ~/.narranexus/nexus.db ~/.narranexus/nexus_memrefactor.db"
        )


def _j(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def _rid(prefix: str, source_id: str) -> str:
    """Deterministic record_id → re-running upserts the same row (idempotent)."""
    return f"mem_{prefix}_{source_id}"[:64]


async def _instance_to_agent(db) -> Dict[str, str]:
    rows = await db.get("module_instances")
    return {r["instance_id"]: r["agent_id"] for r in rows if r.get("agent_id")}


async def migrate_entities(db, inst2agent: Dict[str, str]) -> int:
    rows = await db.get("instance_social_entities")
    n = 0
    for r in rows:
        agent_id = inst2agent.get(r.get("instance_id"))
        if not agent_id:
            continue
        name = r.get("entity_name") or ""
        rec = MemoryRecord(
            record_id=_rid("ent", r["entity_id"]),
            agent_id=agent_id, scope_type=SCOPE_AGENT, kind="entity",
            subtype=r.get("entity_type") or "user",
            content_text=r.get("entity_description") or "",
            attributes={
                "name": name,
                "aliases": _j(r.get("aliases"), []),
                "identity_info": _j(r.get("identity_info"), {}),
                "contact_info": _j(r.get("contact_info"), {}),
                "persona": r.get("persona") or "",
                "familiarity": r.get("familiarity"),
            },
            tags=list(dict.fromkeys(_j(r.get("tags"), []) + ([f"entity:{name.lower()}"] if name else []))),
            proof_count=int(r.get("interaction_count") or 1) or 1,
            created_at=_parse_dt(r.get("created_at")),
        )
        await _upsert(db, agent_id, "entity", rec)
        n += 1
    return n


async def migrate_events(db) -> int:
    rows = await db.get("events")
    n = 0
    for r in rows:
        agent_id = r.get("agent_id")
        if not agent_id:
            continue
        env = _j(r.get("env_context"), {})
        user_in = env.get("input", "") if isinstance(env, dict) else ""
        content = "\n".join(p for p in (user_in, r.get("final_output") or "") if p).strip()
        rec = MemoryRecord(
            record_id=_rid("evt", r["event_id"]),
            agent_id=agent_id,
            scope_type=SCOPE_NARRATIVE if r.get("narrative_id") else SCOPE_AGENT,
            scope_id=r.get("narrative_id") or "",
            kind="event", content_text=content,
            attributes={"trigger": r.get("trigger"), "state": r.get("state")},
            source_ids=[r["event_id"]],
            created_at=_parse_dt(r.get("created_at")),
        )
        await _upsert(db, agent_id, "event", rec)
        n += 1
    return n


async def migrate_narratives(db) -> int:
    rows = await db.get("narratives")
    n = 0
    for r in rows:
        agent_id = r.get("agent_id")
        if not agent_id:
            continue
        info = _j(r.get("narrative_info"), {})
        summary = info.get("current_summary") or info.get("description") or r.get("topic_hint") or ""
        rec = MemoryRecord(
            record_id=_rid("nar", r["narrative_id"]),
            agent_id=agent_id, scope_type=SCOPE_AGENT, scope_id=r["narrative_id"],
            kind="narrative", content_text=summary,
            attributes={"name": info.get("name"), "is_special": r.get("is_special")},
            tags=_j(r.get("topic_keywords"), []),
            history=[{"text": e.get("summary", ""), "event_id": e.get("event_id")}
                     for e in _j(r.get("dynamic_summary"), []) if isinstance(e, dict)],
            source_ids=_j(r.get("event_ids"), []),
            created_at=_parse_dt(r.get("created_at")),
        )
        await _upsert(db, agent_id, "narrative", rec)
        n += 1
    return n


async def migrate_chat(db, inst2agent: Dict[str, str]) -> int:
    rows = await db.get("instance_json_format_memory_chat")
    n = 0
    for r in rows:
        agent_id = inst2agent.get(r.get("instance_id"))
        if not agent_id:
            continue
        blob = _j(r.get("memory"), {})
        messages = blob.get("messages", []) if isinstance(blob, dict) else []
        for idx, m in enumerate(messages):
            if not isinstance(m, dict) or not (m.get("content") or "").strip():
                continue
            meta = m.get("meta_data", {}) if isinstance(m.get("meta_data"), dict) else {}
            nid = meta.get("narrative_id") or ""
            rec = MemoryRecord(
                record_id=_rid("chat", f"{r['instance_id']}_{idx}"),
                agent_id=agent_id,
                scope_type=SCOPE_NARRATIVE if nid else SCOPE_USER,
                scope_id=nid,
                kind="chat", subtype=m.get("role"),
                content_text=m.get("content") or "",
                attributes={"role": m.get("role"), "event_id": meta.get("event_id")},
                created_at=_parse_dt(meta.get("timestamp")),
            )
            await _upsert(db, agent_id, "chat", rec)
            n += 1
    return n


# Engine cache per agent (repos are lazy; reuse across records of same agent)
_engines: Dict[str, MemoryEngine] = {}


async def _upsert(db, agent_id: str, kind: str, rec: MemoryRecord) -> None:
    eng = _engines.get(agent_id)
    if eng is None:
        eng = MemoryEngine(db, agent_id)
        _engines[agent_id] = eng
    # Migration writes are plain upserts — no dedup/consolidation side-effects.
    await eng.repo(kind).upsert(rec)


async def main() -> None:
    _guard()
    db = await get_db_client()
    await auto_migrate(db._backend)
    inst2agent = await _instance_to_agent(db)
    logger.info(f"[migrate] {len(inst2agent)} instance→agent mappings")

    counts = {
        "entity": await migrate_entities(db, inst2agent),
        "event": await migrate_events(db),
        "narrative": await migrate_narratives(db),
        "chat": await migrate_chat(db, inst2agent),
    }

    # Reconcile: report migrated counts vs unified-table counts.
    for kind, migrated in counts.items():
        rows = await db.get(f"memory_{kind}")
        logger.info(f"[migrate] {kind}: migrated {migrated} → memory_{kind} now has {len(rows)} rows")
    await db.close()
    print("MIGRATION_DONE", counts)


if __name__ == "__main__":
    asyncio.run(main())
