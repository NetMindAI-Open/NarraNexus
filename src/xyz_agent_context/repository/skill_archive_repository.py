"""
@file_name: skill_archive_repository.py
@author: NetMind.AI
@date: 2026-05-08
@description: SkillArchive repository

Subproject 2: Bundle Export/Import — registers each installed skill with a reproducible source.
"""

from typing import Dict, Any, List, Optional
from loguru import logger

from .base import BaseRepository
from xyz_agent_context.schema.skill_archive_schema import SkillArchive


class SkillArchiveRepository(BaseRepository[SkillArchive]):
    table_name = "skill_archives"
    id_field = "id"

    async def upsert(
        self,
        user_id: str,
        skill_name: str,
        source_type: str,
        sha256: str,
        source_url: Optional[str] = None,
        archive_path: Optional[str] = None,
    ) -> SkillArchive:
        existing = await self._db.get_one(
            self.table_name, {"user_id": user_id, "skill_name": skill_name}
        )
        payload = {
            "user_id": user_id,
            "skill_name": skill_name,
            "source_type": source_type,
            "source_url": source_url,
            "archive_path": archive_path,
            "sha256": sha256,
        }
        if existing:
            await self._db.update(
                self.table_name,
                {"user_id": user_id, "skill_name": skill_name},
                payload,
            )
        else:
            await self._db.insert(self.table_name, payload)
        row = await self._db.get_one(
            self.table_name, {"user_id": user_id, "skill_name": skill_name}
        )
        return self._row_to_entity(row) if row else SkillArchive(**payload)

    async def get(self, user_id: str, skill_name: str) -> Optional[SkillArchive]:
        row = await self._db.get_one(
            self.table_name, {"user_id": user_id, "skill_name": skill_name}
        )
        return self._row_to_entity(row) if row else None

    async def list_for_user(self, user_id: str) -> List[SkillArchive]:
        rows = await self._db.get(self.table_name, {"user_id": user_id})
        return [self._row_to_entity(r) for r in rows]

    async def remove(self, user_id: str, skill_name: str) -> int:
        return await self._db.delete(
            self.table_name, {"user_id": user_id, "skill_name": skill_name}
        )

    def _row_to_entity(self, row: Dict[str, Any]) -> SkillArchive:
        return SkillArchive(
            id=row.get("id"),
            user_id=row["user_id"],
            skill_name=row["skill_name"],
            source_type=row["source_type"],
            source_url=row.get("source_url"),
            archive_path=row.get("archive_path"),
            sha256=row["sha256"],
            created_at=row.get("created_at"),
        )

    def _entity_to_row(self, entity: SkillArchive) -> Dict[str, Any]:
        return {
            "user_id": entity.user_id,
            "skill_name": entity.skill_name,
            "source_type": entity.source_type,
            "source_url": entity.source_url,
            "archive_path": entity.archive_path,
            "sha256": entity.sha256,
        }
