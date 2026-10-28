"""
@file_name: heal.py
@author: Bin Liang
@date: 2026-07-21
@description: Broken-pointer recovery for artifacts (the "heal" flow).

Under the pointer model an artifact row can outlive its on-disk entry file
(agent moved/deleted the file, legacy NULL-file_path rows, a register killed
mid-flight). The raw route answers 410 for such rows; the frontend calls heal
to try to reconnect the pointer.

Extracted from `backend/routes/agents/artifacts.py` (2026-07-21) so the
recovery strategy is plain, testable service logic instead of living inside
an HTTP handler.
"""

from __future__ import annotations

import asyncio
import os
from typing import List, Optional

from loguru import logger

from narranexus.platform.artifact._artifact_impl import registration
from narranexus.platform.artifact._artifact_impl.errors import (
    ArtifactError,
    ArtifactNotFound,
)
from narranexus.platform.artifact._artifact_impl.notify import stage_artifact_event
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import Artifact, HealCandidate, HealResult
from narranexus.platform.settings import settings
from narranexus.platform.utils.workspace_paths import (
    resolve_inside_workspace,
    team_shared_dir,
)


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


def _absolutise(path: str, search_root: str) -> str:
    """Anchor a candidate path to the root it was found under.

    Candidates are reported relative to the scan root, and `_resolve_entry`
    resolves a RELATIVE entry_path against the agent's own workspace on
    purpose — it will not silently re-base one onto a root the agent did not
    name. That rule is right for the tool surface and wrong to fight here, so
    heal names the root explicitly instead: a team candidate resolved as-is
    would point into the producer's private workspace and be rejected.

    Absolute paths pass through, so a caller that already resolved one (or a
    frontend echoing an earlier absolute candidate) is unaffected.
    """
    return path if os.path.isabs(path) else os.path.join(search_root, path)


def _confined(path: str, search_root: str) -> Optional[str]:
    """Realpath of a candidate proven to sit inside the LEXICAL ``search_root``,
    or None. A link anywhere on its path (the file itself, a parent folder)
    resolves outside and is dropped — before it is stat-ed or hashed."""
    try:
        return str(resolve_inside_workspace(search_root, path))
    except ValueError:
        return None


def _scan_workspace_for_kind(workspace_root: str, kind: str) -> List[HealCandidate]:
    """Return up to `_HEAL_MAX_CANDIDATES` files in the workspace whose
    extension matches the artifact kind, sorted newest-first by mtime.

    ``workspace_root`` is LEXICAL (built on the realpath-ed base) and is walked
    as-is, never resolved; every file is confined with `_confined` before its
    size/mtime is read, so a link to another tenant's file — or a parent dir
    swapped for one — contributes neither a name nor metadata.
    """
    extensions = _KIND_EXTENSIONS.get(kind)
    if not extensions:
        return []

    found: List[HealCandidate] = []
    if not os.path.isdir(workspace_root):
        return []

    for root, _dirs, files in os.walk(workspace_root, followlinks=False):
        for name in files:
            if not name.lower().endswith(extensions):
                continue
            abs_path = os.path.join(root, name)
            real = _confined(abs_path, workspace_root)
            if real is None:
                continue
            try:
                st = os.stat(real)
            except OSError:
                continue
            found.append(
                HealCandidate(
                    workspace_path=os.path.relpath(abs_path, workspace_root),
                    size_bytes=st.st_size,
                    mtime=st.st_mtime,
                )
            )
    found.sort(key=lambda c: c.mtime, reverse=True)
    return found[:_HEAL_MAX_CANDIDATES]


def _path_tail(rel_path: str, segments: int = 2) -> str:
    """Last N path segments — enough for a user-facing toast to say where the
    pointer went without echoing full workspace layout."""
    parts = [p for p in rel_path.replace(os.sep, "/").split("/") if p]
    return "/".join(parts[-segments:])


