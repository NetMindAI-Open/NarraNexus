"""
@file_name: m0005_channel_credentials_switch.py
@author: Bin Liang
@date: 2026-09-04
@description: Reads switched to ``channel_credentials`` (plugin platform batch 4d): copy every remaining row of the six retired tables (inactive ones included) so no binding is lost on upgrade.

Idempotent; the retired tables are kept, never dropped (rule #6).
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
    id="0005_channel_credentials_switch",
    description="copy the retired per-channel credential tables (all rows) before reads switch to channel_credentials",
    apply=_apply,
)
