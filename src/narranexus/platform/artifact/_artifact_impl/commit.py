"""
@file_name: commit.py
@author: NetMind.AI
@date: 2026-08-20
@description: The ONE commit tail for content-change commit points.

Three paths turn "the entry file's bytes changed" into registry state — a
user edit through PUT /content, a watch-page office edit, and external-edit
detection. They all end the same way: refresh the pointer row (hash / size /
updated_at, file_path UNCHANGED), append one history row, stage one
"updated" event. This module owns that tail so the three callers cannot
drift apart (the size rule or the event shape changing in one copy but not
the others is exactly the class of bug a shared owner prevents).

It never resolves ``file_path`` itself: every caller has already confined the
entry to the owner's roots (``confinement.confine_entry``) and hands that
realpath in. A second, independent ``base + file_path`` join here would be the
same fact expressed twice — and the unconfined copy is the one that followed a
swapped-in symlink into another tenant's tree (2026-10-28).
"""

from __future__ import annotations

import os

from narranexus.platform.artifact._artifact_impl.notify import stage_artifact_event
from narranexus.platform.artifact._artifact_impl.registration import (
    _dir_size,
    _record_history,
)
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import Artifact
from narranexus.platform.utils.db.database import AsyncDatabaseClient


def entry_size_bytes(abs_entry: str, file_path: str) -> int:
    """size_bytes under registration's semantics: an entry at the workspace
    top level counts the file alone; an entry in a dedicated directory counts
    the whole directory (sibling assets included)."""
    workspace_top = os.path.dirname(file_path) in ("", ".")
    if workspace_top:
        return os.path.getsize(abs_entry)
    return _dir_size(os.path.dirname(abs_entry))


async def commit_content_refresh(
    db: AsyncDatabaseClient,
    artifact: Artifact,
    *,
    abs_entry: str,
    new_hash: str,
    history_action: str,
    external: bool = False,
) -> Artifact:
    """Commit a content change on ``artifact``'s UNMOVED entry file.

    ``abs_entry`` is the entry's realpath as returned by ``confine_entry`` —
    the caller proves ownership, this function only measures and records.
    Refreshes hash/size/updated_at through the repository, appends one
    history row with ``history_action`` (user_edited / external_edited),
    and stages one "updated" event (``external`` marks detector-sourced
    commits). Returns the refreshed row.
    """
    repo = ArtifactRepository(db)
    size_bytes = entry_size_bytes(abs_entry, artifact.file_path)

    await repo.update_pointer(
        artifact.artifact_id,
        file_path=artifact.file_path,  # a content commit never moves the pointer
        size_bytes=size_bytes,
        content_hash=new_hash,
    )
    updated = await repo.get_by_id(artifact.artifact_id)
    await _record_history(
        repo,
        artifact_id=artifact.artifact_id,
        agent_id=artifact.agent_id,
        file_path=artifact.file_path,
        size_bytes=size_bytes,
        action=history_action,
    )
    if updated is not None:
        await stage_artifact_event(db, action="updated", artifact=updated, external=external)
    return updated if updated is not None else artifact
