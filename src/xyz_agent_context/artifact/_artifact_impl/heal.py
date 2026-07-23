"""
@file_name: heal.py
@author: Bin Liang
@date: 2026-07-21
@description: Broken-pointer recovery for artifacts (the "heal" flow).

Under the pointer model an artifact row can outlive its on-disk entry file
(agent moved/deleted the file, legacy NULL-file_path rows, a register killed
mid-flight). The raw route answers 410 for such rows; the frontend calls heal
to try to reconnect the pointer.

Extracted from `backend/routes/agents_artifacts.py` (2026-07-21) so the
recovery strategy is plain, testable service logic instead of living inside
an HTTP handler.
"""

from __future__ import annotations

import os
from typing import List, Optional

from loguru import logger

from xyz_agent_context.artifact._artifact_impl import registration
from xyz_agent_context.artifact._artifact_impl.errors import (
    ArtifactError,
    ArtifactNotFound,
)
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import HealCandidate, HealResult
from xyz_agent_context.settings import settings


# Kind → file extension(s) used by the workspace scan. Multi-extension tuples
# cover the casual variants an agent might pick.
_KIND_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "text/html": (".html", ".htm"),
    "application/vnd.echarts+json": (".json",),
    "text/csv": (".csv",),
    "text/markdown": (".md", ".markdown"),
    "image/png": (".png",),
    "image/jpeg": (".jpg", ".jpeg"),
    "application/pdf": (".pdf",),
    "application/vnd.officecli-live": (".pptx", ".docx", ".xlsx"),
}

# How many candidate files to surface when the auto-recover heuristic
# can't pick a unique winner. Top-N by mtime desc, scoped to the kind's
# extension(s).
_HEAL_MAX_CANDIDATES = 10


def _scan_workspace_for_kind(workspace_root: str, kind: str) -> List[HealCandidate]:
    """Return up to `_HEAL_MAX_CANDIDATES` files in the workspace whose
    extension matches the artifact kind, sorted newest-first by mtime.

    Symlinks are not followed (registration uses realpath at register time,
    so a symlink to /etc/passwd that survives the scan is still rejected
    when we try to register it).
    """
    extensions = _KIND_EXTENSIONS.get(kind)
    if not extensions:
        return []

    found: List[HealCandidate] = []
    base = os.path.realpath(workspace_root)
    if not os.path.isdir(base):
        return []

    for root, _dirs, files in os.walk(base, followlinks=False):
        for name in files:
            if not name.lower().endswith(extensions):
                continue
            abs_path = os.path.join(root, name)
            try:
                st = os.stat(abs_path)
            except OSError:
                continue
            rel = os.path.relpath(abs_path, base)
            found.append(
                HealCandidate(
                    workspace_path=rel,
                    size_bytes=st.st_size,
                    mtime=st.st_mtime,
                )
            )
    found.sort(key=lambda c: c.mtime, reverse=True)
    return found[:_HEAL_MAX_CANDIDATES]


async def heal_artifact(
    *,
    repo: ArtifactRepository,
    agent_id: str,
    user_id: str,
    artifact_id: str,
    entry_path: Optional[str] = None,
) -> HealResult:
    """Try to recover an artifact whose pointer is broken.

    Recovery sequence (each step short-circuits on success):

    1. If the existing `file_path` is set AND the file is on disk: the
       pointer is actually fine — return recovered=True. Useful when the
       frontend's 410 race was a transient miss.
    2. If `entry_path` is given: caller already picked a candidate —
       re-register onto this artifact_id with that path. This is the
       "user picked from the modal" path. A rejected path propagates as
       ArtifactError so the caller can surface the cause.
    3. Scan the agent workspace for files whose extension matches the
       artifact kind. Sort by mtime desc, cap at `_HEAL_MAX_CANDIDATES`.
       - 1 candidate → auto-register and return recovered=True.
       - 0 candidates → recovered=False, empty list.
       - >1 candidates → recovered=False, list returned so the caller can
         let the user pick.

    All registrations go through `registration.register_artifact` with
    `target_artifact_id=artifact_id` so kind/path/sanity-cap rules stay
    identical to the MCP tool and manual-register flows.

    Raises:
        ArtifactNotFound: artifact_id missing or owned by another agent.
        ArtifactError: the explicitly picked entry_path was rejected.
    """
    art = await repo.get_by_id(artifact_id)
    if art is None or art.agent_id != agent_id:
        raise ArtifactNotFound("artifact not found")

    from xyz_agent_context.utils.workspace_paths import (
        resolve_existing_workspace,
        resolve_workspace_relative_file,
    )

    base = os.path.realpath(settings.base_working_path)
    workspace_root = os.path.realpath(str(resolve_existing_workspace(agent_id, user_id, base)))

    # 1. Pointer might already be valid (frontend saw a transient 410).
    if art.file_path:
        existing_abs = os.path.realpath(str(resolve_workspace_relative_file(art.file_path, agent_id, user_id, base)))
        if existing_abs.startswith(workspace_root + os.sep) and os.path.isfile(existing_abs):
            return HealResult(
                recovered=True,
                artifact=art,
                message="pointer is already valid — no action needed",
            )

    # 2. User explicitly picked a candidate from the modal.
    if entry_path:
        result = await registration.register_artifact(
            repo=repo,
            agent_id=agent_id,
            user_id=user_id,
            session_id=None,
            kind=art.kind,
            entry_path=entry_path,
            title=art.title,
            description=art.description,
            target_artifact_id=artifact_id,
        )
        healed = await repo.get_by_id(result.artifact_id)
        return HealResult(
            recovered=True,
            artifact=healed,
            message=f"re-registered onto {entry_path}",
        )

    # 3. Scan workspace by kind.
    candidates = _scan_workspace_for_kind(workspace_root, art.kind)
    if len(candidates) == 1:
        only = candidates[0]
        try:
            result = await registration.register_artifact(
                repo=repo,
                agent_id=agent_id,
                user_id=user_id,
                session_id=None,
                kind=art.kind,
                entry_path=only.workspace_path,
                title=art.title,
                description=art.description,
                target_artifact_id=artifact_id,
            )
        except ArtifactError as e:
            logger.warning(f"heal_artifact: single-candidate register failed for {artifact_id}: {e}")
            return HealResult(
                recovered=False,
                candidates=candidates,
                message=f"found one match but it could not be registered: {e}",
            )
        healed = await repo.get_by_id(result.artifact_id)
        return HealResult(
            recovered=True,
            artifact=healed,
            message=f"auto-recovered from {only.workspace_path}",
        )

    if not candidates:
        return HealResult(
            recovered=False,
            candidates=[],
            message=(
                "no matching file found in the agent workspace — "
                "regenerate the artifact (re-run the agent) and it will register again"
            ),
        )

    return HealResult(
        recovered=False,
        candidates=candidates,
        message=(f"{len(candidates)} candidate files found — pick the right one to re-register this artifact"),
    )
