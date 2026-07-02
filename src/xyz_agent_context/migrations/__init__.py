"""
@file_name: migrations/__init__.py
@author: NetMind.AI
@date: 2026-06-09
@description: Ordered, version-tracked data migrations — the layer-by-layer
upgrade ledger.

Each migration is one SEPARATE module (`mNNNN_<topic>.py`) exporting a single
`MIGRATION = Migration(...)`. They are listed in REGISTRY in apply order. On
every backend startup (cloud / `bash run.sh` / DMG sidecar all boot the same
`backend.main` lifespan), `run_pending_migrations` is called AFTER schema
auto_migrate. It reads the `schema_migrations` ledger and applies every migration
not yet recorded, IN ORDER.

Why this shape:
  - Cross-version upgrades stay simple. A DB last touched on v1.7 that jumps to
    v2.1 just runs the still-pending migrations one layer at a time (1.8 → 1.9 →
    2.0 → 2.1 worth of steps), each authored against only its own predecessor.
  - Run-once. The ledger means a heavy backfill runs the first time and is a
    single cheap SELECT thereafter.
  - Universal. Startup is the only hook every environment shares; DMG / run.sh
    users have no CI, so a migration MUST run here, not in a deploy step.

Discipline:
  - APPEND new migrations; NEVER reorder, renumber, or mutate a shipped one
    (its id is recorded in users' ledgers). To fix a bad migration, add a new one.
  - Every `apply` MUST be idempotent (re-running upserts the same rows), because
    a failed migration is retried next startup and the ledger row is only written
    on success.

Distinct from `one_shot_migrations.py` (narrow always-run self-heals) and
`schema_registry.auto_migrate` (idempotent DDL). This handles heavy, one-time,
versioned DATA migrations.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Set, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


@dataclass(frozen=True)
class Migration:
    id: str  # "0001_<topic>" — ordered, unique, immutable once shipped
    description: str
    apply: Callable[["AsyncDatabaseClient"], Awaitable[Dict]]  # MUST be idempotent


async def _applied_ids(db: "AsyncDatabaseClient") -> Set[str]:
    try:
        rows = await db.get("schema_migrations")
        return {r["migration_id"] for r in rows if r.get("migration_id")}
    except Exception as e:  # noqa: BLE001 — table absent ⇒ treat as nothing applied
        logger.warning(f"[migrate] could not read ledger ({e}); assuming none applied")
        return set()


async def _record(db: "AsyncDatabaseClient", migration_id: str, stats: Dict) -> None:
    from xyz_agent_context import __version__
    await db.insert("schema_migrations", {
        "migration_id": migration_id,
        "app_version": __version__,
        "notes": json.dumps(stats, default=str)[:2000],
    })


async def run_pending_migrations(db: "AsyncDatabaseClient") -> Dict[str, Dict]:
    """Apply every still-pending migration in order, exactly once.

    Best-effort: a migration that raises is logged, NOT recorded (so it retries
    on the next startup) and STOPS the chain (later migrations may depend on it).
    It never re-raises — startup is never blocked (caller may still wrap defensively).
    """
    applied = await _applied_ids(db)
    results: Dict[str, Dict] = {}
    for m in REGISTRY:
        if m.id in applied:
            continue
        logger.info(f"[migrate] applying {m.id}: {m.description}")
        try:
            stats = await m.apply(db)
        except Exception as e:  # noqa: BLE001 — non-blocking; retry next startup
            logger.error(
                f"[migrate] {m.id} FAILED (non-blocking, retries next startup): {e}"
            )
            break
        try:
            await _record(db, m.id, stats)
        except Exception as e:  # noqa: BLE001 — work done but ledger write failed
            logger.error(f"[migrate] {m.id} applied but ledger write failed: {e}")
            break
        results[m.id] = stats
        logger.info(f"[migrate] {m.id} done: {stats}")
    return results


# ── Ordered registry — APPEND ONLY ────────────────────────────────────────────
from .m0001_unified_memory_backfill import MIGRATION as _m0001  # noqa: E402
from .m0002_workspace_nested_layout import MIGRATION as _m0002  # noqa: E402

REGISTRY: List[Migration] = [
    _m0001,
    _m0002,
]
