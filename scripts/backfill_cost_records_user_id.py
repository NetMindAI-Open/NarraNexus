"""
@file_name: backfill_cost_records_user_id.py
@author: Bin Liang
@date: 2026-07-22
@description: One-shot backfill — populate cost_records.user_id from
agents.created_by for rows written before user_id was captured at insert time.

Why this exists
    cost_records.user_id was added so every billing row is directly
    user-attributable. Rows created before that change have user_id = NULL and
    can only be traced through cost_records.agent_id -> agents.created_by. This
    script walks that link once and freezes the answer into the new column.

The honest limitation
    agents is HARD deleted (no soft-delete column), and deleting an agent
    cascades away events / module_instances / bus_agent_registry — every table
    that ever held the agent_id -> owner mapping. So a cost_records row whose
    agent no longer exists is a TRUE orphan: its owner is unrecoverable from
    anywhere in the database. This script backfills only the rows whose agent
    still exists and reports the orphan count. It does NOT invent attributions.

Idempotent & dual-backend safe
    The UPDATE uses a correlated subquery (SQLite has no UPDATE ... JOIN) and is
    guarded by `user_id IS NULL`, so re-running only ever touches still-blank
    rows. Runs on both SQLite and MySQL.

Usage on the EC2 host:
    cd /opt/narranexus/NarraNexus
    uv run python scripts/backfill_cost_records_user_id.py           # dry run
    uv run python scripts/backfill_cost_records_user_id.py --apply   # execute
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from xyz_agent_context.utils.db_factory import get_db_client


# Rows that are NULL and whose agent still exists -> fillable from created_by.
# EXISTS (not IN): NULL-safe and better-optimized than IN/NOT IN on MySQL.
_COUNT_FILLABLE = """
SELECT COUNT(*) AS n FROM cost_records
WHERE user_id IS NULL
  AND EXISTS (SELECT 1 FROM agents WHERE agents.agent_id = cost_records.agent_id)
"""

# Rows that are NULL and whose agent is gone -> true orphans, unrecoverable.
_COUNT_ORPHAN = """
SELECT COUNT(*) AS n FROM cost_records
WHERE user_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM agents WHERE agents.agent_id = cost_records.agent_id)
"""

# Correlated-subquery backfill (no UPDATE ... JOIN, for SQLite compatibility).
_BACKFILL = """
UPDATE cost_records
SET user_id = (
    SELECT created_by FROM agents WHERE agents.agent_id = cost_records.agent_id
)
WHERE user_id IS NULL
  AND EXISTS (SELECT 1 FROM agents WHERE agents.agent_id = cost_records.agent_id)
"""


async def _count(db, sql: str) -> int:
    rows = await db.execute(sql, params=(), fetch=True)
    return int(rows[0]["n"]) if rows else 0


async def main(apply: bool) -> int:
    db = await get_db_client()

    fillable = await _count(db, _COUNT_FILLABLE)
    orphan = await _count(db, _COUNT_ORPHAN)

    print(f"cost_records with NULL user_id — fillable: {fillable}, orphan: {orphan}")
    if orphan:
        print(
            f"  note: {orphan} orphan rows belong to hard-deleted agents; their "
            "owner is unrecoverable and they will stay NULL."
        )

    if not apply:
        print("dry run — pass --apply to write. Nothing changed.")
        return 0

    if fillable == 0:
        print("nothing to backfill.")
        return 0

    affected = await db.execute(_BACKFILL, params=(), fetch=False)
    remaining_orphan = await _count(db, _COUNT_ORPHAN)
    print(
        f"backfilled: {affected} rows. Remaining NULL (orphans): {remaining_orphan}."
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the backfill. Without it, only counts are printed.",
    )
    args = parser.parse_args()
    rc = asyncio.run(main(apply=args.apply))
    sys.exit(rc)
