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

from xyz_agent_context.utils import AsyncDatabaseClient


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

    async def set_analytics_opt_out(self, user_id: str, opted_out: bool) -> None:
        existing = await self.db.get_one(self.table_name, {"user_id": user_id})
        value = 1 if opted_out else 0
        if existing:
            # updated_at is intentionally omitted from the update dict because
            # db.update() uses parameterized placeholders — passing the SQL
            # expression "(datetime('now'))" would store the literal text, not
            # evaluate it. The column retains its create-time value on updates;
            # a future migration can add a trigger if live update tracking is needed.
            await self.db.update(
                self.table_name,
                {"user_id": user_id},
                {"analytics_opt_out": value},
            )
        else:
            await self.db.insert(
                self.table_name,
                {"user_id": user_id, "analytics_opt_out": value},
            )
