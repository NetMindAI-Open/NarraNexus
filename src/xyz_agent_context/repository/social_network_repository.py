"""
@file_name: social_network_repository.py
@author: NetMind.AI
@date: 2025-12-02
@description: Social Network Repository — entity data access over the UNIFIED memory engine.

Refactor (2026-06-08, unified-memory overhaul task 1):
- Entities now live in ONE home: the engine's ``memory_entity`` table. The
  legacy ``instance_social_entities`` table is retired (tombstoned in
  schema_registry). This kills the old "source table + incomplete mirror"
  dual-write that made third-party / un-touched entities invisible to
  ``remember`` (see TODO-unified-memory-overhaul.md §2-P0).
- This class keeps its public surface (get/add/update/search/...) and the
  ``SocialNetworkEntity`` domain object, but every method now maps that object
  to/from a ``MemoryRecord`` and operates on ``MemoryRepository("entity")``.

Mapping (SocialNetworkEntity ↔ MemoryRecord):
- record_id   : deterministic from (instance_id, entity_id) → stable get/upsert
- scope        : scope_type="instance", scope_id=instance_id (instance isolation)
- subtype      : entity_type (user|agent|group)
- content_text : DERIVED searchable blob (name + aliases + description + keywords)
                 so unified recall/grep can find an entity by NAME, not just
                 by description text.
- tags         : keywords (also feeds tags_any filtering)
- attributes   : the structured truth (entity_id, name, aliases, identity_info,
                 contact_info, familiarity, persona, related_job_ids, ...).
                 `_record_to_entity` reconstructs the entity from here, never
                 from content_text.

`agent_id` is required only for WRITES of NEW rows (so unified `remember`, which
recalls by agent_id, can find the entity). Read/search/related-job-id-update
paths don't need it — they filter by scope_id or re-upsert a loaded record.
"""

import hashlib
import json
from typing import Any, Dict, List, Optional

from loguru import logger

from xyz_agent_context.memory._memory_impl.repository import MemoryRepository
from xyz_agent_context.memory.record import MemoryRecord, SCOPE_INSTANCE
from xyz_agent_context.schema import SocialNetworkEntity
from xyz_agent_context.utils.timezone import utc_now

_KIND = "entity"


def _record_id(instance_id: str, entity_id: str) -> str:
    """Deterministic, collision-safe record id for (instance, entity)."""
    h = hashlib.sha1(f"{instance_id}\x00{entity_id}".encode("utf-8")).hexdigest()[:24]
    return f"mem_ent_{h}"


def _compose_text(name: Optional[str], description: Optional[str],
                  aliases: List[str], keywords: List[str]) -> str:
    """Build the searchable content_text: name + aliases first so an entity is
    findable by name/alias via BM25/grep, then the description, then keywords."""
    parts: List[str] = []
    if name:
        parts.append(name)
    if aliases:
        parts.append(" ".join(a for a in aliases if a))
    if description:
        parts.append(description)
    if keywords:
        parts.append(" ".join(k for k in keywords if k))
    return "\n".join(p for p in parts if p)


