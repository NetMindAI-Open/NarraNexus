"""
@file_name: migrate_workspace_layout.py
@date: 2026-06-17
@description: One-off migration — flat workspace dirs
``{agent_id}_{user_id}`` → nested ``{user_id}/{agent_id}``.

Run this ONCE at deploy, BEFORE flipping
``utils.workspace_paths._LAYOUT`` to "nested" (and before per-user
executor containers bind-mount ``{base}/{user_id}``). Idempotent and
non-destructive: it never overwrites or deletes — a pre-existing nested
target leaves the flat dir untouched and reported as a conflict.

Usage:
    # preview (default)
    uv run python scripts/migrate_workspace_layout.py
    # actually move
    uv run python scripts/migrate_workspace_layout.py --apply
    # explicit base dir
    uv run python scripts/migrate_workspace_layout.py --base /opt/narranexus/workspaces --apply
"""
from __future__ import annotations

import argparse
import asyncio

from xyz_agent_context.settings import settings
from xyz_agent_context.utils.workspace_paths import migrate_flat_to_nested


async def _load_known_user_ids() -> set[str]:
    """Authoritative real user ids from the DB — needed to disambiguate the
    legacy ``_user_`` infix form from a real user id starting with ``user_``."""
    from xyz_agent_context.utils import get_db_client
    db = await get_db_client()
    rows = await db.execute("SELECT user_id FROM users", (), fetch=True)
    return {r["user_id"] for r in (rows or []) if r.get("user_id")}


async def _amain() -> None:
    ap = argparse.ArgumentParser(description="Migrate workspace layout flat → nested")
    ap.add_argument("--base", default=None, help="base working path (default: settings.base_working_path)")
    ap.add_argument("--apply", action="store_true", help="actually move dirs (default: dry-run preview)")
    args = ap.parse_args()

    base = args.base or settings.base_working_path
    known = await _load_known_user_ids()
    report = migrate_flat_to_nested(base, known, dry_run=not args.apply)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] base={base}  (known users: {len(known)})")
    print(f"  to move: {len(report['moved'])}")
    for src, dst in report["moved"][:100]:
        print(f"    {src}  ->  {dst}")
    print(f"  conflicts (target exists, left in place): {len(report['conflicts'])}")
    for c in report["conflicts"][:100]:
        print(f"    {c}")
    print(f"  unknown owner (left in place, NOT guessed): {len(report['unknown'])}")
    for u in report["unknown"][:100]:
        print(f"    {u}")
    print(f"  skipped (non-flat / already nested): {len(report['skipped'])}")
    if not args.apply and report["moved"]:
        print("\n  (dry-run — re-run with --apply to perform the moves)")


if __name__ == "__main__":
    asyncio.run(_amain())
