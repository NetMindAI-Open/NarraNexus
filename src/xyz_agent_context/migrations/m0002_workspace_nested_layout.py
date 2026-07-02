"""
@file_name: m0002_workspace_nested_layout.py
@author: NetMind.AI
@date: 2026-06-18
@description: Migration 0002 — flat workspace dirs ``{agent_id}_{user_id}`` →
nested ``{user_id}/{agent_id}``.

The per-user executor isolation bind-mounts ``{base}/{user_id}`` into each
user's container, so a database that predates the nested layout MUST have its
flat workspace dirs reorganised before agents run — otherwise the executor
mounts an empty per-user dir and the agent loses sight of its history.

This runs automatically on every startup (cloud / ``bash run.sh`` / DMG) via the
versioned migration runner, so self-hosting users upgrading from an older
version are migrated layer-by-layer with no manual step. The CLI wrapper
``scripts/migrate_workspace_layout.py`` (dry-run preview / explicit base) shares
the same underlying ``migrate_flat_to_nested`` and stays for ops debugging.

Idempotent and non-destructive: only ``agent_*_*`` dirs whose owner resolves to
a known user are moved; a pre-existing nested target leaves the flat dir in
place and reports a conflict; unknown owners are never guessed.
"""
from __future__ import annotations

from typing import Dict, TYPE_CHECKING

from loguru import logger

from . import Migration

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


async def _load_known_user_ids(db: "AsyncDatabaseClient") -> set[str]:
    """Authoritative real user ids — needed to disambiguate the legacy
    ``_user_`` infix form from a real user id that starts with ``user_``.

    Tolerant of a missing ``users`` table (a brand-new install): returns an
    empty set, which simply means nothing is migrated (there is no legacy
    flat data on a fresh DB anyway)."""
    try:
        rows = await db.execute("SELECT user_id FROM users", (), fetch=True)
        return {r["user_id"] for r in (rows or []) if r.get("user_id")}
    except Exception as e:  # noqa: BLE001 — fresh DB / no users table
        logger.warning(f"[migrate 0002] could not read users ({e}); treating as none known")
        return set()


async def _apply(db: "AsyncDatabaseClient") -> Dict:
    import os

    from xyz_agent_context.settings import settings
    from xyz_agent_context.utils.workspace_paths import migrate_flat_to_nested

    base = settings.base_working_path
    if not os.path.isdir(base):
        # Fresh install / no workspaces yet — nothing to migrate.
        return {"moved": 0, "conflicts": 0, "unknown": 0, "skipped": 0}

    known = await _load_known_user_ids(db)
    report = migrate_flat_to_nested(base, known, dry_run=False)

    stats = {k: len(report.get(k, [])) for k in ("moved", "conflicts", "unknown", "skipped")}
    if report.get("conflicts"):
        logger.warning(
            f"[migrate 0002] {len(report['conflicts'])} workspace(s) had a nested "
            f"target already present — flat dir left in place: {report['conflicts'][:5]}"
        )
    if report.get("unknown"):
        logger.warning(
            f"[migrate 0002] {len(report['unknown'])} workspace(s) with unresolved "
            f"owner left in place (not guessed): {report['unknown'][:5]}"
        )
    return stats


MIGRATION = Migration(
    id="0002_workspace_nested_layout",
    description="Move flat {agent_id}_{user_id} workspaces to nested {user_id}/{agent_id}",
    apply=_apply,
)