async def _repoint(
    *,
    repo: ArtifactRepository,
    db,
    art: Artifact,
    agent_id: str,
    user_id: str,
    entry_abs: str,
    hash_matched: bool,
) -> Artifact:
    """One honest exit for every repoint (auto or user-picked).

    Three moves, always together: re-register with history action "healed"
    (auditable — a guess/verification is never disguised as an intentional
    update), then stage a "repointed" event whose extra carries the old/new
    path tails and whether the content hash verified the candidate — the
    frontend turns that into a toast and an immediate reload. The plain
    "updated" event from the re-register branch is suppressed so consumers
    see one event, with the richer shape.
    """
    old_rel = art.file_path or ""
    result = await registration.register_artifact(
        repo=repo,
        db=db,
        agent_id=agent_id,
        user_id=user_id,
        session_id=None,
        kind=art.kind,
        entry_path=entry_abs,
        title=art.title,
        description=art.description,
        target_artifact_id=art.artifact_id,
        team_id=art.team_id,
        history_action="healed",
        suppress_notify=True,
    )
    healed = await repo.get_by_id(result.artifact_id)
    if healed is not None:
        await stage_artifact_event(
            db,
            action="repointed",
            artifact=healed,
            extra={
                "old": _path_tail(old_rel),
                "new": _path_tail(healed.file_path),
                "hash_matched": hash_matched,
            },
        )
    return healed if healed is not None else art


