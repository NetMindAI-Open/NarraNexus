"""
@file_name: grant_quota_to_existing_users.py
@author: Bin Liang
@date: 2026-04-16
@description: One-shot migration — iterate over existing users and call
QuotaService.init_for_user on each.

Idempotent: users that already have a user_quotas row are skipped by
init_for_user (it returns the existing row unchanged). Safe to re-run.

Usage on the EC2 host:
    cd /opt/narranexus/NarraNexus
    uv run python scripts/grant_quota_to_existing_users.py

Exits 1 if the feature is disabled (env-check failure) — nothing to do.
"""
from __future__ import annotations

import asyncio
import sys

from loguru import logger

from xyz_agent_context.agent_framework.quota_service import QuotaService
from xyz_agent_context.agent_framework.system_provider_service import (
    SystemProviderService,
)
from xyz_agent_context.repository.quota_repository import QuotaRepository
from xyz_agent_context.utils.db_factory import get_db_client


async def main() -> int:
    sys_provider = SystemProviderService.instance()
    if not sys_provider.is_enabled():
        print(
            "SystemProviderService disabled — nothing to do. "
            "Check SYSTEM_DEFAULT_LLM_* env vars and re-run.",
            file=sys.stderr,
        )
        return 1

    db = await get_db_client()
    quota_repo = QuotaRepository(db)
    quota_svc = QuotaService(repo=quota_repo, system_provider=sys_provider)

    # Enumerate users via raw SQL so we don't depend on UserRepository
    # ordering or filters that might evolve.
    rows = await db.execute(
        "SELECT user_id FROM users", params=(), fetch=True
    )
    user_ids = [r["user_id"] for r in rows if r.get("user_id")]
    total = len(user_ids)

    created = 0
    skipped = 0
    for uid in user_ids:
        try:
            existing = await quota_repo.get_by_user_id(uid)
            if existing is not None:
                skipped += 1
                continue
            result = await quota_svc.init_for_user(uid)
            if result is not None:
                created += 1
            else:
                logger.warning(
                    f"init_for_user returned None for {uid} "
                    "(check logs — likely a DB insert failure)"
                )
        except Exception as e:
            logger.error(f"failed to seed quota for {uid}: {e}")

    print(
        f"done — created: {created}, skipped: {skipped}, total: {total}"
    )
    return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
