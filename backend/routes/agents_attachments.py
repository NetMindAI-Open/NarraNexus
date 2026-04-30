"""
@file_name: agents_attachments.py
@author: Bin Liang
@date: 2026-04-29
@description: Chat-message attachment upload + preview routes

Endpoints
---------
- POST /{agent_id}/attachments?user_id=...
    multipart upload, returns file_id + sniffed metadata
- GET  /{agent_id}/attachments/{file_id}/raw?user_id=...
    streams the original bytes (frontend uses this for image thumbnails)

These are intentionally separate from `agents_files.py` (which manages
flat workspace files used as agent tool inputs). Chat attachments live
under a date-partitioned subdir and carry an index mapping file_id →
on-disk path.
"""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
from pydantic import BaseModel

from backend.config import settings as backend_settings
from xyz_agent_context.schema.attachment_schema import (
    derive_category_from_mime,
)
from xyz_agent_context.utils.attachment_storage import (
    resolve_attachment_path,
    store_uploaded_attachment,
)


router = APIRouter()


class AttachmentUploadResponse(BaseModel):
    """Returned to the frontend after a successful upload."""
    success: bool
    file_id: str | None = None
    mime_type: str | None = None
    original_name: str | None = None
    size_bytes: int | None = None
    category: str | None = None
    error: str | None = None


def _sniff_mime_type(file: UploadFile, raw_bytes: bytes) -> str:
    """Return a best-effort MIME type, preferring server-side detection.

    We do NOT trust `file.content_type` from the browser — it is
    user-controlled and easy to spoof. Instead:

    1. Try to use python-magic if available (real content sniffing).
    2. Fall back to mimetypes.guess_type by extension.
    3. Final fallback to application/octet-stream.
    """
    try:
        import magic  # type: ignore[import-not-found]
        sniffed = magic.from_buffer(raw_bytes, mime=True)
        if sniffed:
            return sniffed
    except ImportError:
        # python-magic not installed; fall through to extension-based guess
        pass
    except Exception as e:
        logger.debug(f"libmagic sniff failed: {e}; falling back to extension")

    guessed, _ = mimetypes.guess_type(file.filename or "")
    if guessed:
        return guessed
    if file.content_type:
        # Last resort — accept the client's claim, but at least it's a string.
        return file.content_type
    return "application/octet-stream"


@router.post("/{agent_id}/attachments", response_model=AttachmentUploadResponse)
async def upload_attachment(
    agent_id: str,
    user_id: str = Query(..., description="User ID"),
    file: UploadFile = File(..., description="File to upload as a chat attachment"),
):
    """Upload a single file to be referenced by an upcoming chat message."""
    logger.info(
        f"Uploading attachment '{file.filename}' agent={agent_id} user={user_id}"
    )

    try:
        raw_bytes = await file.read()

        # Defensive size cap. The backend setting governs all uploads;
        # the agent reads files via its built-in Read tool which has its
        # own per-image cap, so oversize images simply fail to view but
        # do not break the upload pipeline.
        max_bytes = backend_settings.max_upload_bytes
        if len(raw_bytes) > max_bytes:
            return AttachmentUploadResponse(
                success=False,
                error=(
                    f"File exceeds the maximum upload size of "
                    f"{max_bytes // (1024 * 1024)} MB"
                ),
            )

        mime_type = _sniff_mime_type(file, raw_bytes)
        category = derive_category_from_mime(mime_type)

        file_id, on_disk = store_uploaded_attachment(
            agent_id,
            user_id,
            raw_bytes=raw_bytes,
            original_name=file.filename or "upload",
            mime_type=mime_type,
        )
        logger.info(
            f"Attachment stored: file_id={file_id} mime={mime_type} "
            f"size={len(raw_bytes)} path={on_disk}"
        )

        return AttachmentUploadResponse(
            success=True,
            file_id=file_id,
            mime_type=mime_type,
            original_name=file.filename or "upload",
            size_bytes=len(raw_bytes),
            category=category.value,
        )

    except ValueError as e:
        # raised by sanitize_filename / ensure_within_directory
        logger.warning(f"Upload rejected: {e}")
        return AttachmentUploadResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Error uploading attachment: {e}")
        return AttachmentUploadResponse(success=False, error=str(e))


@router.get("/{agent_id}/attachments/{file_id}/raw")
async def get_attachment_raw(
    agent_id: str,
    file_id: str,
    user_id: str = Query(..., description="User ID"),
):
    """Stream the original attachment bytes.

    Used by the frontend to render image thumbnails inline. The path is
    resolved through the same sandbox check the marker-synthesis path
    uses, so a bad / orphaned file_id returns 404 instead of escaping
    the workspace.
    """
    path = resolve_attachment_path(agent_id, user_id, file_id)
    if path is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": "Attachment not found"},
        )

    # Best-effort MIME — same logic as the index, but we re-derive at serve
    # time so a missing/old index doesn't block the stream.
    mime, _ = mimetypes.guess_type(str(path))
    return FileResponse(
        path=str(path),
        media_type=mime or "application/octet-stream",
        filename=Path(path).name,
    )
