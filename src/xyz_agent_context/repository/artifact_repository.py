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
- set_pinned(): toggle pinned flag; pinning clears session_id
- list_by_session(): non-pinned artifacts for a given session
- list_pinned(): pinned artifacts for an agent
- list_by_user(): all artifacts for a user, newest first
- delete() / bulk_delete(): remove artifact rows
- count_for_user() / total_bytes_for_user() / total_bytes_for_agent(): quota queries
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .base import BaseRepository
from xyz_agent_context.schema.artifact_schema import Artifact


def _parse_dt(v: Any) -> datetime:
    """Parse a datetime value from either a datetime object or an ISO string."""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _parse_bool(v: Any) -> bool:
    """Coerce DB integer (0/1) or Python bool to bool."""
    return bool(v)


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
        """
        data: Dict[str, Any] = {
            "file_path": file_path,
            "size_bytes": size_bytes,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if title is not None:
            data["title"] = title[:200]
        if description is not None:
            data["description"] = description
        await self._db.update(self.table_name, {self.id_field: artifact_id}, data)

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

        Returns:
            List of non-pinned Artifact objects belonging to the session.
        """
        sql = """
        SELECT * FROM instance_artifacts
        WHERE agent_id = %s AND session_id = %s AND pinned = 0
        """
        rows = await self._db.execute(sql, params=(agent_id, session_id), fetch=True)
        return [self._row_to_entity(row) for row in rows]

    async def list_pinned(self, agent_id: str) -> List[Artifact]:
        """
        Return pinned artifacts for a given agent.

        Args:
            agent_id: Agent scope.

        Returns:
            List of pinned Artifact objects.
        """
        return await self.find({"agent_id": agent_id, "pinned": 1})

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

    async def count_for_user(self, user_id: str) -> int:
        """
        Return the total artifact count for a user across all their agents.

        Args:
            user_id: User whose quota to count.

        Returns:
            Number of artifacts owned by the user.
        """
        sql = "SELECT COUNT(*) AS n FROM instance_artifacts WHERE user_id = %s"
        rows = await self._db.execute(sql, params=(user_id,), fetch=True)
        if not rows:
            return 0
        return int(rows[0]["n"] or 0)

    async def total_bytes_for_user(self, user_id: str) -> int:
        """
        Return the total size_bytes across all the user's artifacts.

        Pointer model: size_bytes lives directly on instance_artifacts (the
        recursive size of each artifact's root directory at register time), so
        this is a plain SUM with no join.

        Args:
            user_id: User whose quota to sum.

        Returns:
            Sum of size_bytes (0 if user has no artifacts).
        """
        sql = "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM instance_artifacts WHERE user_id = %s"
        rows = await self._db.execute(sql, params=(user_id,), fetch=True)
        if not rows:
            return 0
        return int(rows[0]["total"] or 0)

    async def total_bytes_for_agent(self, agent_id: str) -> int:
        """
        Return the total size_bytes across all the agent's artifacts.

        Args:
            agent_id: Agent whose quota to sum.

        Returns:
            Sum of size_bytes (0 if agent has no artifacts).
        """
        sql = "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM instance_artifacts WHERE agent_id = %s"
        rows = await self._db.execute(sql, params=(agent_id,), fetch=True)
        if not rows:
            return 0
        return int(rows[0]["total"] or 0)

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
            file_path=row.get("file_path") or "",
            size_bytes=int(row.get("size_bytes") or 0),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
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
            "file_path": entity.file_path,
            "size_bytes": entity.size_bytes,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
        }
