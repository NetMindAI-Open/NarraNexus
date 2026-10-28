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

import hashlib
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from narranexus.platform.utils.workspace_paths import refuse_symlinked_dir

from narranexus.platform.artifact._artifact_impl.notify import stage_artifact_event
from narranexus.platform.artifact._artifact_impl.errors import (
    ArtifactError,
    ArtifactKindMismatch,
    ArtifactNotFound,
    ArtifactPathEscape,
    ArtifactTooLarge,
)
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.repository.team_workspace_repository import (
    ArtifactHistoryRepository,
)
from narranexus.platform.schema.artifact_schema import (
    URL_ARTIFACT_KIND,
    Artifact,
    ArtifactKind,
    CreateArtifactToolResult,
)
from narranexus.platform.settings import settings
from narranexus.platform.utils.workspace_paths import team_shared_dir
from narranexus.platform.utils.office_watch import OFFICE_LIVE_KIND


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
    from narranexus.platform.utils.workspace_paths import agent_workspace_path

    return str(agent_workspace_path(agent_id, user_id, base=os.path.realpath(settings.base_working_path)))


def _relative_to_base(absolute_path: str) -> str:
    """Return path relative to settings.base_working_path.

    The base is realpath-ed like every other root in this module: the entry is
    a realpath, so a symlinked base (macOS ``/var``) would otherwise yield a
    ``../``-laden stored pointer."""
    return os.path.relpath(absolute_path, os.path.realpath(settings.base_working_path))


def _build_url(agent_id: str, artifact_id: str) -> str:
    """Directory-serving URL. The trailing slash makes the entry file's relative
    references (./style.css, ./data.json) resolve under the same path."""
    return f"/api/agents/{agent_id}/artifacts/{artifact_id}/raw/"


