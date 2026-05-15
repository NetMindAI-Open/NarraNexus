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
    # How the attachment was produced — echoed back from the request's
    # ``source`` query param so the frontend can route rendering by
    # origin without reinventing the discriminator. ``"recording"`` for
    # in-browser AudioRecorder voice memos, ``"upload"`` (or None) for
    # Paperclip / drag-drop / paste. Persisted on the message
    # attachment dict so historical bubbles also pick the right
    # rendering path.
    source: str | None = None
    # Audio transcription fields. Both populated only for audio/* MIME.
    # `transcript` is the actual Whisper output (None if transcription
    # was skipped or failed). `transcription_available` tells the
    # frontend whether the user *could* use transcription at all
    # (i.e. has any OpenAI-protocol provider configured) — used to
    # show a "voice unavailable" toast when relevant. Stays None for
    # non-audio uploads so the frontend doesn't show a toast for PNGs.
    transcript: str | None = None
    transcription_available: bool | None = None
    error: str | None = None


def _audio_video_container_override(sniffed: str, content_type: str | None) -> str:
    """Disambiguate audio-only files in containers that also hold video.

    WebM, Ogg and MP4 are multimedia containers — the file header is
    identical for audio-only and audio+video streams. libmagic looks
    at the container header only and reports ``video/<container>`` for
    everything. If the browser explicitly tagged the upload as
    ``audio/<container>`` for the **same** container, trust the
    browser as the tiebreaker.

    This is contained: misclassification doesn't escalate privileges
    (the file goes to disk, then to Whisper which silently no-ops on
    non-audio bytes). The narrow override unblocks the in-browser
    voice recorder, which records audio-only WebM/Opus through
    MediaRecorder; without this fix, every recorded clip is sniffed
    as ``video/webm`` and skips transcription.
    """
    if not sniffed.startswith("video/") or not content_type:
        return sniffed
    client_main = content_type.split(";", 1)[0].strip().lower()
    if not client_main.startswith("audio/"):
        return sniffed
    sniffed_container = sniffed.split("/", 1)[1]
    client_container = client_main.split("/", 1)[1]
    if sniffed_container == client_container:
        return f"audio/{sniffed_container}"
    return sniffed


def _sniff_mime_type(file: UploadFile, raw_bytes: bytes) -> str:
    """Return a best-effort MIME type, preferring server-side detection.

    We do NOT trust `file.content_type` from the browser as the primary
    signal — it is user-controlled and easy to spoof. Tier order:

    1. python-magic if available (real content sniffing).
    2. mimetypes.guess_type by extension.
    3. The browser-supplied Content-Type as a last resort.

    Whichever tier produces a value, run it through
    ``_audio_video_container_override`` before returning so the
    audio/video container ambiguity (webm / ogg / mp4) is resolved
    consistently across all three. Without this, an environment
    without python-magic falls through to ``mimetypes`` which hardcodes
    ``video/webm`` for `.webm` — masking the override entirely.
    """
    try:
        import magic  # type: ignore[import-not-found]
        sniffed = magic.from_buffer(raw_bytes, mime=True)
        if sniffed:
            return _audio_video_container_override(sniffed, file.content_type)
    except ImportError:
        # python-magic not installed; fall through to extension-based guess
        pass
    except Exception as e:
        logger.debug(f"libmagic sniff failed: {e}; falling back to extension")

    guessed, _ = mimetypes.guess_type(file.filename or "")
    if guessed:
        return _audio_video_container_override(guessed, file.content_type)
    if file.content_type:
        # Last resort — accept the client's claim, but at least it's a string.
        return file.content_type
    return "application/octet-stream"


@router.post("/{agent_id}/attachments", response_model=AttachmentUploadResponse)
async def upload_attachment(
    agent_id: str,
    user_id: str = Query(..., description="User ID"),
    source: str | None = Query(
        None,
        description=(
            "How the attachment was produced. 'recording' means the "
            "in-browser AudioRecorder captured a voice memo and the "
            "client wants Whisper transcription for it. Any other value "
            "(or omitted) means a regular file upload — Paperclip, "
            "drag-drop, paste — and Whisper is skipped even for audio "
            "MIME types. Treating uploaded audio as opaque is intentional: "
            "the user is sharing a file with the agent, not dictating."
        ),
    ),
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

        # Whisper-style transcription runs for ALL audio/* uploads
        # regardless of how the user produced them — `source` is purely
        # a frontend rendering hint. Routed through TranscriptionService
        # which walks an ordered candidate list (user OpenAI official →
        # user NetMind → user other compatible → settings.openai →
        # system-default NetMind cloud free tier). Never-raise contract:
        # any failure returns transcript=None and the upload still
        # succeeds.
        transcript: str | None = None
        transcription_available: bool | None = None
        if mime_type.startswith("audio/"):
            from xyz_agent_context.agent_framework.transcription import (
                TranscriptionService,
            )
            svc = TranscriptionService.instance()
            transcription_available = await svc.is_available(user_id)
            if transcription_available:
                transcript = await svc.transcribe(
                    file_path=str(on_disk),
                    file_id=file_id,
                    agent_id=agent_id,
                    user_id=user_id,
                )
                if transcript:
                    logger.info(
                        f"Attachment transcribed: file_id={file_id} "
                        f"chars={len(transcript)} source={source or 'upload'}"
                    )

        # Normalise source on the way out: anything other than the
        # explicit "recording" tag is reported back as "upload" so the
        # frontend has a single deterministic value to dispatch on.
        # Stored on the message-attachment dict at send time and
        # surfaces again when chat history replays.
        echoed_source = "recording" if source == "recording" else "upload"

        return AttachmentUploadResponse(
            success=True,
            file_id=file_id,
            mime_type=mime_type,
            original_name=file.filename or "upload",
            size_bytes=len(raw_bytes),
            category=category.value,
            source=echoed_source,
            transcript=transcript,
            transcription_available=transcription_available,
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
