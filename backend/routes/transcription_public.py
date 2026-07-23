"""
@file_name: transcription_public.py
@author: Bin Liang
@date: 2026-05-07
@description: Public (JWT-bypassed) audio fetch route for transcription workers

NetMind's STT worker fetches audio from a publicly-reachable URL —
there is no JWT bypass we can teach NetMind to use. Instead, the
NetMind backend mints HMAC-signed short-TTL tokens; this route
validates them and streams the corresponding audio file from disk.

Auth model
----------
This route lives under ``/api/public/transcription/`` and is exempted
from JWT in ``backend/auth.py::AUTH_EXEMPT_PREFIXES``. The HMAC token
IS the auth — without it, no path on disk is reachable. Tokens encode
(file_id, agent_id, user_id, variant, exp) and self-expire after ~10
minutes; replay outside that window returns 410.

Variants
--------
Tokens carry a ``variant`` field:

  - ``"original"`` → serve the raw upload bytes from
    ``resolve_attachment_path(...)``.
  - ``"mp3"`` → serve the cached transcoded sibling at
    ``{file_path}.with_suffix(".mp3")``. NetMind backend writes
    this file when the original is webm/m4a/mp4 (formats NetMind's
    soundfile decoder doesn't support).

If the variant file is missing on disk we return 404 — never silently
fall back to the original, because the worker would then try to
decode webm and fail with a confusing "Soundfile malformed" error.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from xyz_agent_context.agent_framework.transcription.url_signer import (
    SignedClaims,
    TokenError,
    verify,
)
from xyz_agent_context.utils.attachment_storage import resolve_attachment_path


router = APIRouter()


_MIME_BY_EXT = {
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/opus",
    ".flac": "audio/flac",
    ".aiff": "audio/aiff",
    ".amr": "audio/amr",
}


def _resolve_path_for_variant(claims: SignedClaims) -> Path | None:
    """Pick the on-disk file the token's ``variant`` points at.

    Returns ``None`` if the original attachment can't be resolved
    (missing / orphan / sandbox violation). Returns the cached mp3
    sibling when ``variant=="mp3"``.
    """
    original = resolve_attachment_path(
        claims.agent_id, claims.user_id, claims.file_id,
    )
    if original is None:
        # Fallback: team voice memos live in the per-user shared bus area, not
        # in an agent's user_upload_files. Still gated by the HMAC token +
        # user_id scoping in resolve_shared_file_by_id.
        from xyz_agent_context.message_bus.attachments import (
            resolve_shared_file_by_id,
        )
        original = resolve_shared_file_by_id(claims.user_id, claims.file_id)
    if original is None:
        return None

    if claims.variant == "original":
        return original

    if claims.variant == "mp3":
        cached = original.with_suffix(".mp3")
        return cached if cached.exists() else None

    # Should not reach — verify() rejects unknown variants — but guard
    # anyway so a future variant addition fails closed.
    return None


@router.get("/audio/{token}")
async def fetch_audio(token: str):
    """Stream the audio file referenced by ``token``.

    Status codes:
      - 200: audio bytes
      - 401: signature mismatch / malformed token (TokenInvalid)
      - 410: signature valid but timestamp expired (TokenExpired)
      - 404: token decoded but file missing on disk (orphan or
        non-existent transcoded variant)
    """
    try:
        claims = verify(token)
    except TokenError as e:
        return JSONResponse(
            status_code=e.http_status,
            content={"error": e.__class__.__name__, "detail": str(e)},
        )

    path = _resolve_path_for_variant(claims)
    if path is None or not path.exists():
        logger.warning(
            f"transcription public: file missing for "
            f"file_id={claims.file_id} variant={claims.variant}"
        )
        raise HTTPException(status_code=404, detail="Attachment not found")

    media_type = _MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path=str(path),
        media_type=media_type,
        # Don't leak our internal file_id naming through the
        # Content-Disposition filename — NetMind doesn't need it.
        filename=f"audio{path.suffix.lower()}",
    )
