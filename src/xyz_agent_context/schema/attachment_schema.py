"""
@file_name: attachment_schema.py
@author: Bin Liang
@date: 2026-04-29
@description: User-uploaded attachment data model for chat input

=============================================================================
Belongs to: ChatModule (consumption) + CommonToolsModule (system-prompt
injection) + WS payload (transport)
=============================================================================

Represents a single user-uploaded file referenced from a chat message.

Storage strategy:
- Binary content lives on disk under the agent workspace at
  `{workspace}/user_upload_files/{YYYY-MM-DD}/{file_id}{ext}`.
- This Pydantic model is the in-memory + JSON-memory + WS-payload shape that
  carries the *reference* (file_id + metadata), not the bytes.

Category derivation:
- The `category` field is derived from `mime_type` via `derive_category_from_mime`.
  Categories drive UI rendering branches and the synthesized natural-language
  marker that ChatModule injects into chat_history.

Out-of-scope for MVP (kept as future fields, intentionally not added now):
- `caption: Optional[str]` — vision-LLM-pre-generated description (Path B)
- `transcript: Optional[str]` — STT output for audio/video
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


FILE_ID_PREFIX = "att_"
FILE_ID_REGEX = r"^att_[a-z0-9]{8}$"


class AttachmentCategory(str, Enum):
    """High-level grouping used by UI rendering and prompt synthesis."""
    IMAGE = "image"
    DOCUMENT = "document"   # PDF, DOCX, ODT, ...
    CODE = "code"           # .py, .ts, .json, .md, ...
    DATA = "data"           # CSV, XLSX, TSV, ...
    MEDIA = "media"         # audio/video — readable only after future transcription
    OTHER = "other"


# Image MIME types accepted by the Anthropic Vision API.
SUPPORTED_IMAGE_MIME_TYPES = frozenset({
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
})


def derive_category_from_mime(mime_type: str) -> AttachmentCategory:
    """Map a MIME type to its rendering / capability category.

    Note this is a lossy classification — `category` is a UX hint, not a
    strict capability gate. The agent's built-in `Read` tool re-validates
    file content at read time, so a wrong category here can never grant
    the model access to bytes it shouldn't see.
    """
    mime = (mime_type or "").lower().strip()
    if mime.startswith("image/"):
        return AttachmentCategory.IMAGE
    if mime.startswith("audio/") or mime.startswith("video/"):
        return AttachmentCategory.MEDIA
    if mime in {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.oasis.opendocument.text",
        "application/rtf",
    }:
        return AttachmentCategory.DOCUMENT
    if mime in {
        "text/csv",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.oasis.opendocument.spreadsheet",
    }:
        return AttachmentCategory.DATA
    if (
        mime.startswith("text/")
        or mime in {
            "application/json",
            "application/xml",
            "application/x-yaml",
            "application/javascript",
            "application/typescript",
        }
    ):
        return AttachmentCategory.CODE
    return AttachmentCategory.OTHER


class Attachment(BaseModel):
    """A single user-uploaded file referenced from a chat message."""

    file_id: str = Field(
        ...,
        description="Stable identifier (format: 'att_' + 8 lowercase alphanumerics).",
    )

    mime_type: str = Field(
        ...,
        description="Server-sniffed MIME type (do not trust client Content-Type).",
    )

    original_name: str = Field(
        ...,
        description="User's original filename (display only — never used as a path).",
    )

    size_bytes: int = Field(
        ...,
        ge=0,
        description="File size in bytes.",
    )

    category: AttachmentCategory = Field(
        ...,
        description="High-level grouping derived from mime_type.",
    )

    # ---- Future fields (declared as Optional but unused in MVP) ----
    caption: Optional[str] = Field(
        default=None,
        description="Reserved for Phase 2 vision-LLM-pre-generated description.",
    )

    transcript: Optional[str] = Field(
        default=None,
        description="Reserved for future audio/video transcription output.",
    )

    def synthesize_marker(self, agent_id: str, user_id: str) -> str:
        """Build the natural-language marker that ChatModule appends to
        message content when feeding chat_history to the LLM.

        Strategy: surface the absolute on-disk path so the agent can
        directly call its built-in `Read` tool (which is multimodal and
        natively returns image / PDF / text content blocks). No custom
        MCP tool is needed — Anthropic's SDK ships with the right
        primitive.

        If path resolution fails (file removed / orphan reference), the
        marker still announces the upload but tells the agent the file
        is unavailable so it does not fabricate content.
        """
        # Lazy import: schema must not depend on filesystem-aware utils
        # at import time, but it's fine at call time.
        from xyz_agent_context.utils.attachment_storage import (
            resolve_attachment_path,
        )

        path = resolve_attachment_path(agent_id, user_id, self.file_id)
        path_str = str(path) if path is not None else "<unavailable>"
        kind = self.category.value
        return (
            f"[User uploaded {kind}: name={self.original_name}, "
            f"path={path_str}, mime={self.mime_type} "
            f"— use Read tool to view]"
        )
