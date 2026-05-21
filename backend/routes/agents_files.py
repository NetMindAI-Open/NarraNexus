"""
@file_name: agents_files.py
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

from backend.auth import resolve_current_user_id
from backend.config import settings as backend_settings
from xyz_agent_context.schema import (
    FileInfo,
    FileListResponse,
    FileUploadResponse,
    FileDeleteResponse,
)
from xyz_agent_context.utils.file_safety import (
    enforce_max_bytes,
    ensure_within_directory,
    sanitize_filename,
)


router = APIRouter()


def _get_workspace_path(agent_id: str, user_id: str) -> str:
    """Get Agent-User workspace path."""
    from xyz_agent_context.settings import settings
    return os.path.join(settings.base_working_path, f"{agent_id}_{user_id}")


def _build_tree(workspace_root: Path) -> List[FileInfo]:
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
    """
    def _walk(dir_path: Path) -> List[FileInfo]:
        nodes: List[FileInfo] = []
        try:
            entries = list(os.scandir(dir_path))
        except OSError as e:
            logger.warning(f"workspace scan failed at {dir_path}: {e}")
            return nodes

        for entry in entries:
            if entry.name.startswith("."):
                continue
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
                        children=_walk(entry_path),
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
        return []
    return _walk(workspace_root)


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

    candidate = (workspace / Path(*parts)).resolve()
    workspace_real = workspace.resolve()
    try:
        candidate.relative_to(workspace_real)
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
    other user's workspace tree by changing the query string)."""
    user_id = await resolve_current_user_id(request)
    logger.debug(f"Listing workspace tree for agent: {agent_id}, user: {user_id}")
    try:
        workspace_path = _get_workspace_path(agent_id, user_id)
        tree = _build_tree(Path(workspace_path))
        return FileListResponse(
            success=True, tree=tree, workspace_path=workspace_path
        )
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Error listing workspace tree: {e}")
        return FileListResponse(success=False, error=str(e))


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
    user_id = await resolve_current_user_id(request)
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
    user_id = await resolve_current_user_id(request)
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
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Error uploading file: {e}")
        return FileUploadResponse(success=False, error=str(e))


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
    user_id = await resolve_current_user_id(request)
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
        return FileDeleteResponse(success=False, error=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Error deleting workspace path: {e}")
        return FileDeleteResponse(success=False, error=str(e))
