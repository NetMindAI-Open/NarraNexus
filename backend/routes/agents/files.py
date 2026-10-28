"""
@file_name: files.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent-workspace file management routes (tree view + download + delete).

Provides:
- GET    /{agent_id}/files                 — recursive workspace tree (dotfolders hidden)
- GET    /{agent_id}/files/raw?path=…      — download / preview a single file
- POST   /{agent_id}/files                 — upload a file to the workspace root
- DELETE /{agent_id}/files/{path:path}     — delete a file or directory (subpath ok)

Pointer-model rework (2026-05-14): the listing endpoint went from flat (top-
level files only) to a recursive tree so the workspace config viewer can
browse the directory structure the agent actually builds. Dotfolders are
filtered out at the server so the UI never has to know about `.cache`, `.git`,
etc. Raw / delete endpoints accept nested relative paths so the viewer can
download / preview / remove anything in the tree.
"""

import mimetypes
import os
import shutil
from pathlib import Path, PurePosixPath
from typing import List

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse
from loguru import logger

from backend.config import settings as backend_settings
from backend.routes._ownership import require_owned_workspace_agent
from narranexus.platform.schema import (
    FileInfo,
    FileListResponse,
    FileUploadResponse,
    FileDeleteResponse,
)
from narranexus.platform.utils.file_safety import (
    enforce_max_bytes,
    ensure_within_directory,
    sanitize_filename,
)


router = APIRouter()

# Hard ceilings for the recursive tree (2026-10-28): the listing used to walk
# to the bottom with no bound — one request returned 124 MB of other tenants'
# trees during the incident, and even an honest, file-heavy workspace could
# pin the backend. Past either cap the walk stops and the response says so.
MAX_TREE_NODES = 5000
MAX_TREE_DEPTH = 16


def _get_workspace_path(agent_id: str, user_id: str) -> str:
    """Get Agent-User workspace path (current layout, legacy-flat fallback).

    Only call with an ``agent_id`` that passed
    :func:`backend.routes._ownership.require_owned_workspace_agent`.
    """
    from narranexus.platform.utils.workspace_paths import (
        refuse_symlinked_dir,
        resolve_existing_workspace,
    )
    workspace = resolve_existing_workspace(agent_id, user_id)
    try:
        refuse_symlinked_dir(workspace, label="agent workspace")
    except ValueError:
        # The owner replaced its workspace dir with a link (to anywhere);
        # nothing under it is served, listed or deleted.
        raise HTTPException(404, "agent not found")
    return str(workspace)


def _build_tree(workspace_root: Path) -> tuple[List[FileInfo], bool]:
    """Walk the workspace recursively, returning a tree of FileInfo nodes.

    Dotfolders (any directory whose name starts with `.`) are skipped entirely
    — we don't recurse into them and they don't appear in the tree. The UI
    should never have to know about `.cache`, `.git`, hidden tooling state, etc.
    Hidden files (regular files whose name starts with `.`) are also skipped
    for the same reason.

    Symlinks are not followed when recursing (`os.scandir(follow_symlinks=False)`)
    to keep cycles and out-of-workspace escapes off the table; symlinks
    themselves appear as their target type via the default scandir behaviour.

    Sort order: directories first, then files, each alphabetically — so the
    tree renders predictably.

    Returns ``(tree, truncated)``: ``truncated`` is True only when something
    visible was actually left out — MAX_TREE_NODES ran out with entries still
    pending, or a directory at MAX_TREE_DEPTH has at least one non-hidden
    entry below it (an empty folder at the last level is a complete listing).
    The workspace panel (frontend ``FileUpload``) renders a "partial listing"
    note from it, so files past the cap read as "not shown", never as gone.
    """
    budget = {"nodes": MAX_TREE_NODES, "truncated": False}

    def _walk(dir_path: Path, depth: int = 0) -> List[FileInfo]:
        nodes: List[FileInfo] = []
        if depth >= MAX_TREE_DEPTH:
            # Look before claiming truncation: only a visible entry below the
            # ceiling is content the listing omits.
            try:
                with os.scandir(dir_path) as it:
                    if any(not e.name.startswith(".") for e in it):
                        budget["truncated"] = True
            except OSError as e:
                logger.warning(f"workspace scan failed at {dir_path}: {e}")
            return nodes
        try:
            entries = list(os.scandir(dir_path))
        except OSError as e:
            logger.warning(f"workspace scan failed at {dir_path}: {e}")
            return nodes

        for entry in entries:
            if entry.name.startswith("."):
                continue
            if budget["nodes"] <= 0:
                budget["truncated"] = True
                break
            budget["nodes"] -= 1
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue

            entry_path = Path(entry.path)
            rel = str(entry_path.relative_to(workspace_root))

            if entry.is_dir(follow_symlinks=False):
                nodes.append(
                    FileInfo(
                        name=entry.name,
                        path=rel,
                        is_dir=True,
                        size=0,
                        modified_at=str(stat.st_mtime),
                        children=_walk(entry_path, depth + 1),
                    )
                )
            elif entry.is_file(follow_symlinks=False):
                nodes.append(
                    FileInfo(
                        name=entry.name,
                        path=rel,
                        is_dir=False,
                        size=stat.st_size,
                        modified_at=str(stat.st_mtime),
                        children=None,
                    )
                )
            # symlinks / sockets / fifos are silently ignored

        nodes.sort(key=lambda n: (not n.is_dir, n.name.lower()))
        return nodes

    if not workspace_root.exists():
        return [], False
    tree = _walk(workspace_root)
    return tree, budget["truncated"]


