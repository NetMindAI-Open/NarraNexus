"""
@file_name: registration.py
@author: Bin Liang
@date: 2026-05-08
@description: DB orchestration for register_artifact (pointer model).

`register_artifact` registers a *pointer* to an entry file the agent already
wrote inside its own workspace. It does NOT copy, move, or write any content —
it validates the path, computes the artifact root directory size, and writes
(or updates) one `instance_artifacts` row.

An artifact = an entry file + the directory it lives in (the "artifact root").
The whole root directory is served by the backend, so a multi-file HTML app can
reference sibling assets (css/js/json/images).

Moved here from `module/common_tools_module/_common_tools_impl/artifact_runner.py`
(2026-07-21) when artifact logic was promoted out of the module's private impl
into this dedicated service package.

Raises structured exceptions (see errors.py) for the MCP wrapper / route layer
to convert into caller-readable errors.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from xyz_agent_context.artifact._artifact_impl.errors import (
    ArtifactError,
    ArtifactKindMismatch,
    ArtifactNotFound,
    ArtifactPathEscape,
    ArtifactTooLarge,
)
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import (
    URL_ARTIFACT_KIND,
    Artifact,
    ArtifactKind,
    CreateArtifactToolResult,
)
from xyz_agent_context.settings import settings
from xyz_agent_context.utils.office_watch import OFFICE_LIVE_KIND


# Per-artifact ceiling: the recursive size of one artifact's root directory.
# Caps a single runaway artifact. This is the ONLY artifact limit — there is no
# per-user count or aggregate-byte quota. The old deploy-mode-aware quota
# (50 local / 10 cloud + 100 MB total) was removed in v1.7.0; users may now
# register any number of artifacts.
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024  # 25 MB

# Office documents (.pptx/.docx/.xlsx) render as a LIVE preview (officecli
# watch) rather than a static file — the renderer opens a watch on the entry
# file and auto-refreshes as the agent edits. It's a first-class artifact kind
# so office docs surface through the same register-as-artifact flow as
# everything else (no separate "live preview" path). OFFICE_LIVE_KIND lives in
# the office-watch util (single source shared with the proxy route).
_OFFICE_EXTS = (".pptx", ".docx", ".xlsx")

ALL_KINDS = frozenset(
    {
        "text/html",
        "application/vnd.echarts+json",
        "text/csv",
        "text/markdown",
        "image/png",
        "image/jpeg",
        "application/pdf",
        OFFICE_LIVE_KIND,
        # URL-tab artifacts: the entry is a small JSON doc (UrlArtifactDoc)
        # written by ArtifactService.open_url, so they register through the
        # same pointer path as everything else.
        URL_ARTIFACT_KIND,
    }
)


# ── path helpers ───────────────────────────────────────────────────────────────


def _new_artifact_id() -> str:
    return "art_" + secrets.token_hex(4)


def workspace_root(agent_id: str, user_id: str) -> str:
    from xyz_agent_context.utils.workspace_paths import agent_workspace_path

    return str(agent_workspace_path(agent_id, user_id, base=settings.base_working_path))


def _relative_to_base(absolute_path: str) -> str:
    """Return path relative to settings.base_working_path."""
    return os.path.relpath(absolute_path, settings.base_working_path)


def _build_url(agent_id: str, artifact_id: str) -> str:
    """Directory-serving URL. The trailing slash makes the entry file's relative
    references (./style.css, ./data.json) resolve under the same path."""
    return f"/api/agents/{agent_id}/artifacts/{artifact_id}/raw/"


def _dir_size(path: str) -> int:
    """Recursive sum of file sizes under `path`. Symlinks are not followed."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _resolve_entry(agent_id: str, user_id: str, entry_path: str) -> tuple[str, str]:
    """Resolve and validate the entry file path.

    `entry_path` may be absolute or relative to the agent workspace. The
    resolved file must be an existing regular file, strictly inside the agent
    workspace.

    Returns:
        (abs_entry, artifact_root) — both realpath-resolved absolute paths.
        `artifact_root` is `dirname(abs_entry)`; the public-raw route serves
        files under that root for multi-file artifacts. When the entry sits
        directly in the workspace (artifact_root == workspace), the route
        serves only the entry — sub-path requests 404 — so the agent's other
        files are not exposed. The agent gets sibling-asset support by
        putting the entry in a dedicated subdirectory.

    Raises:
        ArtifactPathEscape: file missing / not a file / outside the workspace.
    """
    workspace = os.path.realpath(workspace_root(agent_id, user_id))
    raw = entry_path if os.path.isabs(entry_path) else os.path.join(workspace, entry_path)
    abs_entry = os.path.realpath(raw)

    if not abs_entry.startswith(workspace + os.sep):
        raise ArtifactPathEscape(
            "entry_path is outside your agent workspace. Write the artifact "
            "files inside your workspace first, then register the entry file."
        )
    if not os.path.isfile(abs_entry):
        raise ArtifactPathEscape(
            "entry_path does not point at an existing file. Write the file into your workspace first, then register it."
        )

    artifact_root = os.path.dirname(abs_entry)
    return abs_entry, artifact_root


