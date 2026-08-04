"""
@file_name: files.py
@author: NexusAgent
@date: 2026-05-26
@description: Read-only file-tree API for Manyfold's per-agent file browser.

Manyfold's "Show file tree" button on the chat header expects a small
set of HTTP endpoints under ``/manyfold/agents/<agent_id>/files/`` that
list, stat, and stream files inside the agent's workspace directory.
Other Manyfold-supported frameworks (claude-code / openclaw / hermes)
implement this via a DUFS WebDAV sidecar; we don't want to ship DUFS
in the NarraNexus image (extra binary, supervisord plumbing, port
allocation), so we implement just the read paths in FastAPI directly.

Write surface (2026-08-03, managed-attachment design §Q7): exactly ONE
write endpoint — ``POST .../files/write`` — added so the platform's
chat-attachment ingest can land inbound files inside the agent
workspace (it is the missing half of the attachment channel; the
platform's ``narraNexusCtx`` wires its ``write`` here and flips
``chatCapabilitiesByFramework.narranexus.attachments``). mkdir / mv /
rm stay unexposed: workspace mutation beyond dropping attachment bytes
remains the agent's own business.

Registered only when ``ENABLE_MANYFOLD_API=1`` (see ``backend/main.py``).
Auth: same gateway-token middleware as the rest of ``/manyfold/...``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.settings import settings as core_settings
from xyz_agent_context.utils.db.db_factory import get_db_client


router = APIRouter()


# Stream files in 64KB chunks. Big enough to keep syscall overhead low,
# small enough that the gateway/proxy doesn't buffer the whole file in
# memory before the first byte reaches the client.
_READ_CHUNK_BYTES = 64 * 1024

# Reject attempts to read absurdly large files via this endpoint —
# Manyfold's preview pane is for source / Markdown / log inspection,
# not for downloading a 5GB agent artifact. Operators who need that
# can use kubectl cp or the bundle export flow.
_MAX_READ_BYTES = 64 * 1024 * 1024


def _require_manyfold_auth(request: Request) -> None:
    """Mirror of `manyfold_agents._require_manyfold_auth`. Duplicated
    rather than imported so this module has no inbound dependency on
    its sibling file — keeps the file-tree API self-contained."""
    if not getattr(request.state, "manyfold_authed", False):
        raise HTTPException(
            status_code=401,
            detail="missing or invalid MANYFOLD_GATEWAY_TOKEN",
        )


async def _resolve_workspace_root(agent_id: str) -> tuple[Path, str]:
    """Resolve ``agent_id`` to ``(workspace_root_abs, user_id)``.

    Looks up the agent's ``created_by`` user from the ``agents`` table
    so the workspace directory naming (``<agent_id>_<user_id>``) is
    correct without the caller having to know NarraNexus's internal
    user-id convention.

    Raises HTTP 404 if the agent doesn't exist.
    """
    db = await get_db_client()
    row = await db.get_one("agents", {"agent_id": agent_id})
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"agent {agent_id!r} not found",
        )
    user_id = row.get("created_by") or ""
    if not user_id:
        raise HTTPException(
            status_code=500,
            detail=f"agent {agent_id!r} has no created_by user",
        )
    from xyz_agent_context.utils.workspace_paths import resolve_existing_workspace
    workspace = resolve_existing_workspace(
        agent_id, user_id, str(core_settings.base_working_path)
    )
    return workspace, user_id


def _safe_resolve(workspace_root: Path, raw_path: str) -> Path:
    """Resolve a caller-supplied ``path`` to an absolute Path, with
    path-traversal protection.

    ``raw_path`` can be:
      - empty / "/" / the workspace root itself — returns the root
      - absolute starting at the workspace root — accepted as-is
      - any other absolute path or one containing ``..`` — rejected

    We resolve the workspace root via ``resolve(strict=False)`` so that
    a non-existent workspace dir (first call before the agent ever
    wrote a file) still returns a Path the caller can stat-and-empty.
    """
    root = workspace_root.resolve(strict=False)
    if not raw_path or raw_path == "/" or raw_path == str(root):
        return root

    candidate = Path(raw_path)
    if candidate.is_absolute():
        target = candidate.resolve(strict=False)
    else:
        target = (root / raw_path).resolve(strict=False)

    # Strict prefix check: target must equal root OR be inside root.
    # Using `Path.is_relative_to` keeps us safe against symlinks that
    # resolve outside the workspace (resolve() above already follows
    # symlinks, so we're checking the post-resolve path).
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"path escapes workspace: {raw_path!r}",
        )
    return target


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------


class FileEntry(BaseModel):
    """Single directory entry. Matches the field names Manyfold's
    `FsEntry` / `K8sFilesClient` consume so the client doesn't have to
    re-map. ``mtime`` is epoch seconds (NOT milliseconds) for parity
    with the other framework adapters."""

    name: str
    type: str  # "file" | "dir" | "link"
    size: int
    mtime: int
    mode: str = "644"


class FileRoot(BaseModel):
    """A logical root the file tree should show. Today we only expose
    a single root (the agent's workspace dir), but the shape is a list
    so a future caller can add e.g. an /artifacts subdir as its own
    root without a breaking change."""

    id: str
    label: str
    path: str
    writable: bool
    supports_listing: bool = Field(alias="supportsListing")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/manyfold/agents/{agent_id}/files/roots")
async def list_roots(agent_id: str, request: Request):
    """Return the set of file roots the agent exposes.

    For NarraNexus we always emit exactly one read-only root — the
    agent's workspace dir. Surfacing it via this endpoint (rather than
    hard-coding on the Manyfold side) means a future addition (logs
    root, artifacts root, etc.) only needs a change here.
    """
    _require_manyfold_auth(request)
    workspace, _ = await _resolve_workspace_root(agent_id)
    root = FileRoot(
        id="workspace",
        label="Workspace",
        path=str(workspace),
        writable=False,
        supports_listing=True,
    )
    return {"roots": [root.model_dump(by_alias=True)]}


@router.get("/manyfold/agents/{agent_id}/files/list")
async def list_dir(
    agent_id: str,
    request: Request,
    path: str = Query(default="", description="Absolute path to list."),
):
    """Return the immediate children of a directory.

    Returns empty entries (200, not 404) for a non-existent workspace
    so the chat header's file tree can render cleanly the first time a
    user opens it — before the agent has produced any files.
    """
    _require_manyfold_auth(request)
    workspace, _ = await _resolve_workspace_root(agent_id)
    target = _safe_resolve(workspace, path)

    if not target.exists():
        if target == workspace.resolve(strict=False):
            # Workspace dir hasn't been created yet — treat as empty
            # rather than 404 so the UI doesn't show a broken state
            # for a freshly-provisioned agent.
            return {"entries": []}
        raise HTTPException(
            status_code=404,
            detail=f"path does not exist: {path!r}",
        )
    if not target.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"not a directory: {path!r}",
        )

    entries: list[dict[str, Any]] = []
    for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        try:
            stat = child.stat()
        except OSError as exc:
            # Don't fail the whole listing for one broken entry; log
            # and skip so the rest of the dir still renders.
            logger.warning(
                f"[manyfold-files] stat failed for {child}: {exc}"
            )
            continue
        entries.append(
            FileEntry(
                name=child.name,
                type=(
                    "dir"
                    if child.is_dir()
                    else "link"
                    if child.is_symlink()
                    else "file"
                ),
                size=stat.st_size,
                mtime=int(stat.st_mtime),
            ).model_dump()
        )
    return {"entries": entries}


@router.get("/manyfold/agents/{agent_id}/files/stat")
async def stat_file(
    agent_id: str,
    request: Request,
    path: str = Query(..., description="Absolute path to stat."),
):
    """Return a single entry's metadata. Used by the chat UI to size
    a file preview pane before fetching the body."""
    _require_manyfold_auth(request)
    workspace, _ = await _resolve_workspace_root(agent_id)
    target = _safe_resolve(workspace, path)
    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail=f"path does not exist: {path!r}",
        )
    stat = target.stat()
    entry = FileEntry(
        name=target.name,
        type=(
            "dir"
            if target.is_dir()
            else "link"
            if target.is_symlink()
            else "file"
        ),
        size=stat.st_size,
        mtime=int(stat.st_mtime),
    )
    return {"entry": entry.model_dump()}


@router.get("/manyfold/agents/{agent_id}/files/read")
async def read_file(
    agent_id: str,
    request: Request,
    path: str = Query(..., description="Absolute path of the file to read."),
):
    """Stream a file's contents to the caller.

    Rejects directories (400), missing paths (404), and files larger
    than ``_MAX_READ_BYTES`` (413) — Manyfold's preview pane is for
    text inspection, not unbounded blob transfer.
    """
    _require_manyfold_auth(request)
    workspace, _ = await _resolve_workspace_root(agent_id)
    target = _safe_resolve(workspace, path)
    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail=f"path does not exist: {path!r}",
        )
    if target.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"path is a directory: {path!r}",
        )
    stat = target.stat()
    if stat.st_size > _MAX_READ_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"file too large: {stat.st_size} bytes "
                f"(limit {_MAX_READ_BYTES})"
            ),
        )

    def iterfile():
        with target.open("rb") as fh:
            while True:
                chunk = fh.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                yield chunk

    # application/octet-stream covers everything safely; Manyfold's UI
    # decides how to render (Markdown / source / hex) based on filename.
    return StreamingResponse(
        iterfile(),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(stat.st_size),
            # Disable proxy buffering so large files start streaming
            # immediately — matches the X-Accel-Buffering header we
            # set on the chat SSE stream.
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# POST /manyfold/agents/{agent_id}/files/write — attachment ingest landing
# ---------------------------------------------------------------------------

# Defensive cap mirroring the platform's own per-file chat-upload limit
# order of magnitude; the platform enforces its caps before sending, this
# is the second line.
_MAX_WRITE_BYTES = 64 * 1024 * 1024


@router.post("/manyfold/agents/{agent_id}/files/write")
async def write_file(
    agent_id: str,
    request: Request,
    path: str = Query(..., description="workspace-relative destination path"),
    overwrite: bool = Query(
        False,
        description=(
            "Passing true authorizes replacing an existing file at path. "
            "Default false: this is the API's only write door, and a caller "
            "naming a colliding path should mean it (attachment ingest "
            "writes unique per-message directories, so collisions there "
            "are retries, not accidents)."
        ),
    ),
):
    """Write the request body's raw bytes to ``path`` inside the agent
    workspace (parents created). The single write surface of this API —
    exists so the platform's chat-attachment ingest can land inbound IM /
    UI files for the agent to Read (managed-attachment design §Q7).

    Same guard rails as the read endpoints: gateway auth, workspace-scoped
    ``_safe_resolve`` (403 on escape), plus a size cap enforced WHILE
    streaming the body (a over-cap upload is rejected at the boundary, not
    after buffering it into memory). Returns the workspace-relative path
    so the caller can reference it on the wire.
    """
    _require_manyfold_auth(request)
    workspace, _user_id = await _resolve_workspace_root(agent_id)
    root = workspace.resolve(strict=False)
    target = _safe_resolve(workspace, path)
    if target == root:
        raise HTTPException(
            status_code=400,
            detail="path must name a file inside the workspace, not the root",
        )
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_WRITE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"body too large: exceeds {_MAX_WRITE_BYTES} bytes"
                ),
            )
        chunks.append(chunk)
    body = b"".join(chunks)
    if target.exists() and target.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"path is a directory: {path!r}",
        )
    if target.exists() and not overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"file exists and overwrite=false: {path!r}",
        )

    def _write() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)

    await asyncio.to_thread(_write)
    rel = str(target.relative_to(root))
    logger.info(
        f"[manyfold-files:{agent_id}] wrote {len(body)} bytes to {rel!r}"
    )
    return {"ok": True, "path": rel, "size": len(body)}
