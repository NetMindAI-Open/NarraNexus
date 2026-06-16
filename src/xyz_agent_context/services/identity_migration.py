"""
@file_name: identity_migration.py
@author: NetMind.AI
@date: 2026-06-12
@description: Platform service for migrating a legacy user id to a NetMind
userSystemCode (Phase 1 user-system unification).

Shared kernel behind two call sites (铁律 3/8 — one source of truth):
  * scripts/migrate_users_to_netmind.py — offline CLI, batch CSV mapping,
    run with the stack stopped (`make app-down`).
  * backend/routes/admin_migration.py — POST /api/admin/migrate-identity,
    one user per call.

Identity rewrite is derived from schema_registry at runtime: every
identity-named column is either rewritten, explicitly excluded (a DIFFERENT
id space — IM platform uids, bus agent ids), or conditional (memory_* scope
only when scope_type='user'). A new identity column added without an explicit
classification makes classify_identity_columns() raise rather than silently
skip data.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from xyz_agent_context.utils import schema_registry

# Identity-shaped column names we scan schema_registry for.
_IDENTITY_COLUMN_NAMES = {
    "user_id",
    "owner_user_id",
    "created_by",
    "used_by_user_id",
    "scope_id",
}

# Explicit classification of every identity-named column. Three buckets:
#   include      — holds a NarraNexus user id, rewrite it
#   exclude      — same name, DIFFERENT id space, never touch
#   conditional  — memory_* scope_id: a user id only when scope_type='user'
_EXCLUDE: dict = {
    # Slack/Telegram platform uids — not NarraNexus users.
    ("channel_slack_credentials", "owner_user_id"): "IM platform uid",
    ("channel_telegram_credentials", "owner_user_id"): "IM platform uid",
    # Bus channel owner is an AGENT id (message_bus_trigger activation rule).
    ("bus_channels", "created_by"): "agent id, not user id",
}

_MEMORY_TABLE_PREFIX = "memory_"

# users.metadata stamp left on migrated rows for audit / idempotency checks.
_MIGRATION_STAMP_KEY = "netmind_migration"


@dataclass
class IdentityColumns:
    plain: List[tuple] = field(default_factory=list)  # (table, column)
    memory_scope_tables: List[str] = field(default_factory=list)


def classify_identity_columns() -> IdentityColumns:
    """Derive the rewrite plan from schema_registry, failing loudly on drift.

    Any identity-named column that is neither classified here nor covered
    by the memory scope rule raises — a new table added after this kernel
    was written must be consciously classified, not silently skipped.
    """
    result = IdentityColumns()
    for table_name, table in sorted(schema_registry.TABLES.items()):
        for col in table.columns:
            if col.name not in _IDENTITY_COLUMN_NAMES:
                continue
            if (table_name, col.name) in _EXCLUDE:
                continue
            if col.name == "scope_id":
                if table_name.startswith(_MEMORY_TABLE_PREFIX):
                    result.memory_scope_tables.append(table_name)
                    continue
                raise RuntimeError(
                    f"Unclassified scope_id column: {table_name}.{col.name} — "
                    "update CLASSIFICATION in identity_migration.py"
                )
            result.plain.append((table_name, col.name))
    return result


async def build_report(db) -> List[dict]:
    """Inventory legacy users and resolve their emails via invite_codes.

    Emails were never written to users.email by the legacy register flow;
    the only mapping lives in invite_codes (code issued to an email,
    consumed by used_by_user_id). Users without a resolvable email are
    flagged for manual handling.
    """
    users = await db.execute(
        "SELECT user_id, user_type, email, metadata FROM users", fetch=True
    )
    rows: List[dict] = []
    for u in users or []:
        user_id = u["user_id"]
        stamp = _read_stamp(u.get("metadata"))
        if stamp is not None:
            rows.append(
                {"user_id": user_id, "email": None, "issue": "already_migrated"}
            )
            continue

        email = (u.get("email") or "").strip() or None
        if email is None:
            invite = await db.execute(
                "SELECT email FROM invite_codes WHERE used_by_user_id = %s "
                "AND email IS NOT NULL LIMIT 1",
                params=(user_id,),
                fetch=True,
            )
            if invite:
                email = (invite[0].get("email") or "").strip() or None

        rows.append(
            {
                "user_id": user_id,
                "email": email,
                "issue": None if email else "no_email",
            }
        )
    return rows


def _read_stamp(metadata_raw) -> Optional[dict]:
    if not metadata_raw:
        return None
    meta = metadata_raw
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            return None
    if not isinstance(meta, dict):
        return None
    return meta.get(_MIGRATION_STAMP_KEY)


async def _stamp_user(db, new_id: str, old_id: str) -> None:
    row = await db.get_one("users", {"user_id": new_id})
    meta = row.get("metadata") if row else None
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    meta = meta if isinstance(meta, dict) else {}
    meta[_MIGRATION_STAMP_KEY] = {"old_user_id": old_id}
    await db.execute(
        "UPDATE users SET metadata = %s WHERE BINARY user_id = %s",
        params=(json.dumps(meta, ensure_ascii=False), new_id),
        fetch=False,
    )


def _is_unique_identity_column(table_name: str, column: str) -> bool:
    """True if `column` is unique within `table` (single-row per-user table).

    Read from schema_registry: the column is marked unique, or a single-column
    unique index covers it. The MERGE path uses this to decide whether a legacy
    row can move onto an existing target (no — drop the legacy duplicate) or
    rewrite freely (yes).
    """
    table = schema_registry.TABLES.get(table_name)
    if table is None:
        return False
    for col in table.columns:
        if col.name == column and getattr(col, "unique", False):
            return True
    for idx in getattr(table, "indexes", None) or []:
        if idx.unique and list(idx.columns) == [column]:
            return True
    return False


async def execute_migration(
    db, mapping: Dict[str, str], base_working_path: str
) -> dict:
    """Rewrite identity columns and rename workspaces for every mapped user.

    Per-user: one transaction over all column rewrites (all-or-nothing),
    then filesystem renames after commit (renames are idempotent and a
    crashed run is recovered by re-running). Users whose old id no longer
    exists are skipped — that is what makes re-runs no-ops.
    """
    cols = classify_identity_columns()
    stats = {"users_migrated": 0, "users_merged": 0,
             "rows_updated": 0, "dirs_renamed": 0}

    for old_id, new_id in mapping.items():
        existing_old = await db.get_one("users", {"user_id": old_id})
        if existing_old is None:
            continue  # already migrated (or never existed) — idempotency

        # If the target userSystemCode row already exists (the user logged in
        # via NetMind before we rekeyed their legacy data), MERGE: unique
        # per-user columns can't be rewritten onto the existing target without
        # colliding, so the legacy duplicate is dropped and the target row
        # stays authoritative. Multi-row business data moves freely.
        merge = (await db.get_one("users", {"user_id": new_id})) is not None

        # Collect agent workspaces BEFORE the rewrite (paths embed old id).
        agents = await db.execute(
            "SELECT agent_id FROM agents WHERE BINARY created_by = %s",
            params=(old_id,),
            fetch=True,
        )
        agent_ids = [a["agent_id"] for a in agents or []]

        began = False
        try:
            if hasattr(db, "begin_transaction"):
                await db.begin_transaction()
                began = True
            for table, column in cols.plain:
                if merge and _is_unique_identity_column(table, column):
                    result = await db.execute(
                        f"DELETE FROM {table} WHERE BINARY `{column}` = %s",
                        params=(old_id,),
                        fetch=False,
                    )
                else:
                    result = await db.execute(
                        f"UPDATE {table} SET `{column}` = %s "
                        f"WHERE BINARY `{column}` = %s",
                        params=(new_id, old_id),
                        fetch=False,
                    )
                if isinstance(result, int):
                    stats["rows_updated"] += result
            for table in cols.memory_scope_tables:
                # memory scope_id is many-per-user, so it always moves.
                result = await db.execute(
                    f"UPDATE {table} SET scope_id = %s "
                    f"WHERE scope_type = 'user' AND BINARY scope_id = %s",
                    params=(new_id, old_id),
                    fetch=False,
                )
                if isinstance(result, int):
                    stats["rows_updated"] += result
            if not merge:
                # Non-merge: the users row was just rewritten old→new; stamp it.
                # Merge: the legacy users row was deleted; target stays as-is.
                await _stamp_user(db, new_id, old_id)
            if began:
                await db.commit()
        except Exception:
            if began:
                await db.rollback()
            raise

        # Filesystem renames after the DB commit. A crash between commit
        # and rename leaves old-named dirs; the second pass below recovers
        # them, and the rename helper tolerates already-renamed dirs.
        stats["dirs_renamed"] += _rename_workspaces(
            base_working_path, agent_ids, old_id, new_id
        )
        stats["users_migrated"] += 1
        if merge:
            stats["users_merged"] += 1

    # Second pass for crash recovery: users already migrated in the DB may
    # still have old-named workspace dirs (crash between commit and rename).
    for old_id, new_id in mapping.items():
        agents = await db.execute(
            "SELECT agent_id FROM agents WHERE BINARY created_by = %s",
            params=(new_id,),
            fetch=True,
        )
        agent_ids = [a["agent_id"] for a in agents or []]
        stats["dirs_renamed"] += _rename_workspaces(
            base_working_path, agent_ids, old_id, new_id
        )

    return stats


def _rename_workspaces(
    base_working_path: str, agent_ids: List[str], old_id: str, new_id: str
) -> int:
    renamed = 0
    for agent_id in agent_ids:
        old_dir = Path(base_working_path) / f"{agent_id}_{old_id}"
        new_dir = Path(base_working_path) / f"{agent_id}_{new_id}"
        if old_dir.is_dir() and not new_dir.exists():
            os.rename(old_dir, new_dir)
            renamed += 1
    return renamed


async def verify_migration(db, old_ids: List[str]) -> Dict[str, int]:
    """Count residual occurrences of each old id across all identity columns.

    Every count must be zero after a successful migration.
    """
    cols = classify_identity_columns()
    residuals: Dict[str, int] = {}
    for old_id in old_ids:
        total = 0
        for table, column in cols.plain:
            rows = await db.execute(
                f"SELECT COUNT(*) AS n FROM {table} "
                f"WHERE BINARY `{column}` = %s",
                params=(old_id,),
                fetch=True,
            )
            total += int(rows[0]["n"]) if rows else 0
        for table in cols.memory_scope_tables:
            rows = await db.execute(
                f"SELECT COUNT(*) AS n FROM {table} "
                f"WHERE scope_type = 'user' AND BINARY scope_id = %s",
                params=(old_id,),
                fetch=True,
            )
            total += int(rows[0]["n"]) if rows else 0
        residuals[old_id] = total
    return residuals