# ── registration ───────────────────────────────────────────────────────────────


async def register_artifact(
    *,
    repo: ArtifactRepository,
    agent_id: str,
    user_id: str,
    session_id: Optional[str],
    kind: ArtifactKind,
    entry_path: str,
    title: str,
    description: Optional[str],
    target_artifact_id: Optional[str],
) -> CreateArtifactToolResult:
    """
    Register a pointer to an entry file the agent wrote in its workspace.

    Workflow:
    1. Validate kind is one of the allowed kinds.
    2. Resolve + validate the entry path (inside workspace, is a file).
    3. Compute size: entry-file size if entry sits at the workspace root
       (single-file artifact), else recursive size of `dirname(entry)`
       (multi-file artifact, where siblings are served too). Reject if it
       exceeds MAX_ARTIFACT_BYTES (per-artifact sanity cap).
    4. New artifact → mint an art_ id and insert a row.
       target_artifact_id → validate it exists and the kind matches, then
       overwrite its pointer in place.
    5. Return CreateArtifactToolResult (artifact_id, url, created_at).

    No filesystem writes. No copy. The DB stores `file_path` = entry file
    relative to settings.base_working_path; `size_bytes` matches what the
    public-raw route serves (single file at root / dir tree otherwise).

    Args:
        repo: ArtifactRepository backed by the active DB client.
        agent_id: Agent that owns the artifact.
        user_id: User that triggered the registration.
        session_id: Session context; None means agent-scoped (auto-pinned).
        kind: One of the ArtifactKind literals.
        entry_path: Absolute or workspace-relative path to the entry file.
        title: Human-readable title (truncated to 200 chars).
        description: Optional freeform description.
        target_artifact_id: If set, re-register onto this existing artifact.

    Returns:
        CreateArtifactToolResult with artifact_id, url, created_at.

    Raises:
        ArtifactError: kind not in the allowed set.
        ArtifactPathEscape: entry path invalid / outside workspace.
        ArtifactTooLarge: artifact size exceeds MAX_ARTIFACT_BYTES.
        ArtifactNotFound: target_artifact_id does not exist.
        ArtifactKindMismatch: target_artifact_id kind differs from requested kind.
    """
    # Office documents always render live, regardless of the kind the caller
    # passed — enables office-as-artifact AND prevents mis-registering a .pptx
    # as text/html (which would render as garbage in the iframe).
    if entry_path.lower().endswith(_OFFICE_EXTS):
        kind = OFFICE_LIVE_KIND

    if kind not in ALL_KINDS:
        raise ArtifactError(
            f"register_artifact does not accept kind={kind!r}. Valid kinds are: "
            f"text/html, application/vnd.echarts+json, text/markdown, text/csv, "
            f"image/png, image/jpeg, application/pdf."
        )

    abs_entry, artifact_root = _resolve_entry(agent_id, user_id, entry_path)
    workspace = os.path.realpath(workspace_root(agent_id, user_id))
    # Single-file mode when entry sits at the workspace root: account only for
    # the entry file (and serve only the entry — see raw_access.py).
    # Otherwise account for the whole dir so the multi-file artifact's
    # sibling assets are reflected in size_bytes (UI / debugging only).
    if artifact_root == workspace:
        size_bytes = os.path.getsize(abs_entry)
    else:
        size_bytes = _dir_size(artifact_root)
    if size_bytes > MAX_ARTIFACT_BYTES:
        raise ArtifactTooLarge(
            f"artifact too large "
            f"({size_bytes / 1024 / 1024:.1f} MB > {MAX_ARTIFACT_BYTES / 1024 / 1024:.0f} MB max). "
            f"Trim the files and register again."
        )

    rel_path = _relative_to_base(abs_entry)
    now = datetime.now(timezone.utc)

    if target_artifact_id is not None:
        existing = await repo.get_by_id(target_artifact_id)
        if existing is None:
            raise ArtifactNotFound("artifact not found — omit target_artifact_id to register a new one")
        if existing.kind != kind:
            raise ArtifactKindMismatch(
                f"cannot re-register a {kind} entry onto target_artifact_id "
                f"{target_artifact_id!r}, which is {existing.kind}. Pass "
                f"kind={existing.kind!r} to update it, or omit target_artifact_id "
                f"to register a new artifact."
            )
        await repo.update_pointer(
            target_artifact_id,
            file_path=rel_path,
            size_bytes=size_bytes,
            title=title[:200],
            description=description,
        )
        logger.debug("Re-registered artifact {} -> {}", target_artifact_id, rel_path)
        return CreateArtifactToolResult(
            artifact_id=target_artifact_id,
            url=_build_url(agent_id, target_artifact_id),
            created_at=existing.created_at,
        )

    if session_id is None:
        # Agent-scoped dedup: the LLM tool path never knows a session_id, so
        # re-registering the same entry file WITHOUT target_artifact_id would
        # mint a second pinned row — a duplicate tab that lives forever
        # (prod 2026-06-30: two pinned "Welcome to NarraNexus" tabs on one
        # agent). Same agent + same entry pointer + agent scope = the same
        # artifact: update it in place and hand the existing id back.
        for existing in await repo.find({"agent_id": agent_id, "file_path": rel_path}):
            if existing.pinned and existing.kind == kind:
                await repo.update_pointer(
                    existing.artifact_id,
                    file_path=rel_path,
                    size_bytes=size_bytes,
                    title=title[:200],
                    description=description,
                )
                logger.debug(
                    "Deduped agent-scoped re-register {} -> {}", existing.artifact_id, rel_path
                )
                return CreateArtifactToolResult(
                    artifact_id=existing.artifact_id,
                    url=_build_url(agent_id, existing.artifact_id),
                    created_at=existing.created_at,
                )

    artifact_id = _new_artifact_id()
    # No session context (LLM-driven calls cannot know a session_id) → default
    # to agent-scoped (pinned=True). Otherwise the artifact would land with
    # session_id=NULL and pinned=False, where neither list_by_session nor
    # list_pinned would surface it.
    await repo.create(
        Artifact(
            artifact_id=artifact_id,
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
            title=title[:200],
            kind=kind,
            description=description,
            pinned=session_id is None,
            file_path=rel_path,
            size_bytes=size_bytes,
            created_at=now,
            updated_at=now,
        )
    )
    logger.debug("Registered artifact {} kind={} -> {}", artifact_id, kind, rel_path)
    return CreateArtifactToolResult(
        artifact_id=artifact_id,
        url=_build_url(agent_id, artifact_id),
        created_at=now,
    )
