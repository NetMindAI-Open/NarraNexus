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

from typing import Optional

from xyz_agent_context.artifact._artifact_impl import (
    heal,
    raw_access,
    registration,
    url_artifact,
)
from xyz_agent_context.artifact._artifact_impl.raw_access import ResolvedRawFile
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import (
    Artifact,
    ArtifactKind,
    CreateArtifactToolResult,
    EmbedMode,
    HealResult,
)
from xyz_agent_context.utils.database import AsyncDatabaseClient


class ArtifactService:
    """Domain operations on artifacts (pointer model).

    Stateless besides the repository handle — cheap to construct per request,
    which matches how routes and the MCP tool acquire their DB client.
    """

    def __init__(self, db: AsyncDatabaseClient):
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
    ) -> CreateArtifactToolResult:
        """Register (or re-register) a pointer to a workspace entry file.

        See `_artifact_impl/registration.py` for the full contract: kind
        whitelist, workspace path confinement, MAX_ARTIFACT_BYTES cap,
        target_artifact_id in-place update semantics.
        """
        return await registration.register_artifact(
            repo=self._repo,
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
            kind=kind,
            entry_path=entry_path,
            title=title,
            description=description,
            target_artifact_id=target_artifact_id,
        )

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