class SocialNetworkRepository:
    """Social entity store, backed by the unified memory engine (kind=entity).

    NOT a BaseRepository anymore — it adapts SocialNetworkEntity onto
    MemoryRepository. Public method signatures are unchanged so callers don't
    move; the only addition is the optional ``agent_id`` constructor arg, which
    write paths supply and read paths can omit.
    """

    def __init__(self, db_client: Any, agent_id: Optional[str] = None):
        self._db = db_client
        self._agent_id = agent_id
        self._mem = MemoryRepository(_KIND, db_client)

    # ── mapping ──────────────────────────────────────────────────────────────
    def _entity_to_record(self, entity: SocialNetworkEntity, *, agent_id: str) -> MemoryRecord:
        lit = entity.last_interaction_time
        attrs: Dict[str, Any] = {
            "entity_id": entity.entity_id,
            "entity_name": entity.entity_name,
            "aliases": list(entity.aliases or []),
            "entity_description": entity.entity_description,
            "identity_info": dict(entity.identity_info or {}),
            "contact_info": dict(entity.contact_info or {}),
            "familiarity": entity.familiarity,
            "relationship_strength": entity.relationship_strength,
            "interaction_count": entity.interaction_count,
            "last_interaction_time": lit.isoformat() if hasattr(lit, "isoformat") else lit,
            "expertise_domains": list(entity.expertise_domains or []),
            "related_job_ids": list(entity.related_job_ids or []),
            "persona": entity.persona,
            "extra_data": dict(entity.extra_data or {}),
        }
        return MemoryRecord(
            record_id=_record_id(entity.instance_id, entity.entity_id),
            agent_id=agent_id,
            scope_type=SCOPE_INSTANCE,
            scope_id=entity.instance_id,
            kind=_KIND,
            subtype=entity.entity_type,
            content_text=_compose_text(
                entity.entity_name, entity.entity_description,
                entity.aliases or [], entity.keywords or [],
            ),
            tags=list(entity.keywords or []),
            attributes=attrs,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    def _record_to_entity(self, rec: MemoryRecord) -> SocialNetworkEntity:
        a = rec.attributes or {}
        return SocialNetworkEntity(
            instance_id=rec.scope_id or "",
            entity_id=a.get("entity_id") or "",
            entity_type=rec.subtype or "user",
            entity_name=a.get("entity_name"),
            aliases=a.get("aliases") or [],
            entity_description=a.get("entity_description"),
            identity_info=a.get("identity_info") or {},
            contact_info=a.get("contact_info") or {},
            familiarity=a.get("familiarity") or "known_of",
            relationship_strength=a.get("relationship_strength") or 0.0,
            interaction_count=a.get("interaction_count") or 0,
            last_interaction_time=a.get("last_interaction_time"),
            keywords=list(rec.tags or a.get("keywords") or []),
            expertise_domains=a.get("expertise_domains") or [],
            related_job_ids=a.get("related_job_ids") or [],
            persona=a.get("persona"),
            extra_data=a.get("extra_data") or {},
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        )

    async def _scope_records(self, instance_id: str) -> List[MemoryRecord]:
        """All live entity records for an instance (newest first)."""
        records = await self._mem.find(
            {"scope_type": SCOPE_INSTANCE, "scope_id": instance_id},
            order_by="created_at DESC",
        )
        return [r for r in records if r.is_live]

    # ── reads ────────────────────────────────────────────────────────────────
    async def get_entity(self, entity_id: str, instance_id: str) -> Optional[SocialNetworkEntity]:
        logger.debug(f"    → SocialNetworkRepository.get_entity({entity_id}, {instance_id})")
        rows = await self._mem.find({"record_id": _record_id(instance_id, entity_id)}, limit=1)
        rec = rows[0] if rows else None
        if rec is None or not rec.is_live:
            return None
        return self._record_to_entity(rec)

    async def get_all_entities(self, instance_id: str, entity_type: Optional[str] = None,
                               limit: int = 100) -> List[SocialNetworkEntity]:
        logger.debug(f"    → SocialNetworkRepository.get_all_entities({instance_id})")
        out = [self._record_to_entity(r) for r in await self._scope_records(instance_id)]
        if entity_type:
            out = [e for e in out if e.entity_type == entity_type]
        return out[:limit]

    async def keyword_search(self, instance_id: str, keyword: str,
                             limit: int = 10) -> List[SocialNetworkEntity]:
        """Fuzzy match over name / description / keywords / aliases (LIKE-style,
        case-insensitive) — same surface as before, now over content_text +
        structured fields of the unified entity records."""
        logger.debug(f"    → SocialNetworkRepository.keyword_search({instance_id}, '{keyword}')")
        kw = (keyword or "").lower()
        if not kw:
            return []
        out: List[SocialNetworkEntity] = []
        for r in await self._scope_records(instance_id):
            e = self._record_to_entity(r)
            hay = " ".join(filter(None, [
                e.entity_name or "", e.entity_description or "",
                " ".join(e.keywords or []), " ".join(e.aliases or []),
            ])).lower()
            if kw in hay:
                out.append(e)
            if len(out) >= limit:
                break
        return out

    async def search_by_tags(self, instance_id: str, tag: str,
                             limit: int = 10) -> List[SocialNetworkEntity]:
        logger.debug(f"    → SocialNetworkRepository.search_by_tags({instance_id}, '{tag}')")
        out: List[SocialNetworkEntity] = []
        for r in await self._scope_records(instance_id):
            if tag in (r.tags or []):
                out.append(self._record_to_entity(r))
            if len(out) >= limit:
                break
        return out

    async def search_by_name_or_alias(self, instance_id: str, name: str,
                                      limit: int = 10) -> List[SocialNetworkEntity]:
        """Exact (case-insensitive) match on entity_name or any alias — used by
        the dedup pipeline to find existing entities that may be the same."""
        logger.debug(f"    → SocialNetworkRepository.search_by_name_or_alias({instance_id}, '{name}')")
        target = (name or "").strip().lower()
        if not target:
            return []
        out: List[SocialNetworkEntity] = []
        for r in await self._scope_records(instance_id):
            e = self._record_to_entity(r)
            names = [e.entity_name or ""] + list(e.aliases or [])
            if any((n or "").strip().lower() == target for n in names):
                out.append(e)
            if len(out) >= limit:
                break
        return out

    # ── writes ───────────────────────────────────────────────────────────────
    async def add_entity(self, entity_id: str, entity_type: str, instance_id: str,
                         entity_name: Optional[str] = None, aliases: Optional[List[str]] = None,
                         entity_description: Optional[str] = None,
                         identity_info: Optional[Dict[str, Any]] = None,
                         contact_info: Optional[Dict[str, Any]] = None,
                         keywords: Optional[List[str]] = None,
                         expertise_domains: Optional[List[str]] = None,
                         familiarity: str = "known_of") -> int:
        """Create (or overwrite) an entity record. Requires the repo to carry an
        ``agent_id`` (unified recall finds entities by agent_id)."""
        logger.debug(f"    → SocialNetworkRepository.add_entity({entity_id})")
        if not self._agent_id:
            raise ValueError(
                "SocialNetworkRepository.add_entity needs an agent_id — construct "
                "SocialNetworkRepository(db, agent_id) on write paths."
            )
        now = utc_now()
        entity = SocialNetworkEntity(
            entity_id=entity_id, entity_type=entity_type, instance_id=instance_id,
            entity_name=entity_name, aliases=aliases or [],
            entity_description=entity_description, identity_info=identity_info or {},
            contact_info=contact_info or {}, keywords=keywords or [],
            expertise_domains=expertise_domains or [], familiarity=familiarity,
            relationship_strength=0.0, interaction_count=0,
            created_at=now, updated_at=now,
        )
        await self._mem.upsert(self._entity_to_record(entity, agent_id=self._agent_id))
        return 1

    async def save_entity(self, entity: SocialNetworkEntity) -> int:
        """Upsert a FULL SocialNetworkEntity (all fields incl. persona /
        related_job_ids / interaction stats). Used by bundle import + the
        unified-memory migration, where the complete entity must round-trip —
        unlike `add_entity`, which only takes the create-time subset. Requires
        the repo to carry an agent_id."""
        if not self._agent_id:
            raise ValueError("SocialNetworkRepository.save_entity needs an agent_id.")
        await self._mem.upsert(self._entity_to_record(entity, agent_id=self._agent_id))
        return 1

    async def update_entity_info(self, entity_id: str, instance_id: str,
                                 updates: Dict[str, Any]) -> int:
        """Patch fields on an existing entity. `updates` keys are
        SocialNetworkEntity field names (entity_name, entity_description, tags→
        keywords, contact_info, persona, ...)."""
        logger.debug(f"    → SocialNetworkRepository.update_entity_info({entity_id})")
        if not updates:
            return 0
        existing = await self.get_entity(entity_id, instance_id)
        if not existing:
            logger.warning(f"Entity {entity_id} not found, skipping update")
            return 0
        # DB column 'tags' is the Python 'keywords' field — accept either name.
        if "tags" in updates and "keywords" not in updates:
            updates = {**updates, "keywords": updates.pop("tags")}
        data = existing.model_dump()
        for k, v in updates.items():
            if k in data:
                data[k] = v
        data["updated_at"] = utc_now()
        merged = SocialNetworkEntity(**data)
        rec = await self._load_record(entity_id, instance_id)
        agent_id = rec.agent_id if rec else (self._agent_id or "")
        await self._mem.upsert(self._entity_to_record(merged, agent_id=agent_id))
        return 1

    async def increment_interaction(self, entity_id: str, instance_id: str) -> int:
        logger.debug(f"    → SocialNetworkRepository.increment_interaction({entity_id})")
        existing = await self.get_entity(entity_id, instance_id)
        if not existing:
            return 0
        return await self.update_entity_info(entity_id, instance_id, {
            "interaction_count": (existing.interaction_count or 0) + 1,
            "last_interaction_time": utc_now(),
        })

    async def append_related_job_ids(self, entity_id: str, instance_id: str,
                                     job_ids: List[str]) -> int:
        logger.debug(f"    → SocialNetworkRepository.append_related_job_ids({entity_id}, {job_ids})")
        if not job_ids:
            return 0
        existing = await self.get_entity(entity_id, instance_id)
        if not existing:
            logger.warning(f"Entity {entity_id} not found, skipping append")
            return 0
        merged = list(dict.fromkeys(list(existing.related_job_ids) + list(job_ids)))
        return await self.update_entity_info(entity_id, instance_id, {"related_job_ids": merged})

    async def remove_related_job_ids(self, entity_id: str, instance_id: str,
                                    job_ids: List[str]) -> int:
        logger.debug(f"    → SocialNetworkRepository.remove_related_job_ids({entity_id}, {job_ids})")
        if not job_ids:
            return 0
        existing = await self.get_entity(entity_id, instance_id)
        if not existing:
            logger.warning(f"Entity {entity_id} not found, skipping remove")
            return 0
        remaining = [j for j in existing.related_job_ids if j not in set(job_ids)]
        return await self.update_entity_info(entity_id, instance_id, {"related_job_ids": remaining})

    async def delete_entity(self, entity_id: str, instance_id: str) -> int:
        """Soft delete (tombstone) — the row is kept with expired_at set, so
        history survives and `is_live` filtering hides it from reads/search."""
        logger.debug(f"    → SocialNetworkRepository.delete_entity({entity_id})")
        rec = await self._load_record(entity_id, instance_id)
        if not rec:
            return 0
        return await self._mem.tombstone(rec.record_id)

    async def _load_record(self, entity_id: str, instance_id: str) -> Optional[MemoryRecord]:
        rows = await self._mem.find({"record_id": _record_id(instance_id, entity_id)}, limit=1)
        return rows[0] if rows else None
