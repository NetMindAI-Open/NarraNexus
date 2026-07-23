"""
@file_name: team_catalog_repository.py
@author: NetMind.AI
@date: 2026-07-21
@description: Team Marketplace catalog repository (cloud-authoritative index).

One row per team template. Mirrors SkillCatalogRepository's conventions
(compact JSON for the categories list, upsert keyed on the stable id).
"""

import json
from typing import Any, Dict, List, Optional

from .base import BaseRepository
from xyz_agent_context.schema.team_marketplace_schema import TeamTemplate

_COMPACT = {"separators": (",", ":"), "ensure_ascii": False}


class TeamCatalogRepository(BaseRepository[TeamTemplate]):
    table_name = "team_catalog"
    id_field = "template_id"

    async def save_template(self, template: TeamTemplate) -> TeamTemplate:
        filters = {"template_id": template.template_id}
        payload = self._entity_to_row(template)
        existing = await self._db.get_one(self.table_name, filters)
        if existing:
            payload.pop("downloads", None)  # don't reset the counter on re-publish
            await self._db.update(self.table_name, filters, payload)
        else:
            await self._db.insert(self.table_name, payload)
        row = await self._db.get_one(self.table_name, filters)
        return self._row_to_entity(row)

    async def get(self, template_id: str) -> Optional[TeamTemplate]:
        row = await self._db.get_one(self.table_name, {"template_id": template_id})
        return self._row_to_entity(row) if row else None

    async def list_enabled(self) -> List[TeamTemplate]:
        rows = await self._db.execute(
            f"SELECT * FROM {self.table_name} WHERE enabled = 1 "
            "ORDER BY sort_order ASC, template_id ASC"
        )
        return [self._row_to_entity(r) for r in rows]

    async def list_all(self) -> List[TeamTemplate]:
        rows = await self._db.execute(
            f"SELECT * FROM {self.table_name} ORDER BY sort_order ASC, template_id ASC"
        )
        return [self._row_to_entity(r) for r in rows]

    async def remove(self, template_id: str) -> int:
        return await self._db.delete(self.table_name, {"template_id": template_id})

    async def increment_downloads(self, template_id: str) -> None:
        await self._db.execute(
            f"UPDATE {self.table_name} SET downloads = downloads + 1 WHERE template_id = %s",
            (template_id,),
            fetch=False,
        )

    def _row_to_entity(self, row: Dict[str, Any]) -> TeamTemplate:
        try:
            categories = json.loads(row.get("categories_json") or "[]")
        except (ValueError, TypeError):
            categories = []
        return TeamTemplate(
            id=row.get("id"),
            template_id=row["template_id"],
            name=row["name"],
            description=row.get("description") or "",
            categories=categories,
            author=row.get("author") or "NarraNexus team",
            agent_count=row.get("agent_count") or 1,
            thumbnail_url=row.get("thumbnail_url"),
            store_key=row.get("store_key") or "",
            bundle_sha256=row.get("bundle_sha256") or "",
            enabled=bool(row.get("enabled", 1)),
            sort_order=row.get("sort_order") or 0,
            downloads=row.get("downloads") or 0,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def _entity_to_row(self, entity: TeamTemplate) -> Dict[str, Any]:
        return {
            "template_id": entity.template_id,
            "name": entity.name,
            "description": entity.description,
            "categories_json": json.dumps(entity.categories, **_COMPACT),
            "author": entity.author,
            "agent_count": entity.agent_count,
            "thumbnail_url": entity.thumbnail_url,
            "store_key": entity.store_key,
            "bundle_sha256": entity.bundle_sha256,
            "enabled": 1 if entity.enabled else 0,
            "sort_order": entity.sort_order,
            "downloads": entity.downloads,
        }
