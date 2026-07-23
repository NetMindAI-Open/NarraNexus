"""
@file_name: mime_sniff.py
@author: NarraNexus
@date: 2026-07-22
@description: The single tiered MIME-type sniffer for uploaded/ingested bytes.

One implementation for every entry point that receives file bytes plus
untrusted naming metadata (browser uploads, IM-channel downloads, team-chat
uploads). Before this module each entry point carried its own copy with
subtly different tiering — a ``.md`` upload could classify as
``application/octet-stream`` on one path and ``text/markdown`` on another,
which changes the derived category (thumbnail vs grey chip) and whether
Whisper transcription runs (``audio/*``).

Tier order (first hit wins):

1. libmagic content sniff (optional dependency) — but an
   ``application/octet-stream`` verdict means "no idea", so it falls through
   instead of masking the later tiers.
2. Extension guess via ``mimetypes.guess_type``.
3. The client/platform-supplied type (browser ``Content-Type`` or IM
   platform metadata) as a last resort — user-controlled, never primary.
4. ``application/octet-stream``.

Tiers 1 and 2 run through ``_audio_video_container_override``: WebM/Ogg/MP4
are containers whose header looks identical for audio-only and audio+video
streams, so libmagic (and the extension table) report ``video/<container>``
for in-browser voice memos. If the client explicitly tagged the SAME
container as ``audio/``, trust it as the tiebreaker — misclassification is
contained (Whisper no-ops on non-audio bytes) and this unblocks recorded
audio-only clips.
"""

from __future__ import annotations

import mimetypes
from typing import Optional

from loguru import logger


def _audio_video_container_override(sniffed: str, client_type: Optional[str]) -> str:
    """Disambiguate audio-only files in containers that also hold video."""
    if not sniffed.startswith("video/") or not client_type:
        return sniffed
    client_main = client_type.split(";", 1)[0].strip().lower()
    if not client_main.startswith("audio/"):
        return sniffed
    sniffed_container = sniffed.split("/", 1)[1]
    client_container = client_main.split("/", 1)[1]
    if sniffed_container == client_container:
        return f"audio/{sniffed_container}"
    return sniffed


def sniff_mime_type(
    raw_bytes: bytes,
    *,
    filename: str = "",
    client_type: Optional[str] = None,
) -> str:
    """Best-effort MIME type for uploaded bytes; see module docstring for tiers.

    Args:
        raw_bytes: The file content (a head slice is enough for libmagic).
        filename: Original file name, used for the extension-guess tier.
        client_type: Client/platform-declared type (browser Content-Type or
            IM-platform metadata) — tiebreaker + last resort, never primary.

    Returns:
        A MIME type string; ``application/octet-stream`` when nothing matches.
    """
    try:
        import magic  # type: ignore[import-not-found]

        sniffed = magic.from_buffer(raw_bytes, mime=True)
        if sniffed and sniffed != "application/octet-stream":
            return _audio_video_container_override(sniffed, client_type)
    except ImportError:
        # python-magic not installed; fall through to the extension guess.
        pass
    except Exception as e:  # noqa: BLE001 — sniffing must never break an upload
        logger.debug(f"libmagic sniff failed: {e}; falling back to extension")

    guessed, _ = mimetypes.guess_type(filename or "")
    if guessed:
        return _audio_video_container_override(guessed, client_type)
    if client_type:
        return client_type
    return "application/octet-stream"
