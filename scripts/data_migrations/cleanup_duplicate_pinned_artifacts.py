"""
@file_name: cleanup_duplicate_pinned_artifacts.py
@author: Bin Liang
@date: 2026-07-23
@description: One-shot cleanup — remove duplicate agent-scoped (pinned)
artifact rows that point at the same entry file, keeping the newest row.

Why this exists
    Before 2026-07-23 the LLM register_artifact path (which never knows a
    session_id -> agent-scoped, auto-pinned) minted a NEW pinned row every
    time an agent re-registered the same entry file without passing
    target_artifact_id. Each duplicate is a pinned tab that lives forever —
    prod showed two "Welcome to NarraNexus" tabs on one agent; dev had
    briefing pages pinned three times. Registration now dedupes in place
    (see artifact/_artifact_impl/registration.py), but the rows minted
    before that fix must be cleaned up once.

What it does
    Groups pinned rows by (agent_id, file_path); in every group with more
    than one row it keeps the newest (by created_at, artifact_id as
    tie-break) and deletes the rest. Registry-only: workspace files are
    never touched — all rows in a group point at the same entry file, which
    the surviving row keeps serving. Session-scoped rows are out of scope.

Idempotent & dual-backend safe
    After one apply run there are no groups left, so re-running is a no-op.
    Plain SQL, bare identifiers — runs on both SQLite and MySQL.

Usage on the EC2 host (inside the backend container):
    cd /app
    uv run python scripts/data_migrations/cleanup_duplicate_pinned_artifacts.py           # dry run
    uv run python scripts/data_migrations/cleanup_duplicate_pinned_artifacts.py --apply   # execute
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any, Dict, List

_GROUPS_SQL = """
SELECT agent_id, file_path, COUNT(*) AS n
FROM instance_artifacts
WHERE pinned = 1
GROUP BY agent_id, file_path
HAVING COUNT(*) > 1
"""

_ROWS_SQL = """
SELECT artifact_id, created_at
FROM instance_artifacts
WHERE pinned = 1 AND agent_id = %s AND file_path = %s
ORDER BY created_at DESC, artifact_id DESC
"""


async def cleanup(db, *, apply: bool) -> Dict[str, Any]:
    """
    Collapse duplicate pinned rows to the newest row per (agent_id, file_path).

    Args:
        db: AsyncDatabaseClient.
        apply: False = report only; True = delete the older duplicates.

    Returns:
        {"groups": <duplicate group count>, "to_delete": [artifact_id, ...]}
    """
    groups: List[Dict[str, Any]] = await db.execute(_GROUPS_SQL, params=(), fetch=True) or []
    to_delete: List[str] = []

    for g in groups:
        rows = await db.execute(_ROWS_SQL, params=(g["agent_id"], g["file_path"]), fetch=True)
        # rows[0] is the newest -> keep; everything after it goes.
        for row in rows[1:]:
            to_delete.append(row["artifact_id"])
        print(
            f"  {g['agent_id']}  {g['file_path']}: {g['n']} rows -> "
            f"keep {rows[0]['artifact_id']}, delete {[r['artifact_id'] for r in rows[1:]]}"
        )

    if apply:
        for artifact_id in to_delete:
            await db.delete("instance_artifacts", {"artifact_id": artifact_id})

    return {"groups": len(groups), "to_delete": to_delete}


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete the older duplicates")
    args = parser.parse_args()

    from xyz_agent_context.utils.db.db_factory import get_db_client

    db = await get_db_client()
    summary = await cleanup(db, apply=args.apply)
    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"[{mode}] {summary['groups']} duplicate groups, {len(summary['to_delete'])} rows "
          f"{'deleted' if args.apply else 'to delete'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
