"""
@file_name: skill_sync_service.py
@author: NetMind.AI
@date: 2026-07-21
@description: Reconciler keeping skill_installations (audit DB) in sync with
the filesystem truth (skills/ + .skill_meta.json).

Users cannot be prevented from hand-editing skills/ (rm -rf, cp -r, manual
edits), so the audit table is a FOLLOWER: this service only ever writes DB
rows and never touches user files. Idempotent — every pass computes the
target state and upserts it.

Drift handling per workspace skill:
- on disk, no DB row              -> upsert (source from .skill_meta.json,
                                     else "manual")
- in DB "installed", not on disk  -> status = external_removed (row kept)
- content hash differs from meta  -> status = modified (unmanaged edit)
- under skills/.disabled/         -> status = disabled
- back on disk after removal      -> status returns to installed

Triggers: run_forever loops on a fixed interval (SKILL_SYNC_INTERVAL_SECONDS,
default 1800), doing its FIRST reconcile immediately (so backend startup does
not block on it — the lifespan launches this as a background task). Setting
the interval to 0 (or below) DISABLES the reconciler entirely — no periodic
loop AND no startup pass; tests use this to opt out. Lifecycle events go to
the log (started/healed/error per pass — incident lesson #4/#5).
"""

import asyncio
import os
from pathlib import Path
from typing import Dict, Optional

from loguru import logger

from xyz_agent_context._skill_marketplace_impl.install_pipeline import compute_content_hash
from xyz_agent_context.repository.skill_installation_repository import (
    SkillInstallationRepository,
)

DEFAULT_INTERVAL_SECONDS = 1800


class SkillSyncService:
    def __init__(self, db_client):
        self.repo = SkillInstallationRepository(db_client)

    # -- single workspace ----------------------------------------------------

    async def reconcile_workspace(self, agent_id: str, user_id: str) -> Dict[str, int]:
        """Reconcile one workspace; returns counters for observability."""
        from xyz_agent_context.module.skill_module import SkillModule

        module = SkillModule(agent_id=agent_id, user_id=user_id)
        stats = {"added": 0, "external_removed": 0, "modified": 0, "disabled": 0, "restored": 0}
        skills_dir = module.skills_dir
        if skills_dir is None:
            return stats

        disk_state: Dict[str, str] = {}  # name -> target status
        disk_meta: Dict[str, dict] = {}

        if skills_dir.exists():
            for path in skills_dir.iterdir():
                if path.is_dir() and not path.name.startswith("."):
                    disk_state[path.name] = self._live_status(module, path)
                    disk_meta[path.name] = module.read_skill_meta(path.name)
            disabled_dir = skills_dir / ".disabled"
            if disabled_dir.exists():
                for path in disabled_dir.iterdir():
                    if path.is_dir():
                        disk_state.setdefault(path.name, "disabled")

        rows = {r.skill_id: r for r in await self.repo.list_for_workspace(agent_id, user_id)}

        for name, target_status in disk_state.items():
            row = rows.get(name)
            if row is None:
                meta = disk_meta.get(name, {})
                await self.repo.upsert_event(
                    agent_id,
                    user_id,
                    name,
                    version=meta.get("version"),
                    source_type=meta.get("source_type") or "manual",
                    source_url=meta.get("source_url"),
                    package_hash=meta.get("hash"),
                    status=target_status,
                    last_event="reconcile",
                )
                stats["added"] += 1
            elif row.status != target_status:
                await self.repo.mark_status(
                    agent_id, user_id, name, status=target_status, last_event="reconcile"
                )
                key = {
                    "modified": "modified",
                    "disabled": "disabled",
                    "installed": "restored",
                }.get(target_status, "restored")
                stats[key] += 1

        for name, row in rows.items():
            if name not in disk_state and row.status not in ("uninstalled", "external_removed"):
                await self.repo.mark_status(
                    agent_id, user_id, name, status="external_removed", last_event="reconcile"
                )
                stats["external_removed"] += 1

        return stats

    @staticmethod
    def _live_status(module, skill_dir: Path) -> str:
        meta = module.read_skill_meta(skill_dir.name)
        recorded_hash = meta.get("content_hash")
        if recorded_hash:
            try:
                if compute_content_hash(skill_dir) != recorded_hash:
                    return "modified"
            except OSError as exc:
                logger.warning(f"Reconcile: cannot hash {skill_dir}: {exc}")
        return "installed"

    # -- whole host ----------------------------------------------------------

    async def reconcile_all(self) -> Dict[str, int]:
        """Walk every workspace under base_working_path (nested layout
        {user_id}/{agent_id}) and reconcile those that have a skills dir."""
        from xyz_agent_context.settings import settings

        totals = {"workspaces": 0, "added": 0, "external_removed": 0,
                  "modified": 0, "disabled": 0, "restored": 0}
        base = Path(settings.base_working_path)
        if not base.exists():
            return totals

        for user_dir in base.iterdir():
            if not user_dir.is_dir() or user_dir.name.startswith("."):
                continue
            for agent_dir in user_dir.iterdir():
                if not agent_dir.is_dir() or agent_dir.name.startswith("."):
                    continue
                if not (agent_dir / "skills").exists():
                    continue
                try:
                    stats = await self.reconcile_workspace(agent_dir.name, user_dir.name)
                except Exception as exc:
                    logger.error(
                        f"Reconcile failed for workspace {user_dir.name}/{agent_dir.name}: {exc}"
                    )
                    continue
                totals["workspaces"] += 1
                for key in ("added", "external_removed", "modified", "disabled", "restored"):
                    totals[key] += stats[key]
        return totals

    async def run_forever(self, interval_seconds: Optional[int] = None) -> None:
        interval = (
            interval_seconds
            if interval_seconds is not None
            else int(os.environ.get("SKILL_SYNC_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))
        )
        if interval <= 0:
            logger.info("SkillSyncService: disabled (interval <= 0)")
            return
        logger.info(f"SkillSyncService: started (interval {interval}s)")
        while True:
            try:
                totals = await self.reconcile_all()
                if any(totals[k] for k in totals if k != "workspaces"):
                    logger.info(f"SkillSyncService: drift healed {totals}")
                else:
                    logger.debug(f"SkillSyncService: pass clean {totals}")
            except asyncio.CancelledError:
                logger.info("SkillSyncService: stopped")
                raise
            except Exception as exc:
                logger.error(f"SkillSyncService: pass failed: {exc}")
            await asyncio.sleep(interval)
