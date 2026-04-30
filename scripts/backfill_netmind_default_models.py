"""
@file_name: backfill_netmind_default_models.py
@author: Bin Liang
@date: 2026-04-29
@description: Backfill newly added NetMind default models into existing user_providers rows.

Idempotent — safe to run multiple times. Adds any model from the current
`get_default_models("netmind", "openai")` list that is missing from a
user's `models` JSON array, preserving existing entries and ordering
new ones at the end.

Usage:
    uv run python scripts/backfill_netmind_default_models.py
    uv run python scripts/backfill_netmind_default_models.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import List

# Make project src/ importable when run as a script (no install required)
sys.path.insert(0, "src")

from xyz_agent_context.agent_framework.model_catalog import get_default_models
from xyz_agent_context.utils.db_factory import get_db_client


_PROTOCOLS = ("openai", "anthropic")


async def _backfill_protocol(db, protocol: str, dry_run: bool) -> int:
    rows = await db.get(
        "user_providers",
        filters={"source": "netmind", "protocol": protocol},
    )
    if not rows:
        print(f"[{protocol}] No NetMind ({protocol}) provider rows found.")
        return 0

    target_defaults: List[str] = list(get_default_models("netmind", protocol))
    print(f"[{protocol}] Current default model list ({len(target_defaults)} models):")
    for m in target_defaults:
        print(f"  - {m}")
    print()

    changed = 0
    for row in rows:
        provider_id = row.get("provider_id")
        user_id = row.get("user_id")
        raw_models = row.get("models") or "[]"
        try:
            existing: List[str] = list(json.loads(raw_models))
        except json.JSONDecodeError:
            print(f"  [SKIP] {user_id}/{provider_id}: malformed models JSON: {raw_models!r}")
            continue

        missing = [m for m in target_defaults if m not in existing]
        if not missing:
            print(f"  [OK]   {user_id}/{provider_id}: already up-to-date "
                  f"({len(existing)} models)")
            continue

        new_models = existing + missing
        print(f"  [FILL] {user_id}/{provider_id}: adding {len(missing)} model(s)")
        for m in missing:
            print(f"         + {m}")

        if dry_run:
            continue

        await db.update(
            "user_providers",
            filters={"id": row["id"]},
            data={"models": json.dumps(new_models, ensure_ascii=False)},
        )
        changed += 1

    return changed


async def main(dry_run: bool) -> int:
    db = await get_db_client()
    total = 0
    for protocol in _PROTOCOLS:
        total += await _backfill_protocol(db, protocol, dry_run)
        print()

    if dry_run:
        print("Dry run — no rows updated. Re-run without --dry-run to apply.")
    else:
        print(f"Done. Updated {total} row(s) across {len(_PROTOCOLS)} protocol(s).")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Preview only; do not write.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.dry_run)))