def _resolve_within_workspace(workspace: Path, rel_path: str) -> Path:
    """Resolve a workspace-relative path to an absolute path, refusing escape.

    Rejects:
    - empty path
    - null bytes / explicit `..` segments
    - any segment that starts with `.` (dotfolder / hidden file)
    - paths whose realpath escapes the workspace root

    Returns the realpath-resolved absolute path. The caller decides what to do
    with it (read, delete, ...).
    """
    if not rel_path or "\x00" in rel_path:
        raise HTTPException(400, "invalid path")
    posix = PurePosixPath(rel_path.replace("\\", "/"))
    parts = posix.parts
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        raise HTTPException(400, "invalid path: dotfolders and traversal are not allowed")

    # The workspace root is LEXICAL (base already realpath-ed by
    # resolve_existing_workspace); only the candidate is resolved, so any
    # symlink on its path — including a swapped workspace dir — lands it
    # outside and is refused.
    candidate = (workspace / Path(*parts)).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        raise HTTPException(400, "invalid path: outside the agent workspace")
    return candidate


@router.get("/{agent_id}/files", response_model=FileListResponse)
async def list_workspace_files(
    agent_id: str,
    request: Request,
):
    """Return the agent workspace as a recursive directory tree.

    Identity comes from auth_middleware (JWT in cloud, X-User-Id header in
    local); the URL no longer accepts a ``user_id`` param to avoid the
    cross-account-leak class of bug (a client could otherwise list any
    other user's workspace tree by changing the query string). The
    ``agent_id`` segment is proven owned by that identity first."""
    user_id = await require_owned_workspace_agent(request, agent_id)
    logger.debug(f"Listing workspace tree for agent: {agent_id}, user: {user_id}")
    try:
        workspace_path = _get_workspace_path(agent_id, user_id)
        tree, truncated = _build_tree(Path(workspace_path))
        if truncated:
            logger.warning(
                f"workspace tree truncated for agent {agent_id}: "
                f">{MAX_TREE_NODES} nodes or >{MAX_TREE_DEPTH} levels"
            )
        return FileListResponse(
            success=True, tree=tree, workspace_path=workspace_path, truncated=truncated
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Error listing workspace tree: {e}")
        return FileListResponse(success=False, error="Failed to list workspace files.")


@router.get("/{agent_id}/files/raw")
async def fetch_workspace_file(
    agent_id: str,
    request: Request,
    path: str = Query(..., description="Workspace-relative path of the file"),
):
    """Stream a single workspace file (for download or inline preview).

    Path is resolved via :func:`_resolve_within_workspace` — dotfolders,
    null bytes, `..` segments, and escapes are all rejected.

    Identity comes from auth_middleware, not the URL — see the listing
    endpoint above for the rationale.
    """
    user_id = await require_owned_workspace_agent(request, agent_id)
    workspace = Path(_get_workspace_path(agent_id, user_id))
    target = _resolve_within_workspace(workspace, path)
    if not target.is_file():
        raise HTTPException(404, "file not found")
    media_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return FileResponse(path=str(target), media_type=media_type, filename=target.name)


@router.post("/{agent_id}/files", response_model=FileUploadResponse)
async def upload_file(
    agent_id: str,
    request: Request,
    file: UploadFile = File(..., description="File to upload"),
):
    """Upload a file to the workspace root.

    Identity comes from auth_middleware, not the URL.
    """
    user_id = await require_owned_workspace_agent(request, agent_id)
    logger.info(f"Uploading file '{file.filename}' for agent: {agent_id}, user: {user_id}")
    try:
        safe_filename = sanitize_filename(file.filename or "", label="filename")
        workspace_path = _get_workspace_path(agent_id, user_id)
        workspace_dir = Path(workspace_path)
        if not workspace_dir.exists():
            workspace_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created workspace directory: {workspace_path}")

        content = await file.read()
        enforce_max_bytes(len(content), backend_settings.max_upload_bytes, label="File")
        filepath = ensure_within_directory(workspace_dir, safe_filename, label="filename")

        with open(filepath, "wb") as f:
            f.write(content)

        logger.info(f"File saved: {filepath} ({len(content)} bytes)")
        return FileUploadResponse(
            success=True,
            filename=safe_filename,
            size=len(content),
            workspace_path=workspace_path,
        )
    except ValueError as e:
        return FileUploadResponse(success=False, error=str(e))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Error uploading file: {e}")
        return FileUploadResponse(success=False, error="Failed to upload file.")


@router.delete("/{agent_id}/files/{path:path}", response_model=FileDeleteResponse)
async def delete_file(
    agent_id: str,
    path: str,
    request: Request,
):
    """Delete a file or a directory (recursively) from the workspace.

    Accepts nested paths (e.g. ``report/index.html`` or ``report``). When the
    resolved path is a directory, ``shutil.rmtree`` removes it and everything
    under it — confine yourself; this is destructive.

    Identity comes from auth_middleware, not the URL.
    """
    user_id = await require_owned_workspace_agent(request, agent_id)
    logger.info(f"Deleting workspace path '{path}' for agent: {agent_id}, user: {user_id}")
    try:
        workspace = Path(_get_workspace_path(agent_id, user_id))
        target = _resolve_within_workspace(workspace, path)
        if not target.exists():
            return FileDeleteResponse(success=False, error=f"path not found: {path}")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        logger.info(f"Workspace path deleted: {target}")
        return FileDeleteResponse(success=True, path=path)
    except HTTPException:
        raise
    except OSError as e:
        logger.exception(f"OS error deleting workspace path: {e}")
        return FileDeleteResponse(success=False, error="Failed to delete the file.")
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Error deleting workspace path: {e}")
        return FileDeleteResponse(success=False, error="Failed to delete the file.")