async def heal_artifact(
    *,
    repo: ArtifactRepository,
    db,
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
    3. Scan the artifact's own root for files whose extension matches the
       artifact kind. Sort by mtime desc, cap at `_HEAL_MAX_CANDIDATES`.
       - 1 candidate → auto-register and return recovered=True.
       - 0 candidates → recovered=False, empty list.
       - >1 candidates → recovered=False, list returned so the caller can
         let the user pick.

    All three steps are scoped to `search_root`, which is the TEAM's shared
    folder for a team artifact and the agent's own workspace otherwise. Every
    step was originally written when only the latter existed, and each broke
    in a different way once team artifacts were required to live in the team
    folder: step 1 declared an intact pointer broken, step 3 offered the
    agent's unrelated private files as replacements for a team artifact, and
    both registrations dropped `team_id` and so failed the ownership check
    with "artifact not found". Together that made recovery of a team artifact
    impossible rather than merely awkward, which is why the root is derived
    once here and every step reads it.

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

    from narranexus.platform.utils.workspace_paths import (
        resolve_existing_workspace,
        resolve_workspace_relative_file,
    )

    # The platform base is resolved; the tenant tail stays LEXICAL (same rule
    # as every other owner-root check, see confinement.py). The workspace and
    # the team folder sit inside the tenant's own mount, so either can be
    # swapped for a link to another tenant's tree: resolving it would make the
    # victim's files "candidates", hash them against art.content_hash, and
    # call a victim file at the same relpath an "already valid" pointer.
    base = os.path.realpath(settings.base_working_path)
    search_root = str(resolve_existing_workspace(agent_id, user_id, base))
    if art.team_id:
        search_root = str(team_shared_dir(user_id, art.team_id, base))
    if os.path.islink(search_root):
        logger.warning(f"heal_artifact: {artifact_id} scope root is a symlink, refusing: {search_root}")
        return HealResult(
            recovered=False,
            candidates=[],
            message=(
                f"{'the team shared folder' if art.team_id else 'the agent workspace'} "
                "is a symbolic link — recovery is refused; replace it with a real folder"
            ),
        )

    # 1. Pointer might already be valid (frontend saw a transient 410).
    if art.file_path:
        existing_abs = _confined(
            str(resolve_workspace_relative_file(art.file_path, agent_id, user_id, base)), search_root
        )
        if existing_abs is not None and os.path.isfile(existing_abs):
            return HealResult(
                recovered=True,
                artifact=art,
                message="pointer is already valid — no action needed",
            )

    # 2. User explicitly picked a candidate from the modal. Same honesty
    #    treatment as auto-repoints (history "healed" + "repointed" event) —
    #    the pointer moved either way; only the chooser differs.
    if entry_path:
        healed = await _repoint(
            repo=repo, db=db, art=art, agent_id=agent_id, user_id=user_id,
            entry_abs=_absolutise(entry_path, search_root),
            hash_matched=False,
        )
        return HealResult(
            recovered=True,
            artifact=healed,
            message=f"re-registered onto {entry_path}",
        )

    # 3. Scan the scope root by kind, then filter and rank candidates.
    candidates = _scan_workspace_for_kind(search_root, art.kind)

    # Guardrail: never offer (or auto-take) a file that some OTHER live
    # artifact currently points at — repointing there would collapse two
    # artifacts onto one file. Both sides are compared as REALPATHS (the
    # candidate via `_confined`, the taken pointer via realpath of its base
    # join) — candidates are scope-relative while DB file_paths are
    # base-relative, and only realpaths agree when an in-workspace link makes
    # two spellings name one file. A candidate that no longer confines is
    # dropped here too (it may have been swapped since the scan).
    taken = await repo.list_file_paths_for_heal_scope(agent_id, art.team_id)
    taken.discard(art.file_path)  # self: its pointer is the broken one
    taken_abs = {os.path.realpath(os.path.join(base, fp)) for fp in taken}
    confined_by_path = {
        c.workspace_path: _confined(c.workspace_path, search_root) for c in candidates
    }
    candidates = [
        c for c in candidates
        if confined_by_path[c.workspace_path] is not None
        and confined_by_path[c.workspace_path] not in taken_abs
    ]

    # Hash tier: a stored fingerprint lets us VERIFY a candidate is the same
    # content (rename detection) instead of guessing by extension. Multiple
    # hash hits mean copies exist — ambiguous intent, so the user picks.
    if art.content_hash and candidates:
        # Hashing a dozen candidates is synchronous chunked IO — run it in a
        # worker thread so the loop keeps serving other requests (#334 I12).
        def _match_by_hash() -> list:
            hits = []
            for c in candidates:
                # Re-confined at read time: only bytes inside the scope root
                # are ever hashed, even if a path was swapped after the scan.
                real = _confined(c.workspace_path, search_root)
                if real is not None and registration.compute_entry_hash(real) == art.content_hash:
                    hits.append(c)
            return hits

        hash_hits = await asyncio.to_thread(_match_by_hash)
        if len(hash_hits) == 1:
            healed = await _repoint(
                repo=repo, db=db, art=art, agent_id=agent_id, user_id=user_id,
                entry_abs=_absolutise(hash_hits[0].workspace_path, search_root),
                hash_matched=True,
            )
            return HealResult(
                recovered=True,
                artifact=healed,
                message=f"recovered by content hash from {hash_hits[0].workspace_path}",
            )
        if len(hash_hits) >= 2:
            return HealResult(
                recovered=False,
                candidates=hash_hits,
                message=(
                    f"{len(hash_hits)} identical copies found — pick which one "
                    "this artifact should follow"
                ),
            )
        # 0 hits: renamed-and-edited or hash unknown → extension tier below.

    if len(candidates) == 1:
        only = candidates[0]
        try:
            healed = await _repoint(
                repo=repo, db=db, art=art, agent_id=agent_id, user_id=user_id,
                entry_abs=_absolutise(only.workspace_path, search_root),
                hash_matched=False,
            )
        except ArtifactError as e:
            logger.warning(f"heal_artifact: single-candidate register failed for {artifact_id}: {e}")
            return HealResult(
                recovered=False,
                candidates=candidates,
                message=f"found one match but it could not be registered: {e}",
            )
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
                f"no matching file found in {'the team shared folder' if art.team_id else 'the agent workspace'} — "
                "regenerate the artifact (re-run the agent) and it will register again"
            ),
        )

    return HealResult(
        recovered=False,
        candidates=candidates,
        message=(f"{len(candidates)} candidate files found — pick the right one to re-register this artifact"),
    )
