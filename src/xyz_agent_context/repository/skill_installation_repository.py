"""
@file_name: skill_installation_repository.py
@author: NetMind.AI
@date: 2026-07-20
@description: Audit trail of skill installs per (agent_id, user_id) workspace.

Follower of the filesystem truth (skills/ + .skill_meta.json). Rows are
upserted by the InstallPipeline and corrected by the reconciler; they are
never deleted so uninstall/removal history stays visible.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .base import BaseRepository
from xyz_agent_context.schema.skill_marketplace_schema import SkillInstallationRecord


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class SkillInstallationRepository(BaseRepository[SkillInstallationRecord]):
    table_name = "skill_installations"
    id_field = "id"

    async def upsert_event(
        self,
        agent_id: str,
        user_id: str,
        skill_id: str,
        *,
        version: Optional[str] = None,
        source_type: str,
        source_url: Optional[str] = None,
        package_hash: Optional[str] = None,
        status: str = "installed",
        last_event: str = "install",
    ) -> SkillInstallationRecord:
        filters = {"agent_id": agent_id, "user_id": user_id, "skill_id": skill_id}
        payload = {
            **filters,
            "version": version,
            "source_type": source_type,
            "source_url": source_url,
            "package_hash": package_hash,
            "status": status,
            "last_event": last_event,
            "updated_at": _now(),
        }
        existing = await self._db.get_one(self.table_name, filters)
        if existing:
            await self._db.update(self.table_name, filters, payload)
        else:
            payload["installed_at"] = _now()
            await self._db.insert(self.table_name, payload)
        row = await self._db.get_one(self.table_name, filters)
        return self._row_to_entity(row)

    async def mark_status(
        self,
        agent_id: str,
        user_id: str,
        skill_id: str,
        *,
        status: str,
        last_event: str = "reconcile",
    ) -> bool:
        """Set status on an existing row; returns False when no row exists."""
        filters = {"agent_id": agent_id, "user_id": user_id, "skill_id": skill_id}
        existing = await self._db.get_one(self.table_name, filters)
        if not existing:
            return False
        await self._db.update(
            self.table_name,
            filters,
            {"status": status, "last_event": last_event, "updated_at": _now()},
        )
        return True

    async def list_for_workspace(
        self, agent_id: str, user_id: str
    ) -> List[SkillInstallationRecord]:
        rows = await self._db.get(
            self.table_name, {"agent_id": agent_id, "user_id": user_id}
        )
        return [self._row_to_entity(r) for r in rows]

    def _row_to_entity(self, row: Dict[str, Any]) -> SkillInstallationRecord:
        return SkillInstallationRecord(
            id=row.get("id"),
            agent_id=row["agent_id"],
            user_id=row["user_id"],
            skill_id=row["skill_id"],
            version=row.get("version"),
            source_type=row["source_type"],
            source_url=row.get("source_url"),
            package_hash=row.get("package_hash"),
            status=row["status"],
            last_event=row.get("last_event"),
            installed_at=row.get("installed_at"),
            updated_at=row.get("updated_at"),
        )

    def _entity_to_row(self, entity: SkillInstallationRecord) -> Dict[str, Any]:
        return {
            "agent_id": entity.agent_id,
            "user_id": entity.user_id,
            "skill_id": entity.skill_id,
            "version": entity.version,
            "source_type": entity.source_type,
            "source_url": entity.source_url,
            "package_hash": entity.package_hash,
            "status": entity.status,
            "last_event": entity.last_event,
            "installed_at": entity.installed_at,
        }
