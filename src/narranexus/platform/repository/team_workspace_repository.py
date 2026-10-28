"""
@file_name: team_workspace_repository.py
@author: NarraNexus
@date: 2026-08-10
@description: Data access for the two tables the team workspace introduced.

Why these exist rather than raw SQL at each call site
-----------------------------------------------------
Both tables were being queried by hand from wherever they were needed:
`team_files` from the MCP tool, the staging path and the teams route;
`instance_artifact_history` written from inside `_artifact_impl/` by reaching
through `ArtifactRepository._db` — a private attribute, from another layer.

Beyond the project convention that every table gets a repository, this is the
mechanism that keeps DIALECT risk in one place. Hand-written SQL scattered
across three modules is three chances to write something SQLite accepts and
MySQL rejects; `tests/message_bus/test_team_workspace_mysql.py` exists because
that has happened before in this codebase.

These carry raw SQL rather than subclassing `BaseRepository`'s entity plumbing:
neither table has a Pydantic model, and both are read as plain dicts by their
consumers (a listing endpoint and a chip lookup). Adding entities purely to
satisfy the base class would be ceremony without a reader.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from narranexus.platform.repository.base import parse_dt


class TeamFileRepository:
    """Rows describing files shared into a team's folder."""

    def __init__(self, db):
        self._db = db

    async def add(self, row: Dict[str, Any]) -> None:
        """Insert one file row. Raises on the UNIQUE dedup index — the caller
        treats that as "someone else won the race" and adopts their row."""
        await self._db.insert("team_files", row)

    async def list_by_team(
        self, team_id: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """A team's files, newest first, in the shape the API returns.

        The wire shape is chosen here rather than inherited from the table:
        `id`, `owner_user_id` and `content_hash` stay server-side.

        `created_at` is normalised to an OFFSET-AWARE ISO string. The stored
        value is UTC but unmarked (SQLite's ``datetime('now')`` yields
        ``'2026-08-07 12:34:56'``; MySQL a naive datetime), and a date-time
        without an offset is read as LOCAL time by ``Date.parse`` — a file
        shared moments ago rendered hours old for a non-UTC user.
        """
        sql = "SELECT * FROM team_files WHERE team_id = %s ORDER BY id DESC"
        params: tuple = (team_id,)
        if limit is not None:
            sql += " LIMIT %s"
            params = (team_id, int(limit))
        rows = await self._db.execute(sql, params, fetch=True)
        return [
            {
                "file_id": r["file_id"],
                "original_name": r["original_name"],
                "rel_path": r["rel_path"],
                "mime_type": r.get("mime_type"),
                "category": r.get("category"),
                "size_bytes": int(r.get("size_bytes") or 0),
                "shared_by_agent_id": r.get("shared_by_agent_id"),
                "created_at": parse_dt(r["created_at"]).isoformat(),
            }
            for r in (rows or [])
        ]

    async def find_by_name_and_size(
        self, team_id: str, name: str, size: int
    ) -> List[Dict[str, Any]]:
        """Dedup candidates — the cheap indexed probe that runs BEFORE hashing.

        Hashing costs a full read, so it only happens when this returns
        something. Deliberately not filtered by hash: the caller compares
        digests itself so it reads the source exactly once.
        """
        rows = await self._db.execute(
            "SELECT * FROM team_files WHERE team_id = %s AND original_name = %s "
            "AND size_bytes = %s",
            (team_id, name, size), fetch=True,
        )
        return list(rows or [])

    async def find_exact(
        self, team_id: str, name: str, content_hash: str
    ) -> Optional[Dict[str, Any]]:
        """The row a concurrent share won with, if any (same dedup key)."""
        rows = await self._db.execute(
            "SELECT * FROM team_files WHERE team_id = %s AND original_name = %s "
            "AND content_hash = %s",
            (team_id, name, content_hash), fetch=True,
        )
        return (rows or [None])[0]

    async def delete_by_team(self, team_id: str) -> int:
        return await self._db.delete("team_files", {"team_id": team_id})


class ArtifactHistoryRepository:
    """Attribution rows: who changed an artifact, when, in which turn."""

    def __init__(self, db):
        self._db = db

    async def append(
        self,
        *,
        artifact_id: str,
        agent_id: str,
        file_path: str,
        size_bytes: int,
        action: str,
        event_id: Optional[str] = None,
    ) -> None:
        await self._db.insert("instance_artifact_history", {
            "artifact_id": artifact_id,
            "agent_id": agent_id,
            "file_path": file_path,
            "size_bytes": size_bytes,
            "action": action,
            "event_id": event_id,
        })

    async def latest_actions(self, artifact_ids: List[str]) -> Dict[str, str]:
        """Map artifact_id → its most recent history action.

        The state block derives its "modified by the user / externally
        modified" markers from this: the marker clears when the agent
        re-registers (a newer updated/registered row wins). Bounded callers
        only (the state block passes ≤ its display cap)."""
        if not artifact_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(artifact_ids))
        # Only the newest row per artifact — an 800-edit history must not be
        # pulled wholesale to read one action (review #334 I10). MAX(id)
        # subquery works on both dialects; the doubled IN-list doubles the
        # placeholders, fine at the state block's ≤20 cap.
        rows = await self._db.execute(
            "SELECT artifact_id, action FROM instance_artifact_history "
            f"WHERE artifact_id IN ({placeholders}) AND id IN ("
            "SELECT MAX(id) FROM instance_artifact_history "
            f"WHERE artifact_id IN ({placeholders}) GROUP BY artifact_id)",
            params=tuple(artifact_ids) + tuple(artifact_ids),
            fetch=True,
        )
        return {r["artifact_id"]: r["action"] for r in rows or []}

    async def turns_for_team(self, team_id: str) -> Dict[str, List[str]]:
        """Map each turn to the team artifacts it created or updated.

        Joins to `instance_artifacts` so the team filter applies to the
        ARTIFACT, not the history row — history carries no team of its own.
        Rows with no ``event_id`` are skipped rather than grouped under a
        placeholder: they predate the turn handle or came from a caller with
        no event in scope.
        """
        rows = await self._db.execute(
            "SELECT DISTINCT h.event_id AS event_id, h.artifact_id AS artifact_id "
            "FROM instance_artifact_history h "
            "JOIN instance_artifacts a ON a.artifact_id = h.artifact_id "
            "WHERE a.team_id = %s AND h.event_id IS NOT NULL "
            "ORDER BY h.artifact_id",
            (team_id,), fetch=True,
        )
        out: Dict[str, List[str]] = {}
        for r in rows or []:
            out.setdefault(r["event_id"], []).append(r["artifact_id"])
        return out

    async def delete_for_artifacts(self, artifact_ids: List[str]) -> None:
        """One statement, not one per id. Bare `%s` placeholders so the raw SQL
        stays portable across the sqlite and MySQL dialects."""
        if not artifact_ids:
            return
        placeholders = ", ".join(["%s"] * len(artifact_ids))
        await self._db.execute(
            f"DELETE FROM instance_artifact_history WHERE artifact_id IN ({placeholders})",
            tuple(artifact_ids), fetch=False,
        )
