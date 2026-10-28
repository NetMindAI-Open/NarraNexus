"""
@file_name: team_bulletin_repository.py
@author: NarraNexus
@date: 2026-08-10
@description: Data access for the team bulletin — the standing rules every
member loads on every team turn.

Two storage decisions live here rather than at the call sites, because every
caller would otherwise have to re-derive them and one of them getting it wrong
is invisible until a prompt has already gone out wrong.

**The auto-summary is a slot.** At most one `source='auto_summary'` row per
team, overwritten in place. It reaches every team turn's prompt, so a summary
that accumulated would reproduce exactly the problem the bulletin exists to
solve — unbounded standing text crowding out the conversation — and a poor
summary would compound rather than be replaced. `upsert_summary` is the only
way to write one, so no caller can turn it into a list by accident.

**The summary does not spend the entry budget.** `usage()` excludes it. If it
counted, an automatic, best-effort, possibly-stale paragraph could push a rule
the user typed by hand out of the prompt — the platform overruling the user
with its own guesswork. Its ceiling is separate (`BULLETIN_MAX_SUMMARY_CHARS`).

Ordering is oldest-first. The prompt numbers the rules, and an agent told
"rule 2" should still find the same rule 2 next turn.
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional

from .base import BaseRepository, parse_dt
from narranexus.platform.schema.team_schema import (
    BULLETIN_SOURCE_SUMMARY,
    BULLETIN_TIER_LONG_TERM,
    BulletinEntry,
    BulletinUsage,
)


class TeamBulletinRepository(BaseRepository[BulletinEntry]):
    table_name = "team_bulletin_entries"
    id_field = "entry_id"

    @staticmethod
    def gen_entry_id() -> str:
        return f"bul_{secrets.token_hex(4)}"

    def _row_to_entity(self, row: Dict[str, Any]) -> BulletinEntry:
        return BulletinEntry(
            id=row.get("id"),
            entry_id=row["entry_id"],
            team_id=row["team_id"],
            content=row.get("content") or "",
            source=row.get("source") or "user",
            author_id=row.get("author_id"),
            tier=row.get("tier") or BULLETIN_TIER_LONG_TERM,
            created_at=parse_dt(row.get("created_at")),
            updated_at=parse_dt(row.get("updated_at")),
        )

    def _entity_to_row(self, entity: BulletinEntry) -> Dict[str, Any]:
        row = entity.model_dump(exclude={"id", "created_at", "updated_at"})
        return row

    # ── reads ───────────────────────────────────────────────────────────────

    async def list_for_team(self, team_id: str) -> List[BulletinEntry]:
        """Every entry including the summary, oldest first."""
        rows = await self._db.execute(
            f"SELECT * FROM {self.table_name} WHERE team_id = %s ORDER BY id ASC",
            (team_id,),
            fetch=True,
        )
        return [self._row_to_entity(r) for r in (rows or [])]

    async def get(self, entry_id: str) -> Optional[BulletinEntry]:
        row = await self._db.get_one(self.table_name, {"entry_id": entry_id})
        return self._row_to_entity(row) if row else None

    async def get_summary(self, team_id: str) -> Optional[BulletinEntry]:
        row = await self._db.get_one(self.table_name, {"team_id": team_id, "source": BULLETIN_SOURCE_SUMMARY})
        return self._row_to_entity(row) if row else None

    async def usage(self, team_id: str) -> BulletinUsage:
        """What the SHARED entry budget holds — user and agent entries only.

        Both writers share one budget because the prompt does not care who
        wrote a line; the summary is excluded for the reason in the module
        docstring.
        """
        rows = await self._db.execute(
            f"SELECT content FROM {self.table_name} WHERE team_id = %s AND source != %s",
            (team_id, BULLETIN_SOURCE_SUMMARY),
            fetch=True,
        )
        rows = rows or []
        return BulletinUsage(
            entry_count=len(rows),
            total_chars=sum(len(r.get("content") or "") for r in rows),
        )

    # ── writes ──────────────────────────────────────────────────────────────

    async def add(
        self,
        *,
        team_id: str,
        content: str,
        source: str,
        author_id: Optional[str],
        tier: str = BULLETIN_TIER_LONG_TERM,
    ) -> BulletinEntry:
        """Append an entry. Budget enforcement belongs to the caller (the REST
        layer and the tool both refuse with a message; this layer would have
        nowhere to put the explanation)."""
        entry = BulletinEntry(
            entry_id=self.gen_entry_id(),
            team_id=team_id,
            content=content,
            source=source,
            author_id=author_id,
            tier=tier,
        )
        await self.insert(entry)
        return entry

    async def update_content(self, entry_id: str, content: str) -> bool:
        """Replace an entry's text and move its `updated_at`.

        The timestamp is written explicitly because the db client does not touch
        it; leaving it at the creation time would make "last edited" report when
        the rule was first written, which is precisely the question a reader
        asks when a rule surprises them.
        """
        from narranexus.platform.utils.timezone import utc_now

        changed = await self._db.update(
            self.table_name,
            {"entry_id": entry_id},
            {"content": content, "updated_at": utc_now()},
        )
        return bool(changed)

    async def upsert_summary(self, team_id: str, content: str) -> BulletinEntry:
        """Write THE summary slot for this team — replacing whatever was there.

        The only writer for `source='auto_summary'`, so the slot cannot become
        a list by an unlucky call site. Per team: overwriting is scoped by
        `team_id`, never global.
        """
        existing = await self.get_summary(team_id)
        if existing is not None:
            await self._db.update(
                self.table_name,
                {"entry_id": existing.entry_id},
                {"content": content},
            )
            existing.content = content
            return existing
        return await self.add(
            team_id=team_id,
            content=content,
            source=BULLETIN_SOURCE_SUMMARY,
            author_id=None,
        )

    async def delete(self, entry_id: str) -> bool:
        removed = await self._db.delete(self.table_name, {"entry_id": entry_id})
        return bool(removed)

    async def delete_tier(self, team_id: str, tier: str) -> int:
        """Clear one tier. The summary belongs to no tier and survives — a
        "clear the current task" action must not quietly drop it too."""
        return await self._db.execute(
            f"DELETE FROM {self.table_name} WHERE team_id = %s AND tier = %s AND source != %s",
            (team_id, tier, BULLETIN_SOURCE_SUMMARY),
            fetch=False,
        )

    async def delete_for_team(self, team_id: str) -> int:
        """Everything, summary included — for a team wipe or deletion."""
        return await self._db.delete(self.table_name, {"team_id": team_id})
