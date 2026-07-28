"""
@file_name: _audio_url.py
@author: Bin Liang
@date: 2026-07-28
@description: Turn a stored attachment into a URL NetMind's worker can fetch.

Shared by both NetMind-backed transcription paths — the legacy direct backend
and the gateway proxy — because the two now differ ONLY in what they do with the
resulting URL. Duplicating this would mean maintaining the transcode rules and
the signing dance twice.

Two constraints drive everything here:

1. **NetMind's worker decodes with Python ``soundfile``**, which accepts
   wav / flac / ogg / aiff but NOT webm — and webm is what every Chromium
   MediaRecorder produces. So non-native inputs are transcoded to mp3 and cached
   next to the original.

2. **NetMind PULLS the audio.** Our attachments live behind JWT, which their
   worker cannot present, so we mint a short-TTL HMAC URL that the public
   transcription route validates without auth_middleware. This survives the
   proxy: the proxy forwards a URL, it does not carry bytes.

Never raises: every failure path (unsupported extension, missing ffmpeg,
transcode error, unsigned deployment, oversized file) returns ``None`` and the
caller walks to the next candidate.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Optional

from loguru import logger

from xyz_agent_context.agent_framework.llm.transcription import url_signer
from xyz_agent_context.agent_framework.llm.transcription.backends.openai_multipart import (
    SUPPORTED_AUDIO_EXTENSIONS,
)

# Extensions soundfile (NetMind's decoder) accepts as-is.
_SOUNDFILE_NATIVE = frozenset({".mp3", ".wav", ".flac", ".ogg", ".oga", ".aiff"})

# Audio container NetMind's worker is happy with. mp3 because (a) ffmpeg
# produces it everywhere, (b) it compresses speech well so we don't blow up
# public-URL traffic, and (c) their own error message recommended it.
_TRANSCODED_EXT = ".mp3"
_TRANSCODE_TIMEOUT_S = 30.0

# Hard ceiling on prepared audio. Same 25MB Whisper-style cap as the OpenAI
# backend — NetMind's own limit isn't published, but big inputs fail anyway and
# we'd rather log the reason than time out.
MAX_AUDIO_BYTES = 25 * 1024 * 1024


async def prepare_public_audio_url(
    file_path: str,
    *,
    file_id: str,
    agent_id: str,
    user_id: str,
    log_tag: str,
) -> Optional[str]:
    """Return a signed, publicly-fetchable URL for this audio, or ``None``."""
    path = Path(file_path)
    if not path.is_file():
        logger.warning(f"{log_tag}: file missing {file_path}")
        return None
    if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
        logger.debug(f"{log_tag}: unsupported ext {path.suffix}")
        return None

    served, variant = await _prepare_servable(path, log_tag)
    if served is None:
        return None  # logged inside

    size = served.stat().st_size
    if size > MAX_AUDIO_BYTES:
        logger.warning(
            f"{log_tag}: file too large after preparation "
            f"({size}B > {MAX_AUDIO_BYTES}B): {served.name}"
        )
        return None

    try:
        token = url_signer.mint(
            file_id=file_id, agent_id=agent_id, user_id=user_id, variant=variant
        )
    except RuntimeError as e:
        logger.error(f"{log_tag}: cannot mint signed URL: {e}")
        return None

    public_url = url_signer.public_url_for(token)
    if not public_url:
        logger.error(
            f"{log_tag}: public_base_url is unset — cannot give the worker a "
            f"URL it can fetch. The resolver should have skipped this candidate."
        )
        return None
    return public_url


async def _prepare_servable(
    original: Path, log_tag: str
) -> tuple[Optional[Path], str]:
    """``(path_to_serve, variant)``; ``variant`` matches the signer's vocabulary.

    ``(None, "")`` on unrecoverable failure — NOT an exception path: ffmpeg
    simply not being installed is a routine degradation.
    """
    if original.suffix.lower() in _SOUNDFILE_NATIVE:
        return original, "original"

    cached = original.with_suffix(_TRANSCODED_EXT)
    if cached.exists() and cached.stat().st_size > 0:
        # Same input always yields the same output, and source filenames are
        # immutable ({file_id}.{ext}) and never overwritten — so this is a pure
        # CPU saver with no stale-cache concern.
        return cached, "mp3"

    if shutil.which("ffmpeg") is None:
        logger.warning(
            f"{log_tag}: ffmpeg not found on PATH — cannot transcode "
            f"{original.name}. Install ffmpeg or use an OpenAI-shaped provider."
        )
        return None, ""

    try:
        await _ffmpeg_to_mp3(original, cached)
    except Exception as e:  # noqa: BLE001 — degradation, not a crash
        logger.error(f"{log_tag}: transcode {original.name} -> mp3 failed: {e}")
        # Don't leave a partial / 0-byte file behind, so a later call retries.
        try:
            cached.unlink(missing_ok=True)
        except Exception:
            pass
        return None, ""

    return cached, "mp3"


async def _ffmpeg_to_mp3(src: Path, dst: Path) -> None:
    """Run ffmpeg to convert ``src`` into mp3 at ``dst``.

    Uses libmp3lame (universally available in ffmpeg builds), 64 kbps
    mono — voice-band audio doesn't need stereo, and lower bitrate is
    a meaningful win for the public-URL transfer to NetMind.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-b:a", "64k",
        str(dst),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_TRANSCODE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"ffmpeg timed out after {_TRANSCODE_TIMEOUT_S}s")

    if proc.returncode != 0:
        msg = (stderr or b"").decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"ffmpeg exit {proc.returncode}: {msg}")
    if not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced empty output")
