"""
@file_name: m0004_channel_credentials_backfill.py
@author: Bin Liang
@date: 2026-09-04
@description: Copy of the six retired per-channel credential tables into ``channel_credentials`` (plugin platform batch 4).

Idempotent (a binding already in the generic table is left alone). First
shipped in 4b as an active-rows backfill; since 4d the managers read and
write the generic table, so this copies EVERY legacy row through the
column→field translation in ``channel/credential_legacy.py``. m0005 runs the
same copy for installs that applied this id during the dual-write phase.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from . import Migration

if TYPE_CHECKING:
    from narranexus.platform.utils.db.database import AsyncDatabaseClient


async def _apply(db: "AsyncDatabaseClient") -> Dict:
    from narranexus.platform.channel.credential_legacy import copy_legacy_tables

    counts = await copy_legacy_tables(db)
    return {"channels": counts, "rows": sum(counts.values())}


MIGRATION = Migration(
    id="0004_channel_credentials_backfill",
    description="copy the retired per-channel credential tables into channel_credentials",
    apply=_apply,
)
