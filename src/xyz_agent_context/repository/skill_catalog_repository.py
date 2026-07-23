"""
@file_name: skill_catalog_repository.py
@author: NetMind.AI
@date: 2026-07-20
@description: Marketplace skill catalog repository (cloud-authoritative directory).

One row per (skill_id, version). JSON payload fields are stored compact
(no whitespace) so capability/tag filters can use reliable LIKE substring
matching on the serialized form. Search operates at "one card per skill"
granularity: it returns only the latest published version of each skill_id.
"""

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseRepository
from xyz_agent_context.schema.skill_marketplace_schema import SkillCatalogEntry

_COMPACT = {"separators": (",", ":"), "ensure_ascii": False}


def _semver_key(version: str) -> Tuple:
    """Sortable key for semver-ish strings; non-numeric parts sort lowest."""
    numbers = re.findall(r"\d+", version.split("-")[0].split("+")[0])
    return tuple(int(n) for n in numbers[:3]) + (0,) * (3 - len(numbers[:3]))


class SkillCatalogRepository(BaseRepository[SkillCatalogEntry]):
    table_name = "skill_catalog"
    id_field = "id"

    async def publish(self, entry: SkillCatalogEntry) -> SkillCatalogEntry:
        """Insert or update one (skill_id, version) row."""
        filters = {"skill_id": entry.skill_id, "version": entry.version}
        payload = self._entity_to_row(entry)
        existing = await self._db.get_one(self.table_name, filters)
        if existing:
            payload.pop("downloads", None)  # never reset the counter on re-publish
            await self._db.update(self.table_name, filters, payload)
        else:
            await self._db.insert(self.table_name, payload)
        row = await self._db.get_one(self.table_name, filters)
        return self._row_to_entity(row)

    async def get_version(self, skill_id: str, version: str) -> Optional[SkillCatalogEntry]:
        row = await self._db.get_one(
            self.table_name, {"skill_id": skill_id, "version": version}
        )
        return self._row_to_entity(row) if row else None

    async def list_versions(self, skill_id: str) -> List[SkillCatalogEntry]:
        rows = await self._db.get(self.table_name, {"skill_id": skill_id})
        entries = [self._row_to_entity(r) for r in rows]
        entries.sort(key=lambda e: _semver_key(e.version), reverse=True)
        return entries

    async def get_latest(self, skill_id: str) -> Optional[SkillCatalogEntry]:
        published = [
            e for e in await self.list_versions(skill_id) if e.status == "published"
        ]
        return published[0] if published else None

    async def search(
        self,
        q: Optional[str] = None,
        category: Optional[str] = None,
        capability: Optional[str] = None,
        tags: Optional[List[str]] = None,
        sort: str = "downloads",
        page: int = 1,
        limit: int = 20,
    ) -> Tuple[List[SkillCatalogEntry], int]:
        """Return (latest-version cards matching the filters, total match count)."""
        conditions = ["status = %s"]
        params: List[Any] = ["published"]
        if category:
            conditions.append("category = %s")
            params.append(category)
        if q:
            like = f"%{q}%"
            conditions.append("(name LIKE %s OR description LIKE %s OR tags_json LIKE %s)")
            params.extend([like, like, like])
        if capability:
            conditions.append("capabilities_json LIKE %s")
            params.append(f'%"{capability}"%')
        for tag in tags or []:
            conditions.append("tags_json LIKE %s")
            params.append(f'%"{tag}"%')

        rows = await self._db.execute(
            f"SELECT * FROM {self.table_name} WHERE {' AND '.join(conditions)}",
            tuple(params),
        )

        latest_by_skill: Dict[str, SkillCatalogEntry] = {}
        for row in rows:
            entry = self._row_to_entity(row)
            current = latest_by_skill.get(entry.skill_id)
            if current is None or _semver_key(entry.version) > _semver_key(current.version):
                latest_by_skill[entry.skill_id] = entry

        items = list(latest_by_skill.values())
        if sort == "name":
            items.sort(key=lambda e: e.name.lower())
        elif sort == "published":
            items.sort(key=lambda e: e.published_at or datetime.min, reverse=True)
        else:
            items.sort(key=lambda e: e.downloads, reverse=True)

        total = len(items)
        start = max(page - 1, 0) * limit
        return items[start : start + limit], total

    async def list_defaults(self) -> List[SkillCatalogEntry]:
        """Latest published version of every skill flagged is_default."""
        rows = await self._db.execute(
            f"SELECT * FROM {self.table_name} WHERE is_default = 1 AND status = %s",
            ("published",),
        )
        latest: Dict[str, SkillCatalogEntry] = {}
        for row in rows:
            entry = self._row_to_entity(row)
            current = latest.get(entry.skill_id)
            if current is None or _semver_key(entry.version) > _semver_key(current.version):
                latest[entry.skill_id] = entry
        return sorted(latest.values(), key=lambda e: e.skill_id)

    async def increment_downloads(self, skill_id: str, version: str) -> None:
        await self._db.execute(
            f"UPDATE {self.table_name} SET downloads = downloads + 1 "
            "WHERE skill_id = %s AND version = %s",
            (skill_id, version),
            fetch=False,
        )

    def _row_to_entity(self, row: Dict[str, Any]) -> SkillCatalogEntry:
        def loads(key: str, fallback):
            raw = row.get(key)
            if not raw:
                return fallback
            try:
                return json.loads(raw)
            except (ValueError, TypeError):
                return fallback

        return SkillCatalogEntry(
            id=row.get("id"),
            skill_id=row["skill_id"],
            version=row["version"],
            name=row["name"],
            description=row.get("description"),
            author=loads("author_json", None),
            license=row.get("license"),
            category=row.get("category"),
            capabilities=loads("capabilities_json", []),
            tags=loads("tags_json", []),
            config_schema=loads("config_schema_json", None),
            dependencies=loads("dependencies_json", {}),
            compatibility=loads("compatibility_json", None),
            s3_key=row["s3_key"],
            package_hash=row["package_hash"],
            publisher=row.get("publisher"),
            scan_status=row["scan_status"],
            status=row["status"],
            downloads=row.get("downloads") or 0,
            is_default=bool(row.get("is_default")),
            avg_rating=row.get("avg_rating"),
            published_at=row.get("published_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def _entity_to_row(self, entity: SkillCatalogEntry) -> Dict[str, Any]:
        return {
            "skill_id": entity.skill_id,
            "version": entity.version,
            "name": entity.name,
            "description": entity.description,
            "author_json": json.dumps(entity.author, **_COMPACT) if entity.author else None,
            "license": entity.license,
            "category": entity.category,
            "capabilities_json": json.dumps(entity.capabilities, **_COMPACT),
            "tags_json": json.dumps(entity.tags, **_COMPACT),
            "config_schema_json": (
                json.dumps(entity.config_schema, **_COMPACT) if entity.config_schema else None
            ),
            "dependencies_json": json.dumps(entity.dependencies, **_COMPACT),
            "compatibility_json": (
                json.dumps(entity.compatibility, **_COMPACT) if entity.compatibility else None
            ),
            "s3_key": entity.s3_key,
            "package_hash": entity.package_hash,
            "publisher": entity.publisher,
            "scan_status": entity.scan_status,
            "status": entity.status,
            "downloads": entity.downloads,
            "is_default": 1 if entity.is_default else 0,
            "avg_rating": entity.avg_rating,
            "published_at": (
                entity.published_at.strftime("%Y-%m-%d %H:%M:%S")
                if entity.published_at
                else None
            ),
        }