def compute_entry_hash(abs_entry: str) -> Optional[str]:
    """sha256 hex of the entry file; None on any IO error.

    Best-effort by contract: the fingerprint upgrades heal's rename
    detection from an extension guess to a verified identity, but a hashing
    failure must never block the registration it accompanies. Entry files
    are capped at MAX_ARTIFACT_BYTES (25 MB), so streaming in 1 MB chunks
    keeps this a sub-perceptible cost on the register path.
    """
    try:
        digest = hashlib.sha256()
        with open(abs_entry, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as e:
        logger.warning(f"content_hash skipped for {abs_entry}: {e}")
        return None


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


def _resolve_entry(
    agent_id: str, user_id: str, entry_path: str, team_id: Optional[str] = None
) -> tuple[str, str]:
    """Resolve and validate the entry file path.

    `entry_path` may be absolute or relative to the agent workspace. Where the
    resolved file must live depends on who the artifact is for:

    * PRIVATE (no team) — inside the agent's own workspace, as before.
    * TEAM — inside THAT team's shared folder, and nowhere else. Not merely
      allowed there: required. A teammate's turn can only reach its own
      workspace, the bus attachment dir and this team's folder, so an entry
      left in the producer's workspace is unreadable to the very people the
      artifact is for — on NexusPower it is denied outright, while claude and
      codex quietly succeed.

    `team_id` reaches here from the server-side identity headers, never from a
    tool argument, so a private turn cannot name a team to reach its files, and
    a sibling team's folder stays out of bounds either way.

    Returns:
        (abs_entry, artifact_root, team_root) — realpath-resolved absolutes.
        `team_root` is the team folder on a team turn, else None. It is
        returned rather than recomputed by callers because it is ALSO a
        container root: an entry sitting directly in it must be treated the way
        an entry at the workspace root is (single-file mode), both for size
        accounting and for raw serving.
        `artifact_root` is `dirname(abs_entry)`; the public-raw route serves
        files under that root for multi-file artifacts. When the entry sits
        directly in the workspace (artifact_root == workspace), the route
        serves only the entry — sub-path requests 404 — so the agent's other
        files are not exposed. The agent gets sibling-asset support by
        putting the entry in a dedicated subdirectory.

    Raises:
        ArtifactPathEscape: file missing / not a file / outside the workspace.
    """
    # Lexical workspace root (base realpath-ed inside workspace_root, tenant
    # tail never resolved) and a hard refusal of a symlinked root: the entry
    # below IS resolved, so a link anywhere on its path fails closed.
    workspace = str(workspace_root(agent_id, user_id))
    try:
        refuse_symlinked_dir(workspace, label="agent workspace")
    except ValueError as e:
        raise ArtifactPathEscape(str(e))
    # Relative paths keep resolving against the agent's OWN workspace: the
    # team folder is reachable by absolute path, never by silently re-basing
    # a relative one onto a root the agent did not name.
    raw = entry_path if os.path.isabs(entry_path) else os.path.join(workspace, entry_path)
    abs_entry = os.path.realpath(raw)

    team_root = None
    if team_id:
        # A TEAM artifact must live in the team's shared folder — not merely
        # be allowed to.
        #
        # The reason is reachability, not tidiness. A teammate opening this
        # work has exactly three roots granted to its turn (see
        # `turn_accessible_roots`): its own workspace, the bus attachment dir,
        # and this team's folder. A file left in the PRODUCER's workspace is in
        # none of them, so NexusPower denies the read while claude and codex,
        # which run no confinement layer, succeed — the three-frameworks-two-
        # behaviours state this feature exists to remove, and a silent defeat
        # of the whole point of a shared workspace.
        #
        # Pointer semantics are untouched: this never copies or moves. The
        # entry simply has to already be somewhere the team can read, and the
        # agent can put it there (the grant covers writes), so the error below
        # is one move and a retry away from success.
        team_root = str(team_shared_dir(user_id, team_id))
        try:
            refuse_symlinked_dir(team_root, label="team folder")
        except ValueError as e:
            raise ArtifactPathEscape(str(e))
        if not abs_entry.startswith(team_root + os.sep):
            raise ArtifactPathEscape(
                f"a team artifact must live in your team's shared folder so "
                f"your teammates can open it. Write the file(s) under "
                f"{team_root} and register the entry from there. (Files in "
                f"your own workspace are private to you — your teammates' "
                f"tools cannot reach them.)"
            )
    elif not abs_entry.startswith(workspace + os.sep):
        raise ArtifactPathEscape(
            "entry_path is outside your agent workspace. Write the artifact "
            "files inside your workspace first, then register the entry file."
        )
    if not os.path.isfile(abs_entry):
        raise ArtifactPathEscape(
            "entry_path does not point at an existing file. Write the file into your workspace first, then register it."
        )

    artifact_root = os.path.dirname(abs_entry)
    return abs_entry, artifact_root, team_root


async def _record_history(
    repo: ArtifactRepository,
    *,
    artifact_id: str,
    agent_id: str,
    file_path: str,
    size_bytes: int,
    action: str,
    event_id: Optional[str] = None,
) -> None:
    """Append one attribution row. Never raises.

    Registration must not fail because bookkeeping did: the artifact itself is
    already correct at this point, and turning a successful registration into
    an error would cost the agent real work to save a log line. A missing row
    degrades the history; a raised exception would degrade the feature.

    `event_id` names the turn that made the change. It arrives from the
    server-side identity headers, so it is a fact rather than an inference —
    matching an artifact to a turn by timestamp would break on the ordinary
    cases (two artifacts in one turn, concurrent turns). None when the caller
    had no event in scope, which degrades the record without failing anything.
    """
    try:
        # Through the repository, not `repo._db`: reaching into another
        # layer's private handle from `_*_impl/` is exactly the coupling the
        # repository seam exists to prevent, and it put a third copy of this
        # table's SQL in a third module.
        await ArtifactHistoryRepository(repo._db).append(
            artifact_id=artifact_id, agent_id=agent_id, file_path=file_path,
            size_bytes=size_bytes, action=action, event_id=event_id,
        )
    except Exception as e:  # noqa: BLE001 — bookkeeping is never flow control
        logger.warning(f"artifact history not recorded for {artifact_id}: {e}")


# ── registration ───────────────────────────────────────────────────────────────


async def register_artifact(
    *,
    repo: ArtifactRepository,
    db,
    agent_id: str,
    user_id: str,
    session_id: Optional[str],
    kind: ArtifactKind,
    entry_path: str,
    title: str,
    description: Optional[str],
    target_artifact_id: Optional[str],
    team_id: Optional[str] = None,
    event_id: Optional[str] = None,
    history_action: str = "updated",
    suppress_notify: bool = False,
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
        team_id: Owning team when this is a team turn, else None (private).
            Comes from the server-side identity headers, never from the model.

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

    abs_entry, artifact_root, team_root = _resolve_entry(
        agent_id, user_id, entry_path, team_id
    )
    workspace = str(workspace_root(agent_id, user_id))  # lexical root (see _resolve_entry)
    try:
        refuse_symlinked_dir(workspace, label="agent workspace")
    except ValueError as e:
        raise ArtifactPathEscape(str(e))
    # Single-file mode when entry sits at the workspace root: account only for
    # the entry file (and serve only the entry — see raw_access.py).
    # Otherwise account for the whole dir so the multi-file artifact's
    # sibling assets are reflected in size_bytes (UI / debugging only).
    # The team root counts as a container root too. Without it, an entry at
    # the team folder charges the artifact the size of EVERYTHING the team has
    # ever shared — and once that crosses MAX_ARTIFACT_BYTES nobody on the team
    # can register anything again. Requiring team artifacts to live there is
    # what put a root in this position that this branch had never seen.
    if artifact_root in (workspace, team_root):
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
    content_hash = compute_entry_hash(abs_entry)
    now = datetime.now(timezone.utc)

    if target_artifact_id is not None:
        existing = await repo.get_by_id(target_artifact_id)
        # Reachability, not just existence. Until this check the branch looked
        # the artifact up by id and validated only its KIND, so any agent that
        # guessed an `art_` id could repoint someone else's artifact at its own
        # file. Ids are eight hex chars; guessing was never the hard part.
        #
        # What "reachable" means differs by scope, and mirrors exactly what the
        # read surfaces already enforce:
        #   * a TEAM artifact belongs to the team, so any turn in THAT team may
        #     update it — picking up a teammate's work is the whole point, and
        #     agent identity is deliberately not the test;
        #   * a PRIVATE artifact belongs to its producer, so only that agent
        #     may update it.
        # A team turn therefore cannot reach a private artifact, and a private
        # turn cannot reach a team's — otherwise `scope="private"`, which is
        # supposed to only NARROW, would become a way to pull an artifact out
        # of the team that owns it.
        #
        # 404-shaped (ArtifactNotFound), like the HTTP routes: a distinct
        # "forbidden" would confirm which ids exist to anyone probing.
        if existing is None or existing.team_id != team_id:
            raise ArtifactNotFound("artifact not found — omit target_artifact_id to register a new one")
        if team_id is None and existing.agent_id != agent_id:
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
            content_hash=content_hash,
        )
        # history_action / suppress_notify only apply on this target branch:
        # heal repoints through here and needs "healed" in the history plus a
        # richer "repointed" event that it stages itself (with old/new path
        # tails) — a plain "updated" on top of it would be a duplicate.
        await _record_history(
            repo, artifact_id=target_artifact_id, agent_id=agent_id,
            file_path=rel_path, size_bytes=size_bytes, action=history_action,
            event_id=event_id,
        )
        refreshed = await repo.get_by_id(target_artifact_id)
        if refreshed is not None and not suppress_notify:
            await stage_artifact_event(db, action="updated", artifact=refreshed)
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
            # The scope is part of the identity, not a detail of it. Without
            # it, an agent that surfaces the same file both privately and to
            # its team gets ONE artifact and the second call's scope is
            # silently discarded — in either direction: a private call can be
            # handed back the team's artifact, or a team registration can be
            # folded into a private one nobody on the team can see. (Caught by
            # an end-to-end probe; the unit tests each used a fresh database,
            # so the collision never arose.)
            if existing.pinned and existing.kind == kind and existing.team_id == team_id:
                await repo.update_pointer(
                    existing.artifact_id,
                    file_path=rel_path,
                    size_bytes=size_bytes,
                    title=title[:200],
                    description=description,
                    content_hash=content_hash,
                )
                await _record_history(
                    repo, artifact_id=existing.artifact_id, agent_id=agent_id,
                    file_path=rel_path, size_bytes=size_bytes, action="updated",
                    event_id=event_id,
                )
                refreshed = await repo.get_by_id(existing.artifact_id)
                if refreshed is not None:
                    await stage_artifact_event(
                        db, action="updated", artifact=refreshed
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
            team_id=team_id,
            file_path=rel_path,
            size_bytes=size_bytes,
            content_hash=content_hash,
            created_at=now,
            updated_at=now,
        )
    )
    await _record_history(
        repo, artifact_id=artifact_id, agent_id=agent_id,
        file_path=rel_path, size_bytes=size_bytes, action="created",
        event_id=event_id,
    )
    created = await repo.get_by_id(artifact_id)
    if created is not None:
        await stage_artifact_event(db, action="registered", artifact=created)
    logger.debug("Registered artifact {} kind={} -> {}", artifact_id, kind, rel_path)
    return CreateArtifactToolResult(
        artifact_id=artifact_id,
        url=_build_url(agent_id, artifact_id),
        created_at=now,
    )
