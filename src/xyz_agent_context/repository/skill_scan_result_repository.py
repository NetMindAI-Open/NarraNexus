"""
@file_name: skill_scan_result_repository.py
@author: NetMind.AI
@date: 2026-07-20
@description: Append-only store of security-scan runs per skill version.

The same version may be scanned multiple times (scanner rule upgrades trigger
re-scans); consumers read the newest row via latest_for().
"""

import json
from typing import Any, Dict, Optional

from .base import BaseRepository
from xyz_agent_context.schema.skill_marketplace_schema import SkillScanResult


class SkillScanResultRepository(BaseRepository[SkillScanResult]):
    table_name = "skill_scan_results"
    id_field = "id"

    async def record(self, result: SkillScanResult) -> SkillScanResult:
        await self._db.insert(self.table_name, self._entity_to_row(result))
        latest = await self.latest_for(result.skill_id, result.version)
        return latest if latest else result

    async def latest_for(self, skill_id: str, version: str) -> Optional[SkillScanResult]:
        rows = await self._db.execute(
            f"SELECT * FROM {self.table_name} "
            "WHERE skill_id = %s AND version = %s ORDER BY id DESC LIMIT 1",
            (skill_id, version),
        )
        return self._row_to_entity(rows[0]) if rows else None

    def _row_to_entity(self, row: Dict[str, Any]) -> SkillScanResult:
        try:
            issues = json.loads(row.get("issues_json") or "[]")
        except (ValueError, TypeError):
            issues = []
        return SkillScanResult(
            id=row.get("id"),
            skill_id=row["skill_id"],
            version=row["version"],
            status=row["status"],
            high_issues=row.get("high_issues") or 0,
            low_issues=row.get("low_issues") or 0,
            issues=issues,
            scanner_version=row.get("scanner_version"),
            scanned_at=row.get("scanned_at"),
        )

    def _entity_to_row(self, entity: SkillScanResult) -> Dict[str, Any]:
        return {
            "skill_id": entity.skill_id,
            "version": entity.version,
            "status": entity.status,
            "high_issues": entity.high_issues,
            "low_issues": entity.low_issues,
            "issues_json": json.dumps(entity.issues, separators=(",", ":"), ensure_ascii=False),
            "scanner_version": entity.scanner_version,
        }
