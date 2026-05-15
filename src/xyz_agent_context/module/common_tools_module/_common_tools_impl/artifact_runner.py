"""
@file_name: artifact_runner.py
@author: Bin Liang
@date: 2026-05-08
@description: DB orchestration for register_artifact (pointer model).

`register_artifact` registers a *pointer* to an entry file the agent already
wrote inside its own workspace. It does NOT copy, move, or write any content —
it validates the path, computes the artifact root directory size, enforces the
per-user quota, and writes (or updates) one `instance_artifacts` row.

An artifact = an entry file + the directory it lives in (the "artifact root").
The whole root directory is served by the backend, so a multi-file HTML app can
reference sibling assets (css/js/json/images).

Public function:
- register_artifact(repo, agent_id, user_id, session_id, kind, entry_path,
                    title, description, target_artifact_id) -> CreateArtifactToolResult

Raises structured exceptions for the MCP wrapper / route layer to convert into
caller-readable errors.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import (
    Artifact,
    ArtifactKind,
    CreateArtifactToolResult,
)
from xyz_agent_context.settings import settings


# Per-artifact ceiling: the recursive size of one artifact's root directory.
# Caps a single runaway artifact; the per-user aggregate quota (count + bytes,
# deploy-mode aware) lives in settings and is enforced on top of this.
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024  # 25 MB

ALL_KINDS = frozenset(
    {
        "text/html",
        "application/vnd.echarts+json",
        "text/csv",
        "text/markdown",
        "image/png",
        "image/jpeg",
        "application/pdf",
    }
)


# ── structured exception hierarchy ────────────────────────────────────────────


class ArtifactError(Exception):
    """Base class for artifact_runner errors. The .code attribute maps to HTTP status."""

    code: int = 400


class ArtifactTooLarge(ArtifactError):
    code = 413


class ArtifactNotFound(ArtifactError):
    code = 404


class ArtifactKindMismatch(ArtifactError):
    code = 400


class ArtifactPathEscape(ArtifactError):
    code = 400


class ArtifactQuotaExceeded(ArtifactError):
    code = 507


# ── path helpers ───────────────────────────────────────────────────────────────


def _new_artifact_id() -> str:
    return "art_" + secrets.token_hex(4)


def _workspace_root(agent_id: str, user_id: str) -> str:
    return os.path.join(settings.base_working_path, f"{agent_id}_{user_id}")


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
    workspace = os.path.realpath(_workspace_root(agent_id, user_id))
    raw = entry_path if os.path.isabs(entry_path) else os.path.join(workspace, entry_path)
    abs_entry = os.path.realpath(raw)

    if not abs_entry.startswith(workspace + os.sep):
        raise ArtifactPathEscape(
            "entry_path is outside your agent workspace. Write the artifact "
            "files inside your workspace first, then register the entry file."
        )
    if not os.path.isfile(abs_entry):
        raise ArtifactPathEscape(
            "entry_path does not point at an existing file. Write the file "
            "into your workspace first, then register it."
        )

    artifact_root = os.path.dirname(abs_entry)
    return abs_entry, artifact_root


# ── quota enforcement ──────────────────────────────────────────────────────────


async def _enforce_quota(
    repo: ArtifactRepository,
    user_id: str,
    incoming_bytes: int,
    *,
    is_iteration: bool,
) -> None:
    """Raise ArtifactQuotaExceeded if registering this artifact would exceed the
    per-user count or byte limit.

    Both limits are user-scoped (cross-agent): count is 50 local / 10 cloud
    (deploy-mode aware via settings.is_cloud_mode), bytes is 100 MB regardless
    of mode. `incoming_bytes` is the *delta* — the new artifact root size for a
    fresh register, or (new size − old size) for a re-register — so a
    re-register that shrinks an artifact never trips the byte ceiling.

    The count check is skipped for re-registrations (`is_iteration=True`) — they
    update an existing row in place and don't grow the artifact count.

    Error messages explicitly mention "Settings → Artifacts" so the agent can
    relay actionable guidance, and the frontend can pattern-match the structured
    payload to surface a modal.
    """
    used_bytes = await repo.total_bytes_for_user(user_id)
    byte_limit = settings.artifact_total_bytes_per_user
    if used_bytes + incoming_bytes > byte_limit:
        raise ArtifactQuotaExceeded(
            f"Artifact storage limit reached "
            f"({used_bytes / 1024 / 1024:.1f} MB used + {incoming_bytes / 1024 / 1024:.1f} MB incoming "
            f"> {byte_limit / 1024 / 1024:.0f} MB total per user). "
            f"Manage in Settings → Artifacts."
        )

    if not is_iteration:
        used_count = await repo.count_for_user(user_id)
        count_limit = settings.artifact_count_limit_per_user
        if used_count + 1 > count_limit:
            raise ArtifactQuotaExceeded(
                f"Artifact limit reached ({used_count}/{count_limit}). "
                f"Manage in Settings → Artifacts before registering new ones."
            )


# ── public API ─────────────────────────────────────────────────────────────────


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
    1. Validate kind is one of the 7 allowed kinds.
    2. Resolve + validate the entry path (inside workspace, is a file).
    3. Compute size: entry-file size if entry sits at the workspace root
       (single-file artifact), else recursive size of `dirname(entry)`
       (multi-file artifact, where siblings are served too). Reject if it
       exceeds MAX_ARTIFACT_BYTES.
    4. Enforce the per-user quota (count for new artifacts, bytes always).
    5. New artifact → mint an art_ id and insert a row.
       target_artifact_id → validate it exists and the kind matches, then
       overwrite its pointer in place.
    6. Return CreateArtifactToolResult (artifact_id, url, created_at).

    No filesystem writes. No copy. The DB stores `file_path` = entry file
    relative to settings.base_working_path; `size_bytes` matches what the
    public-raw route serves (single file at root / dir tree otherwise).

    Args:
        repo: ArtifactRepository backed by the active DB client.
        agent_id: Agent that owns the artifact.
        user_id: User that triggered the registration.
        session_id: Session context; None means agent-scoped (auto-pinned).
        kind: One of the 7 ArtifactKind literals.
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
        ArtifactQuotaExceeded: registering would exceed the per-user quota.
        ArtifactNotFound: target_artifact_id does not exist.
        ArtifactKindMismatch: target_artifact_id kind differs from requested kind.
    """
    if kind not in ALL_KINDS:
        raise ArtifactError(
            f"register_artifact does not accept kind={kind!r}. Valid kinds are: "
            f"text/html, application/vnd.echarts+json, text/markdown, text/csv, "
            f"image/png, image/jpeg, application/pdf."
        )

    abs_entry, artifact_root = _resolve_entry(agent_id, user_id, entry_path)
    workspace = os.path.realpath(_workspace_root(agent_id, user_id))
    # Single-file mode when entry sits at the workspace root: account only for
    # the entry file (and serve only the entry — see artifacts_public.py).
    # Otherwise account for the whole dir so the multi-file artifact's
    # sibling assets are paid for by this artifact's quota.
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
            raise ArtifactNotFound(
                "artifact not found — omit target_artifact_id to register a new one"
            )
        if existing.kind != kind:
            raise ArtifactKindMismatch(
                f"cannot re-register a {kind} entry onto target_artifact_id "
                f"{target_artifact_id!r}, which is {existing.kind}. Pass "
                f"kind={existing.kind!r} to update it, or omit target_artifact_id "
                f"to register a new artifact."
            )
        # Quota delta: only the growth (or shrink) relative to the old size.
        await _enforce_quota(
            repo, user_id, size_bytes - existing.size_bytes, is_iteration=True
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

    await _enforce_quota(repo, user_id, size_bytes, is_iteration=False)
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
