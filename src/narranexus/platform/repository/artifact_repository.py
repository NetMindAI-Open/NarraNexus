"""
@file_name: artifact_repository.py
@author: Bin Liang
@date: 2026-05-08
@description: Data-access layer for the instance_artifacts table (pointer model).

Pointer model (2026-05-14): one row = one artifact = a pointer to an entry file
the agent wrote in its workspace. There is no version table anymore; "updating"
an artifact overwrites the pointer in place.

Provides:
- create(): insert one artifact row
- update_pointer(): overwrite file_path/size_bytes/title/description in place
- update_title(): rename an artifact (200-char cap)
- set_pinned(): toggle pinned flag; pinning clears session_id
- list_by_session(): non-pinned artifacts for a given session
- list_pinned(): pinned artifacts for an agent
- list_by_user(): all artifacts for a user, newest first
- delete() / bulk_delete(): remove artifact rows
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .base import BaseRepository, parse_dt
from narranexus.platform.schema.artifact_schema import Artifact




def _parse_bool(v: Any) -> bool:
    """Coerce DB integer (0/1) or Python bool to bool."""
    return bool(v)


# The agent-context visibility predicate — own pinned ∪ every team the agent
# belongs to (membership from team_members, NOT the owning user; see
# list_for_agent_context's docstring for why keying on the user is the
# cross-team leak). ONE definition consumed by list / search / count so the
# copies cannot drift (review #334 I10); params are always (agent_id, agent_id).
_AGENT_CONTEXT_WHERE = (
    "((agent_id = %s AND pinned = 1 AND team_id IS NULL) "
    "OR team_id IN (SELECT team_id FROM team_members WHERE agent_id = %s))"
)


class ArtifactRepository(BaseRepository[Artifact]):
    """
    Repository for the instance_artifacts table.

    Inherits generic helpers (get_by_id, get_by_ids, find, find_one, save,
    insert, update, delete, upsert) from BaseRepository.
    """

    table_name = "instance_artifacts"
    id_field = "artifact_id"

    # ── write operations ───────────────────────────────────────────────────────

    async def create(self, entity: Artifact) -> None:
        """
        Insert one artifact row.

        The entity already carries `file_path` (entry file relative to
        base_working_path) and `size_bytes` (recursive size of the artifact
        root directory) — the runner computes both before calling here.
        """
        await self._db.insert(self.table_name, self._entity_to_row(entity))

    async def update_pointer(
        self,
        artifact_id: str,
        *,
        file_path: str,
        size_bytes: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        content_hash: Optional[str] = None,
    ) -> None:
        """
        Overwrite an artifact's pointer (and optionally title/description) in place.

        This is the `target_artifact_id` re-registration path: the agent
        re-registers a new entry file onto an existing artifact tab. The kind
        is intentionally NOT updated here — kind-match is validated upstream.

        Args:
            artifact_id: ID of the artifact to update.
            file_path: New entry file path relative to base_working_path.
            size_bytes: New artifact root directory size in bytes.
            title: New title if provided.
            description: New description if provided.
            content_hash: sha256 of the new entry, or None when hashing
                failed — written as given (a stale fingerprint is worse than
                an absent one, so no keep-old fallback).
        """
        data: Dict[str, Any] = {
            "file_path": file_path,
            "size_bytes": size_bytes,
            "content_hash": content_hash,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if title is not None:
            data["title"] = title[:200]
        if description is not None:
            data["description"] = description
        await self._db.update(self.table_name, {self.id_field: artifact_id}, data)

    async def update_title(self, artifact_id: str, title: str) -> None:
        """
        Update an artifact's title (truncated to the schema's 200-char cap).

        Args:
            artifact_id: ID of the artifact to rename.
            title: New title.
        """
        await self._db.update(
            self.table_name,
            {self.id_field: artifact_id},
            {
                "title": title[:200],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def set_pinned(self, artifact_id: str, *, pinned: bool) -> None:
        """
        Toggle pin state.

        On pin: remember current session_id in original_session_id, then clear
        session_id (cross-session visibility).
        On unpin: restore session_id from original_session_id, clear
        original_session_id, set pinned=0.
        If original_session_id was never set (agent-created artifact with no
        session context), unpin leaves the artifact orphaned with
        session_id=NULL — the route layer rejects that case and tells the user
        to delete instead.

        Args:
            artifact_id: ID of the artifact.
            pinned: Target pinned state.
        """
        existing = await self.get_by_id(artifact_id)
        if existing is None:
            raise LookupError(f"artifact not found: {artifact_id}")

        now = datetime.now(timezone.utc)
        if pinned:
            # raw SQL because db.update() filters None and we need to preserve
            # session_id NULL semantics correctly here.
            # COALESCE(original_session_id, ?) preserves a previously saved value
            # so that double-pinning does not overwrite the real original session_id
            # with the already-NULLed session_id from the first pin.
            sql = """
            UPDATE instance_artifacts
            SET pinned = %s, original_session_id = COALESCE(original_session_id, %s), session_id = NULL, updated_at = %s
            WHERE artifact_id = %s
            """
            await self._db.execute(
                sql,
                params=(1, existing.session_id, now, artifact_id),
                fetch=False,
            )
        else:
            # restore session_id from original_session_id; clear original_session_id; set pinned=0
            sql = """
            UPDATE instance_artifacts
            SET pinned = %s, session_id = %s, original_session_id = NULL, updated_at = %s
            WHERE artifact_id = %s
            """
            await self._db.execute(
                sql,
                params=(0, existing.original_session_id, now, artifact_id),
                fetch=False,
            )

    async def delete(self, artifact_id: str) -> None:  # type: ignore[override]
        """
        Delete an artifact row.

        Pointer model: there is no version table to cascade. On-disk cleanup
        (when the caller asked to delete the source files too) is the route
        layer's responsibility — this method only touches the DB.

        Args:
            artifact_id: ID of the artifact to delete.
        """
        await self._db.delete(self.table_name, {self.id_field: artifact_id})

    async def bulk_delete(self, artifact_ids: List[str]) -> int:
        """
        Delete multiple artifact rows in one call.

        On-disk cleanup is the route's responsibility — this method only
        touches the DB. Returns the number of rows actually removed.

        Args:
            artifact_ids: Artifact IDs to delete. Empty list → no-op.

        Returns:
            Number of rows deleted.
        """
        if not artifact_ids:
            return 0
        deleted = 0
        for aid in artifact_ids:
            n = await self._db.delete(self.table_name, {self.id_field: aid})
            deleted += int(n or 0)
        return deleted

    # ── query operations ───────────────────────────────────────────────────────

    async def list_by_session(
        self, agent_id: str, session_id: str
    ) -> List[Artifact]:
        """
        Return non-pinned artifacts for a given session.

        Uses raw SQL because BaseRepository.find() cannot express
        `pinned = 0 AND session_id = ?` with the simple filters dict API.

        Args:
            agent_id: Agent scope.
            session_id: Session scope.

        `team_id IS NULL` keeps this the PRIVATE surface: an artifact produced
        in a team turn belongs to the team room, and surfacing it in a
        one-to-one chat would move a team's work into a conversation it was
        never part of.

        Returns:
            List of non-pinned Artifact objects belonging to the session.
        """
        sql = """
        SELECT * FROM instance_artifacts
        WHERE agent_id = %s AND session_id = %s AND pinned = 0
          AND team_id IS NULL
        """
        rows = await self._db.execute(sql, params=(agent_id, session_id), fetch=True)
        return [self._row_to_entity(row) for row in rows]

    async def list_pinned(
        self, agent_id: str, limit: Optional[int] = None
    ) -> List[Artifact]:
        """
        Return pinned artifacts for a given agent, freshest first.

        Ordered by `updated_at DESC` so callers that only want the working
        set can take the head. Re-registering an artifact refreshes that
        timestamp, so "freshest" tracks what the agent is actually iterating
        on rather than what it happened to create first.

        Args:
            agent_id: Agent scope.
            limit: Cap the result at this many rows. None (default) returns
                every pinned artifact — bootstrap's duplicate check relies on
                an exhaustive scan, so truncation must stay opt-in.

        `team_id IS NULL` keeps this the PRIVATE surface (see list_by_session).
        For what the AGENT should have in context — its own work plus every
        team it belongs to — use `list_for_agent_context`.

        Returns:
            List of pinned Artifact objects, most recently updated first.
        """
        sql = """
        SELECT * FROM instance_artifacts
        WHERE agent_id = %s AND pinned = 1 AND team_id IS NULL
        ORDER BY updated_at DESC
        """
        params: tuple = (agent_id,)
        if limit is not None:
            sql += " LIMIT %s"
            params = (agent_id, int(limit))
        rows = await self._db.execute(sql, params=params, fetch=True)
        return [self._row_to_entity(row) for row in rows]

    async def list_by_team(
        self, team_id: str, limit: Optional[int] = None
    ) -> List[Artifact]:
        """
        Return a team's artifacts, freshest first — the team workspace panel.

        Deliberately NOT filtered by agent: the panel shows the TEAM's output,
        whichever member produced it. `agent_id` still rides on every row, so
        "who made this" survives the move to team ownership.

        Args:
            team_id: Team scope.
            limit: Cap the result; None (default) returns all.

        Returns:
            Artifacts owned by the team, most recently updated first.
        """
        sql = """
        SELECT * FROM instance_artifacts
        WHERE team_id = %s
        ORDER BY updated_at DESC
        """
        params: tuple = (team_id,)
        if limit is not None:
            sql += " LIMIT %s"
            params = (team_id, int(limit))
        rows = await self._db.execute(sql, params=params, fetch=True)
        return [self._row_to_entity(row) for row in rows]

    async def list_for_agent_context(
        self, agent_id: str, limit: Optional[int] = None
    ) -> List[Artifact]:
        """
        Return what this agent should be aware of: its own pinned artifacts
        UNION the artifacts of every team it belongs to, freshest first.

        This is the widest of the three surfaces and the one whose failure is
        easiest to miss. An over-narrow result does not raise or look wrong —
        the agent simply never learns a teammate's artifact exists, so it
        cannot pick the work up, and collaboration quietly degrades to what it
        was before the team workspace existed.

        Membership comes from `team_members`, NOT from the owning user. One
        user owns many teams; keying on the user would hand every team's
        artifacts to every agent that user owns, which is the cross-team leak.
        The subquery lives here rather than in the caller so no caller can
        supply the wrong team list.

        Args:
            agent_id: The agent whose context is being built.
            limit: Cap the result; None (default) returns all.

        Returns:
            Private-pinned ∪ team artifacts, most recently updated first.
        """
        sql = (
            "SELECT * FROM instance_artifacts WHERE "
            + _AGENT_CONTEXT_WHERE
            + " ORDER BY updated_at DESC"
        )
        params: tuple = (agent_id, agent_id)
        if limit is not None:
            sql += " LIMIT %s"
            params = (agent_id, agent_id, int(limit))
        rows = await self._db.execute(sql, params=params, fetch=True)
        return [self._row_to_entity(row) for row in rows]

    @staticmethod
    def _context_filters(
        kind: str, team_id: str, title_contains: str
    ) -> tuple:
        """Filter SQL + params shared by search/count so the two can never
        disagree about what "matching" means."""
        sql = ""
        params: list = []
        if kind:
            sql += " AND kind = %s"
            params.append(kind)
        if team_id:
            sql += " AND team_id = %s"
            params.append(team_id)
        if title_contains:
            # ESCAPE '!' — NOT backslash: in a MySQL string literal backslash
            # is itself an escape, so ESCAPE '\' arrives as an unterminated
            # string → 1064 on every call, while SQLite (no backslash
            # handling) accepts it — the exact class of bug the local suite
            # can never see (review #334 r2 C1). '!' reads identically on
            # both dialects. Escape the escape char FIRST, then the LIKE
            # metacharacters, or 'a!b' double-escapes.
            escaped = (
                title_contains.replace("!", "!!")
                .replace("%", "!%")
                .replace("_", "!_")
            )
            sql += " AND title LIKE %s ESCAPE '!'"
            params.append(f"%{escaped}%")
        return sql, params

    async def count_agent_context_filtered(
        self,
        agent_id: str,
        *,
        kind: str = "",
        team_id: str = "",
        title_contains: str = "",
    ) -> int:
        """COUNT of `search_agent_context`'s result set (for page math)."""
        fsql, fparams = self._context_filters(kind, team_id, title_contains)
        rows = await self._db.execute(
            "SELECT COUNT(*) AS n FROM instance_artifacts WHERE "
            + _AGENT_CONTEXT_WHERE + fsql,
            params=(agent_id, agent_id, *fparams),
            fetch=True,
        )
        return int(rows[0]["n"]) if rows else 0

    async def search_agent_context(
        self,
        agent_id: str,
        *,
        kind: str = "",
        team_id: str = "",
        title_contains: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> List[Artifact]:
        """The list_artifacts tool surface: the SAME visibility predicate as
        list_for_agent_context with filters and paging pushed into SQL —
        page size must mean something to the DB, not be a Python slice over
        a full pull (review #334 I10). Filters only NARROW.

        `title_contains` is matched with LIKE; `%`/`_` metacharacters in the
        needle are escaped so a literal "a_b" cannot match "axb"."""
        fsql, fparams = self._context_filters(kind, team_id, title_contains)
        sql = (
            "SELECT * FROM instance_artifacts WHERE "
            + _AGENT_CONTEXT_WHERE + fsql
            + " ORDER BY updated_at DESC LIMIT %s OFFSET %s"
        )
        rows = await self._db.execute(
            sql,
            params=(agent_id, agent_id, *fparams, int(limit), int(offset)),
            fetch=True,
        )
        return [self._row_to_entity(row) for row in rows]

    async def list_file_paths_for_heal_scope(
        self, agent_id: str, team_id: Optional[str]
    ) -> set:
        """file_paths of LIVE artifacts in a heal scan scope.

        Heal must never offer (or silently take) a file that some OTHER
        artifact currently points at — repointing there would collapse two
        artifacts onto one file (edits through either would show up in
        both). Scope mirrors heal's search_root: an agent's private
        workspace can only be pointed into by that agent's private rows;
        a team's shared folder only by that team's rows.
        """
        if team_id is None:
            rows = await self._db.execute(
                "SELECT file_path FROM instance_artifacts "
                "WHERE agent_id = %s AND team_id IS NULL",
                params=(agent_id,),
                fetch=True,
            )
        else:
            rows = await self._db.execute(
                "SELECT file_path FROM instance_artifacts WHERE team_id = %s",
                params=(team_id,),
                fetch=True,
            )
        return {r["file_path"] for r in rows if r.get("file_path")}

    async def count_for_agent_context(self, agent_id: str) -> int:
        """COUNT of the `list_for_agent_context` surface — the state block's
        truthful footer needs the total without paying for the rows."""
        sql = "SELECT COUNT(*) AS n FROM instance_artifacts WHERE " + _AGENT_CONTEXT_WHERE
        rows = await self._db.execute(sql, params=(agent_id, agent_id), fetch=True)
        return int(rows[0]["n"]) if rows else 0

    async def list_by_user(self, user_id: str) -> List[Artifact]:
        """
        Return all artifacts owned by a user, across every agent the user owns.

        Ordered by updated_at DESC so the freshest activity surfaces first
        in the Settings → Artifacts management table.

        Args:
            user_id: User scope.

        Returns:
            List of Artifact objects belonging to the user, newest first.
        """
        sql = """
        SELECT * FROM instance_artifacts
        WHERE user_id = %s
        ORDER BY updated_at DESC
        """
        rows = await self._db.execute(sql, params=(user_id,), fetch=True)
        return [self._row_to_entity(row) for row in rows]

    # ── conversion helpers ─────────────────────────────────────────────────────

    def _row_to_entity(self, row: Dict[str, Any]) -> Artifact:
        return Artifact(
            artifact_id=row["artifact_id"],
            agent_id=row["agent_id"],
            user_id=row["user_id"],
            session_id=row.get("session_id"),
            original_session_id=row.get("original_session_id"),
            title=row["title"],
            kind=row["kind"],
            description=row.get("description"),
            pinned=_parse_bool(row.get("pinned", 0)),
            team_id=row.get("team_id"),
            file_path=row.get("file_path") or "",
            size_bytes=int(row.get("size_bytes") or 0),
            content_hash=row.get("content_hash"),
            created_at=parse_dt(row["created_at"]),
            updated_at=parse_dt(row["updated_at"]),
        )

    def _entity_to_row(self, entity: Artifact) -> Dict[str, Any]:
        return {
            "artifact_id": entity.artifact_id,
            "agent_id": entity.agent_id,
            "user_id": entity.user_id,
            "session_id": entity.session_id,
            "original_session_id": entity.original_session_id,
            "title": entity.title,
            "kind": entity.kind,
            "description": entity.description,
            "pinned": 1 if entity.pinned else 0,
            "team_id": entity.team_id,
            "file_path": entity.file_path,
            "size_bytes": entity.size_bytes,
            "content_hash": entity.content_hash,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
        }
