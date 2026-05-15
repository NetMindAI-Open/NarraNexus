"""
db_doctor — one-shot SQLite / MySQL schema diagnostic.

Usage:
    uv run python scripts/db_doctor.py

Prints a report of:
  - Which backend is in use (SQLite path or MySQL host)
  - Which tables the registry expects vs. which actually exist
  - Row counts for the user-data tables (sanity check that data didn't
    disappear after an upgrade)

Exits non-zero if any expected table is missing — useful in CI / smoke
test wrappers.

This is the "no, your data IS fine" / "yes, your data IS broken" tool.
Run it after upgrading NarraNexus if you suspect schema drift.
"""

import asyncio
import sys

from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils.schema_registry import (
    TABLES,
    _verify_all_tables_present,
    auto_migrate,
)


USER_DATA_TABLES = [
    "users",
    "agents",
    "teams",
    "team_members",
    "events",
    "event_stream",
    "agent_messages",
    "instance_artifacts",
    "narratives",
]


async def main() -> int:
    db = await get_db_client()
    backend = db._backend
    dialect = backend.dialect

    print("=" * 64)
    print("NarraNexus DB doctor")
    print("=" * 64)
    print(f"Dialect       : {dialect}")
    print(f"Registry size : {len(TABLES)} tables expected")
    print()

    # Step 1: integrity check WITHOUT running migration.
    missing_before = await _verify_all_tables_present(backend, dialect)
    if missing_before:
        print(f"[!] Missing tables BEFORE migrate: {missing_before}")
        print("    Running auto_migrate() to attempt repair...")
        await auto_migrate(backend)
        missing_after = await _verify_all_tables_present(backend, dialect)
        if missing_after:
            print(f"[X] Still missing AFTER migrate: {missing_after}")
            print("    auto_migrate could not create them — inspect logs above.")
            return 2
        print("[OK] Repaired by auto_migrate.")
    else:
        print("[OK] All registry tables present.")

    # Step 2: row counts on user-data tables.
    print()
    print("Row counts (user-data tables):")
    for t in USER_DATA_TABLES:
        if t not in TABLES:
            continue
        try:
            rows = await backend.execute(
                f"SELECT COUNT(*) AS c FROM {t}", None
            )
            count = rows[0]["c"] if rows else 0
            print(f"  {t:<28} {count}")
        except Exception as e:  # noqa: BLE001
            print(f"  {t:<28} ERROR: {e}")
            return 3

    print()
    print("Done. Schema is healthy.")
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except Exception as e:
        logger.exception(f"db_doctor crashed: {e}")
        rc = 1
    sys.exit(rc)
