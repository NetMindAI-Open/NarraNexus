"""
@file_name: backfill.py
@author: Bin Liang
@date: 2026-05-13
@description: One-shot backfill of new user_providers columns for legacy rows.

Phase 0 of the Provider Unification rolls out four new columns on
``user_providers`` (driver_type / owner_user_id / billing_policy /
auth_ref). ``auto_migrate()`` adds the columns but doesn't populate
them. This module fills them in by deriving values from the existing
(source, auth_type, protocol) triple.

Design contract:

* **Idempotent** — running it twice changes nothing the second time.
* **Forward-only** — never overwrites a non-null new column value (so
  manual admin edits stick), except OAuth ``auth_ref`` canonicalization
  where a source-specific sentinel is required for runtime auth.
* **Best-effort** — rows it can't classify get logged and left alone;
  the resolver's hot path will refuse to handle them and surface a
  clear error to the user (rather than us guessing wrong).

Called once during ``get_db_client`` bootstrap, right after
``auto_migrate`` returns. Safe to call again at any time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from xyz_agent_context.agent_framework.provider_driver.derive import (
    derive_auth_ref,
    derive_billing_policy,
    derive_driver_type,
)

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


async def backfill_provider_metadata(db: "AsyncDatabaseClient") -> dict:
    """Populate ``driver_type`` / ``owner_user_id`` / ``billing_policy``
    / ``auth_ref`` on existing ``user_providers`` rows.

    Returns a small stats dict so callers (and `make health-check`)
    can log progress.

    Implementation notes:

    * We update one row at a time rather than a single bulk UPDATE so
      the derive logic stays in Python — easier to test, easier to
      change, no SQL gymnastics for CASE / JSON expressions.
    * ``user_providers`` rarely has more than a few dozen rows per
      user; even on cloud Aurora the per-tenant set rarely tops a
      hundred. The loop's cost is negligible compared to the rest of
      backend startup.
    """
    # Pull all rows. Most already-classified rows are no-ops, but this
    # lets us normalize stale OAuth auth_ref sentinels left by older
    # builds without waiting for users to recreate providers.
    rows = await db.get("user_providers")

    classified = 0
    skipped = 0
    already_set = 0
    normalized_auth_refs = 0

    for row in rows or []:
        driver_type = row.get("driver_type")
        if driver_type:
            already_set += 1
        else:
            driver_type = derive_driver_type(
                row.get("source"),
                row.get("auth_type"),
                row.get("protocol"),
            )

        if not driver_type:
            logger.warning(
                f"[backfill] Cannot classify provider row "
                f"provider_id={row.get('provider_id')!r} "
                f"source={row.get('source')!r} "
                f"auth_type={row.get('auth_type')!r} "
                f"protocol={row.get('protocol')!r}"
            )
            skipped += 1
            continue

        auth_ref = None
        if driver_type in {"claude_oauth", "codex_oauth"}:
            auth_ref = derive_auth_ref(row.get("auth_type"), row.get("source"))

        # owner_user_id: local mode → always self-owned (= user_id).
        # Cloud mode system rows come in with owner_user_id IS NULL by
        # design and don't appear here because the cloud migration
        # script writes the column at insert time.
        owner_user_id = row.get("user_id") or None

        updates = {}
        if not row.get("driver_type"):
            updates["driver_type"] = driver_type
            updates["billing_policy"] = derive_billing_policy(
                row.get("source"),
                row.get("auth_type"),
            )
        if auth_ref is not None and row.get("auth_ref") != auth_ref:
            updates["auth_ref"] = auth_ref
            normalized_auth_refs += 1
        if owner_user_id and not row.get("owner_user_id"):
            updates["owner_user_id"] = owner_user_id

        if not updates:
            continue

        await db.update(
            "user_providers",
            {"provider_id": row["provider_id"]},
            updates,
        )
        if "driver_type" in updates:
            classified += 1

    stats = {
        "classified": classified,
        "skipped": skipped,
        "already_set": already_set,
        "normalized_auth_refs": normalized_auth_refs,
        "total_seen": len(rows or []),
    }
    if classified or skipped or normalized_auth_refs:
        logger.info(
            f"[backfill] user_providers metadata: classified={classified}, "
            f"skipped={skipped}, already_set={already_set}, "
            f"normalized_auth_refs={normalized_auth_refs}"
        )
    else:
        logger.debug(f"[backfill] user_providers metadata: nothing to do ({stats})")
    return stats


__all__ = ["backfill_provider_metadata"]
