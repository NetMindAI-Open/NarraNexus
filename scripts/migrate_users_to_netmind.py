"""
@file_name: migrate_users_to_netmind.py
@author: NetMind.AI
@date: 2026-06-11
@description: One-shot offline migration of legacy user ids to NetMind
userSystemCode (Phase 1 user-system unification).

Legacy users registered with a self-chosen username that became their
user_id; the NetMind switchover keys identity by the 32-hex
userSystemCode instead. This is a thin CLI over the shared kernel in
`xyz_agent_context.services.identity_migration` (same kernel the
POST /api/admin/migrate-identity route uses — one source of truth).

OPERATIONAL RULES (hard-won — see v1.7.16 outage):
  * Run OFFLINE with the stack stopped (`make app-down`). Never wire
    this into backend lifespan; it would blow the compose healthcheck
    window and take prod down.
  * Three-stage workflow, in order:
        --report            inventory users + resolve emails, list issues
        --execute --mapping mapping.csv   rewrite (one txn per user)
        --verify  --mapping mapping.csv   assert zero residual old ids
  * mapping.csv columns: old_user_id,new_user_system_code
    (emails from --report are sent to NetMind; their batch-provisioning
    answer comes back as this CSV).
  * Idempotent: re-running --execute after success is a no-op, so a
    crash mid-run is recovered by simply running again.

Usage:
    uv run python scripts/migrate_users_to_netmind.py --report
    uv run python scripts/migrate_users_to_netmind.py --execute --mapping m.csv
    uv run python scripts/migrate_users_to_netmind.py --verify --mapping m.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path
from typing import Dict

# Allow running both as a script (uv run python scripts/...) and via import.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

# Re-export the shared kernel so existing tooling/tests that import this
# script keep working unchanged (铁律 8 — no duplicated migration logic).
from xyz_agent_context.services.identity_migration import (  # noqa: E402,F401
    IdentityColumns,
    build_report,
    classify_identity_columns,
    execute_migration,
    verify_migration,
)


def _load_mapping(path: str) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or row[0].startswith("#") or row[0] == "old_user_id":
                continue
            old_id, new_id = row[0].strip(), row[1].strip()
            if len(new_id) != 32:
                raise SystemExit(
                    f"Suspicious userSystemCode for {old_id!r}: {new_id!r} "
                    "(expected 32 chars)"
                )
            mapping[old_id] = new_id
    return mapping


async def _amain() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--report", action="store_true")
    group.add_argument("--execute", action="store_true")
    group.add_argument("--verify", action="store_true")
    parser.add_argument("--mapping", help="CSV: old_user_id,new_user_system_code")
    args = parser.parse_args()

    from xyz_agent_context.settings import settings
    from xyz_agent_context.utils.db_factory import get_db_client

    db = await get_db_client()

    if args.report:
        rows = await build_report(db)
        writer = csv.writer(sys.stdout)
        writer.writerow(["user_id", "email", "issue"])
        for r in rows:
            writer.writerow([r["user_id"], r["email"] or "", r["issue"] or ""])
        issues = [r for r in rows if r["issue"] == "no_email"]
        print(
            f"# {len(rows)} users, {len(issues)} without a resolvable email",
            file=sys.stderr,
        )
        return

    if not args.mapping:
        raise SystemExit("--execute/--verify require --mapping <csv>")
    mapping = _load_mapping(args.mapping)

    if args.execute:
        stats = await execute_migration(
            db, mapping, base_working_path=settings.base_working_path
        )
        print(json.dumps(stats, indent=2))
        return

    residuals = await verify_migration(db, list(mapping.keys()))
    print(json.dumps(residuals, indent=2))
    bad = {k: v for k, v in residuals.items() if v}
    if bad:
        raise SystemExit(f"Residual old ids found: {bad}")
    print("verify OK — zero residuals", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(_amain())
