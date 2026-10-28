"""
@file_name: user_settings_repository.py
@date: 2026-06-08
@description: CRUD for per-user settings (user_settings table).

First consumer: analytics opt-out. Read path is hot (checked before every
funnel event) so is_analytics_opted_out() is a single indexed lookup that
treats "no row" as "not opted out" (tracking on by default).
"""
from __future__ import annotations

from typing import Any

from narranexus.platform.utils import AsyncDatabaseClient


class UserSettingsRepository:
    table_name = "user_settings"

    def __init__(self, db: AsyncDatabaseClient) -> None:
        self.db = db

    async def is_analytics_opted_out(self, user_id: str) -> bool:
        row: dict[str, Any] | None = await self.db.get_one(
            self.table_name, {"user_id": user_id}
        )
        if not row:
            return False
        return bool(row.get("analytics_opt_out"))

    async def get_reply_language(self, user_id: str) -> str | None:
        """The user's preferred reply language (i18n code) or None when
        never set — None means the model keeps its historical freedom."""
        row: dict[str, Any] | None = await self.db.get_one(
            self.table_name, {"user_id": user_id}
        )
        if not row:
            return None
        value = (row.get("reply_language") or "").strip()
        return value or None

    async def set_reply_language(self, user_id: str, language: str | None) -> None:
        """Persist the reply-language preference (None/empty clears it)."""
        value = (language or "").strip()
        # Atomic upsert (db-level); the manual read-then-write pair racing
        # two concurrent PUTs on the UNIQUE user_id was review issue #4.
        await self.db.upsert(
            self.table_name, {"user_id": user_id, "reply_language": value}, "user_id"
        )

    async def set_analytics_opt_out(self, user_id: str, opted_out: bool) -> None:
        value = 1 if opted_out else 0
        await self.db.upsert(
            self.table_name, {"user_id": user_id, "analytics_opt_out": value}, "user_id"
        )
