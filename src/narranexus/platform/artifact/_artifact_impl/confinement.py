"""
@file_name: confinement.py
@author: Bin Liang
@date: 2026-10-28
@description: Pin a stored artifact ``file_path`` to the roots its OWNER may
touch before anything reads, writes or uploads next to it.

An artifact row's ``file_path`` is user-influenced: it arrives from a bundle
import, from an LLM tool argument, or from an older row written under a
previous layout. Serving / editing / asset-upload used to check only that the
resolved path sat under ``base_working_path`` — which is every tenant's root —
so a crafted pointer (``../<victim>/<agent>/x``) was "inside".

The admitted roots are exactly what the registration rules allow a writer to
produce: that agent's workspace (current nested layout plus the legacy flat
dir names that still hold real data on disk) and, for a team artifact, that
team's shared folder. Not the user's whole root: a sibling agent's tree would
otherwise be servable through the view-token raw route, which carries no JWT.

Roots are LEXICAL (``normpath``), never ``realpath``-ed: every one of those
directories is inside the tenant's own bind mount, so the tenant can replace
it with a symlink; resolving it would turn the link's target into "the
owner's root". The entry, on the other hand, IS realpath-ed, so a link anywhere
on its path lands it outside the lexical roots and fails closed.
"""
from __future__ import annotations

import os
from typing import Optional

from narranexus.platform.utils.workspace_paths import (
    workspace_candidate_relpaths,
    resolve_workspace_relative_file,
    team_shared_dir,
)

from .errors import ArtifactPathEscape


def owner_roots(
    agent_id: str, user_id: str, base: str, *, team_id: Optional[str] = None
) -> tuple[str, ...]:
    """Lexical roots an artifact of (agent_id, user_id[, team_id]) may live under."""
    base_real = os.path.realpath(base)
    roots = {
        os.path.normpath(os.path.join(base_real, rel))
        for rel in workspace_candidate_relpaths(agent_id, user_id)
    }
    if team_id:
        roots.add(os.path.normpath(str(team_shared_dir(user_id, team_id, base=base_real))))
    return tuple(sorted(roots))


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def confine_entry(
    file_path: str, agent_id: str, user_id: str, base: str, *, team_id: Optional[str] = None
) -> str:
    """Resolve ``file_path`` (base-relative, layout-tolerant) and prove it sits
    under one of the owner's roots. Returns the entry's realpath.

    Raises ArtifactPathEscape otherwise — callers map it to their 404/410.
    A root that is itself a symlink is refused outright: a tenant swapping
    its workspace directory for a link is the attack this exists to stop.
    """
    if not file_path:
        raise ArtifactPathEscape("artifact has no content pointer")
    base_real = os.path.realpath(base)
    roots = owner_roots(agent_id, user_id, base_real, team_id=team_id)
    for root in roots:
        if os.path.islink(root):
            raise ArtifactPathEscape(f"owner root is a symlink: {root!r}")
    entry = os.path.realpath(str(resolve_workspace_relative_file(file_path, agent_id, user_id, base_real)))
    if not any(_within(entry, r) for r in roots):
        raise ArtifactPathEscape(f"entry outside owner roots: {file_path!r}")
    return entry
