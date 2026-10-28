"""
@file_name: artifact_service.py
@author: Bin Liang
@date: 2026-07-21
@description: Public protocol layer of the artifact subsystem (Service + Bridge).

`ArtifactService` is the single entry point for artifact business logic —
registration, broken-pointer recovery (heal), and raw-content resolution.
Every consumer (MCP tool, HTTP routes, bootstrap provisioning) constructs it
with a DB client and calls these methods; the concrete logic lives in
`_artifact_impl/` and is never imported directly from outside this package.

Plain CRUD (list / get / delete / pin) intentionally stays on
`ArtifactRepository` — this service carries domain operations, not a
pass-through facade over every repository method.

All failures raise the structured `ArtifactError` hierarchy (errors carry a
`.code` that maps to HTTP status), so MCP and HTTP callers convert them
uniformly.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from narranexus.platform.artifact._artifact_impl import (
    freshness,
    heal,
    raw_access,
    registration,
    url_artifact,
    confinement,
    user_edit,
)
from narranexus.platform.artifact._artifact_impl.notify import stage_artifact_event
from narranexus.platform.artifact._artifact_impl.raw_access import ResolvedRawFile
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import (
    Artifact,
    ArtifactKind,
    CreateArtifactToolResult,
    EmbedMode,
    HealResult,
)
from narranexus.platform.utils.db.database import AsyncDatabaseClient


class ArtifactService:
    """Domain operations on artifacts (pointer model).

    Stateless besides the repository handle — cheap to construct per request,
    which matches how routes and the MCP tool acquire their DB client.
    """

    def __init__(self, db: AsyncDatabaseClient):
        self._db = db
        self._repo = ArtifactRepository(db)

    async def register(
        self,
        *,
        agent_id: str,
        user_id: str,
        session_id: Optional[str],
        kind: ArtifactKind,
        entry_path: str,
        title: str,
        description: Optional[str] = None,
        target_artifact_id: Optional[str] = None,
        team_id: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> CreateArtifactToolResult:
        """Register (or re-register) a pointer to a workspace entry file.

        See `_artifact_impl/registration.py` for the full contract: kind
        whitelist, workspace path confinement, MAX_ARTIFACT_BYTES cap,
        target_artifact_id in-place update semantics.
        """
        return await registration.register_artifact(
            repo=self._repo,
            db=self._db,
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
            kind=kind,
            entry_path=entry_path,
            title=title,
            description=description,
            target_artifact_id=target_artifact_id,
            team_id=team_id,
            event_id=event_id,
        )

    async def bulk_delete(
        self, *, user_id: str, artifact_ids: List[str]
    ) -> Tuple[int, List[str]]:
        """Delete registry rows owned by `user_id`, staging one "deleted"
        event per row.

        Lives here rather than on the repository (despite the "plain CRUD
        stays on ArtifactRepository" rule above) because eventing made
        deletion a domain operation: ownership check, row capture for the
        event payload, delete, stage — callers must not be able to take the
        delete without the event. Workspace files are NOT touched.

        Returns:
            (deleted_count, skipped_not_owned_ids) — unowned or unknown ids
            are reported, never silently deleted.
        """
        # One IN query, not N round-trips (review #334 I10); the route caps
        # the id cardinality (BulkDeleteRequest max_length).
        fetched = await self._repo.get_by_ids(artifact_ids)
        by_id = {a.artifact_id: a for a in fetched if a is not None}
        to_delete: List[Artifact] = []
        skipped: List[str] = []
        for aid in artifact_ids:
            art = by_id.get(aid)
            if art is None or art.user_id != user_id:
                skipped.append(aid)
                continue
            to_delete.append(art)

        deleted = await self._repo.bulk_delete([a.artifact_id for a in to_delete])
        for art in to_delete:
            await stage_artifact_event(
                self._db, action="deleted", artifact=art
            )
        return deleted, skipped

    async def heal(
        self,
        *,
        agent_id: str,
        user_id: str,
        artifact_id: str,
        entry_path: Optional[str] = None,
    ) -> HealResult:
        """Try to recover an artifact whose pointer is broken.

        Strategy (see `_artifact_impl/heal.py`): validate the current pointer
        first; re-register onto a caller-picked `entry_path` if given;
        otherwise scan the workspace by kind and auto-recover on a unique
        match, or return candidates for the user to pick from.
        """
        return await heal.heal_artifact(
            repo=self._repo,
            db=self._db,
            agent_id=agent_id,
            user_id=user_id,
            artifact_id=artifact_id,
            entry_path=entry_path,
        )

    async def open_url(
        self,
        *,
        agent_id: str,
        user_id: str,
        session_id: Optional[str],
        url: str,
        title: Optional[str] = None,
        app_origin: Optional[str] = None,
    ) -> CreateArtifactToolResult:
        """Open a web page as a URL-tab artifact.

        See `_artifact_impl/url_artifact.py`: rejects our own origin
        (self-origin guard) and SSRF-gates the URL, probes its embeddability,
        writes the UrlArtifactDoc, and registers it through the shared pointer
        path. Raises ArtifactError on our own origin / a non-public URL.

        `app_origin` (the browser-visible app origin, supplied by the HTTP
        route) widens the self-origin guard; the MCP path leaves it None and
        relies on settings.public_base_url.
        """
        return await url_artifact.open_url(
            repo=self._repo,
            db=self._db,
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
            url=url,
            title=title,
            app_origin=app_origin,
        )

    async def set_embed_mode(
        self,
        *,
        agent_id: str,
        artifact_id: str,
        mode: Optional[EmbedMode],
    ) -> Artifact:
        """Set (or clear, mode=None) the user's manual embed override on a URL
        tab. Rewrites the on-disk doc; raises ArtifactNotFound if the artifact
        is missing / not this agent's / not a URL tab."""
        return await url_artifact.set_embed_mode(
            repo=self._repo,
            agent_id=agent_id,
            artifact_id=artifact_id,
            mode=mode,
        )

    async def resolve_raw_file(
        self,
        *,
        agent_id: str,
        artifact_id: str,
        file_path: str = "",
    ) -> ResolvedRawFile:
        """Resolve which on-disk file a raw request serves (entry or sibling
        asset), with all path-confinement rules applied.

        See `_artifact_impl/raw_access.py` for the escape/single-file rules
        and the 404-vs-410 error contract.
        """
        return await raw_access.resolve_raw_file(
            repo=self._repo,
            agent_id=agent_id,
            artifact_id=artifact_id,
            file_path=file_path,
        )

    async def save_user_content(
        self,
        *,
        agent_id: str,
        artifact_id: str,
        content: str,
        base_hash: str,
    ) -> Artifact:
        """Persist a user edit from an editing surface and commit it (hash
        refresh + history "user_edited" + staged "updated" event).

        See `_artifact_impl/user_edit.py` for the optimistic-lock and
        atomic-write rules; raises ArtifactEditConflict (409) on a stale
        base_hash with `.current_hash` for the editor's re-base flow.
        """
        return await user_edit.save_user_content(
            self._db,
            agent_id=agent_id,
            artifact_id=artifact_id,
            content=content,
            base_hash=base_hash,
        )

    def resolve_owned_entry(self, artifact: Artifact) -> str:
        """Realpath of ``artifact``'s entry file, proven to sit under its owner's
        roots (that agent's workspace or its team folder). Raises
        ArtifactPathEscape otherwise — the stored ``file_path`` is user-influenced
        and must never be joined to the base blindly (2026-10-28)."""
        from narranexus.platform.settings import settings
        return confinement.confine_entry(
            artifact.file_path, artifact.agent_id, artifact.user_id,
            settings.base_working_path, team_id=artifact.team_id,
        )

    async def commit_office_user_edit(self, *, agent_id: str, artifact_id: str) -> Artifact:
        """Refresh the registry after a watch-page user edit on an office
        artifact (hash + history "user_edited" + event). Idempotent on an
        unchanged hash. See `_artifact_impl/user_edit.py`."""
        return await user_edit.commit_office_user_edit(
            self._db, agent_id=agent_id, artifact_id=artifact_id
        )

    async def refresh_external_state(self, artifact: Artifact) -> str:
        """Detect (and commit) an external change to the artifact's entry
        file. Returns "fresh" / "external" / "missing" — see
        `_artifact_impl/freshness.py` for the two-stage detection and the
        commit-point contract."""
        return await freshness.refresh_external_state(self._db, artifact)
