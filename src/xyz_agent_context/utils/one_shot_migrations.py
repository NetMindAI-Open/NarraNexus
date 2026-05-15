"""
@file_name: one_shot_migrations.py
@author: Bin Liang
@date: 2026-04-21
@description: One-shot data migrations that run on every backend startup after
auto_migrate. Each function is idempotent (safe to call multiple times).

Spec: 2026-04-21-job-timezone-redesign
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


def _is_cloud_mode_env() -> bool:
    """Local copy of the cloud-mode check to avoid an import cycle with
    backend.auth. Mirrors the semantics there: cloud mode iff
    ``DATABASE_URL`` points at a non-sqlite backend."""
    url = (os.environ.get("DATABASE_URL") or "").strip().lower()
    return bool(url) and not url.startswith("sqlite")


async def heal_legacy_singleton_ownership(
    db: "AsyncDatabaseClient",
) -> Dict[str, int]:
    """Re-attribute pre-2026-05-13 local-mode data that was wrongly owned
    by the 'local-default' singleton user.

    Background: until 2026-05-13, ``get_local_user_id()`` always returned
    the first row in the ``users`` table — regardless of which user the
    frontend was actually logged in as. So when alice logged in locally
    and created a team, the team's ``owner_user_id`` was silently set to
    'local-default' even though the agent she created was correctly
    owned by 'alice' (the agents route used a different code path).

    Symptom: alice can't add her own agents to her own teams because the
    backend's ``add_member`` route compares team.owner (='local-default')
    against alice (the now-correctly-resolved user) and rejects with 403.

    Self-heal strategy — VERY narrow conditions to avoid accidental
    re-attribution:

      1. local mode only (cloud has correct identity from JWT)
      2. exactly ONE non-default user exists (no ambiguity over who the
         legacy data belongs to)
      3. that user has at least one agent of their own (proves they
         actually used the app — not just an empty account that someone
         created via ``create-user``)
      4. there are rows owned by 'local-default' that this user should
         logically own

    Only when ALL four hold do we re-attribute the legacy rows. Else
    we no-op and log. The function is idempotent: after re-attribution
    'local-default' no longer owns anything, condition (4) fails on
    next boot, no-op.

    Returns: {"teams": <int re-attributed>}
    """
    out: Dict[str, int] = {"teams": 0}

    # Condition 1: local mode only.
    if _is_cloud_mode_env():
        return out

    # Condition 2: exactly one non-default user.
    users = await db.get("users", {})
    custom_users = [u for u in users if u.get("user_id") != "local-default"]
    if len(custom_users) != 1:
        if len(custom_users) > 1:
            logger.info(
                f"[singleton-heal] {len(custom_users)} non-default users — "
                f"refusing to auto-attribute (ambiguous). Manual SQL needed "
                f"if any 'local-default'-owned rows exist."
            )
        return out
    target_user_id = custom_users[0]["user_id"]

    # Condition 3: the target user has at least one agent of their own.
    real_agents = await db.get("agents", {"created_by": target_user_id})
    if not real_agents:
        return out

    # Condition 4: anything legacy-owned to re-attribute?
    legacy_teams = await db.get("teams", {"owner_user_id": "local-default"})
    if not legacy_teams:
        return out

    logger.warning(
        f"[singleton-heal] Re-attributing {len(legacy_teams)} legacy team(s) "
        f"from 'local-default' to '{target_user_id}'. This fixes the "
        f"pre-2026-05-13 local-mode singleton-ownership bug."
    )

    try:
        affected = await db.update(
            "teams",
            {"owner_user_id": "local-default"},
            {"owner_user_id": target_user_id},
        )
        out["teams"] = affected if isinstance(affected, int) else len(legacy_teams)
    except Exception as e:  # noqa: BLE001
        logger.error(
            f"[singleton-heal] team re-attribution failed: {e}. "
            f"Legacy teams remain owned by 'local-default' and will not "
            f"appear in the UI under '{target_user_id}'. Manual SQL: "
            f"UPDATE teams SET owner_user_id='{target_user_id}' "
            f"WHERE owner_user_id='local-default';"
        )

    return out


async def migrate_jobs_protocol_v2_timezone(db: "AsyncDatabaseClient") -> Dict[str, int]:
    """
    Cancel active jobs that predate the v2 timezone protocol.

    A job is "old-protocol" iff:
      - status in ('pending', 'active', 'paused'), AND
      - trigger_config JSON has no 'timezone' key.

    Idempotent: old rows become status='cancelled'; next-run fields nulled.
    Subsequent calls find no candidates.

    Returns: {"cancelled": <int>}
    """
    cancelled = 0
    for status in ("pending", "active", "paused"):
        rows = await db.get("instance_jobs", filters={"status": status})
        for row in rows:
            tc_raw = row.get("trigger_config")
            if not tc_raw:
                continue
            try:
                tc = json.loads(tc_raw) if isinstance(tc_raw, str) else tc_raw
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(tc, dict):
                continue
            if tc.get("timezone"):
                continue  # new protocol job, leave alone
            await db.update(
                "instance_jobs",
                {"job_id": row["job_id"]},
                {
                    "status": "cancelled",
                    "last_error": (
                        "Protocol migration: trigger_config schema now requires "
                        "timezone field, please recreate this job via the agent."
                    ),
                    "next_run_time": None,
                    "next_run_at_local": None,
                    "next_run_tz": None,
                },
            )
            cancelled += 1

    if cancelled:
        logger.info(f"[migration] jobs_protocol_v2_timezone cancelled={cancelled}")
    return {"cancelled": cancelled}
