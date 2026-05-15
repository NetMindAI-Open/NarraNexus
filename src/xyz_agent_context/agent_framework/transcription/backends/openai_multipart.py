"""
@file_name: openai_multipart.py
@author: Bin Liang
@date: 2026-05-07
@description: OpenAI Whisper /audio/transcriptions multipart backend

Single round-trip multipart upload to ``{base_url}/audio/transcriptions``
with ``response_format=text`` so we get plain text back. Retries once on
429 / 5xx; non-retryable 4xx logs and bails immediately.

Currently usable for: OpenAI official, Yunwu, any self-hosted whisper.cpp
behind an OpenAI-shaped multipart endpoint. NetMind and OpenRouter live
on a different shape and use a different backend.

Browser-recorded webm/opus is accepted directly by official OpenAI Whisper
and by Yunwu, so this backend never transcodes — it sends the bytes
verbatim.

GOTCHA: re-open the file every attempt. httpx exhausts the file handle
on send; reusing the same fp on retry posts 0 bytes. Tests cover this
implicitly (the retry test would 401 if the body were empty), and the
production code uses ``with path.open("rb") as fp:`` inside the retry
loop.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from xyz_agent_context.agent_framework.transcription.backends.base import (
    TranscriptionBackend,
)
from xyz_agent_context.agent_framework.transcription.credential import (
    TranscriptionBackendKind,
    TranscriptionCredential,
)


# Whisper hard limit; second-line guard since backend max_upload_bytes
# default (50 MB) is larger than what OpenAI accepts (25 MB).
WHISPER_MAX_FILE_BYTES = 25 * 1024 * 1024

_HTTPX_TIMEOUT = httpx.Timeout(connect=3.0, read=30.0, write=10.0, pool=3.0)
_MAX_ATTEMPTS = 2  # one initial + one retry on 429/5xx


# Mirrors the SUPPORTED_AUDIO_EXTENSIONS list from the old utility.
# Kept here because both backends consult it (NetMind also rejects
# unknown extensions early — different reason but same predicate).
SUPPORTED_AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a",
    ".wav", ".webm", ".ogg", ".oga", ".opus",
    ".flac", ".amr",
})


_MIME_BY_EXT = {
    ".ogg": "audio/ogg", ".opus": "audio/opus", ".oga": "audio/ogg",
    ".mp3": "audio/mpeg", ".mpeg": "audio/mpeg", ".mpga": "audio/mpeg",
    ".m4a": "audio/mp4", ".mp4": "audio/mp4",
    ".wav": "audio/wav", ".webm": "audio/webm",
    ".flac": "audio/flac", ".amr": "audio/amr",
}


def _guess_mime(path: Path) -> str:
    return _MIME_BY_EXT.get(path.suffix.lower(), "audio/ogg")


class OpenAIMultipartBackend(TranscriptionBackend):
    """OpenAI-protocol Whisper at ``/audio/transcriptions`` (multipart)."""

    kind = TranscriptionBackendKind.OPENAI_MULTIPART.value

    async def transcribe(
        self,
        file_path: str,
        cred: TranscriptionCredential,
        *,
        file_id: str,
        agent_id: str,
        user_id: str,
        language: Optional[str] = None,
    ) -> Optional[str]:
        # OpenAI multipart sends the audio bytes directly — file_id,
        # agent_id, user_id are unused here. They're part of the
        # interface for NetMind's URL-signing path.
        del file_id, agent_id, user_id
        path = Path(file_path)
        if not path.is_file():
            logger.warning(f"openai_multipart: file missing {file_path}")
            return None
        if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            logger.debug(f"openai_multipart: unsupported ext {path.suffix}")
            return None

        size = path.stat().st_size
        if size == 0:
            return None
        if size > WHISPER_MAX_FILE_BYTES:
            logger.warning(
                f"openai_multipart: file too large for Whisper "
                f"({size}B > {WHISPER_MAX_FILE_BYTES}B): {path.name}"
            )
            return None

        url = f"{cred.base_url.rstrip('/')}/audio/transcriptions"
        data = {"model": cred.model, "response_format": "text"}
        if language:
            data["language"] = language
        headers = {"Authorization": f"Bearer {cred.api_key}"}

        async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    with path.open("rb") as fp:
                        files = {"file": (path.name, fp, _guess_mime(path))}
                        resp = await client.post(
                            url, data=data, files=files, headers=headers,
                        )
                except httpx.HTTPError as e:
                    logger.warning(
                        f"openai_multipart attempt {attempt} via {cred.source_tag}: "
                        f"http error {e}"
                    )
                    if attempt == _MAX_ATTEMPTS:
                        return None
                    await asyncio.sleep(0.5)
                    continue

                if resp.status_code == 200:
                    text = resp.text.strip()
                    return text or None

                if resp.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        f"openai_multipart attempt {attempt} via {cred.source_tag}: "
                        f"retryable {resp.status_code} {resp.text[:200]}"
                    )
                    if attempt == _MAX_ATTEMPTS:
                        return None
                    await asyncio.sleep(0.5 * attempt)
                    continue

                logger.error(
                    f"openai_multipart {resp.status_code} via {cred.source_tag}: "
                    f"{resp.text[:200]}"
                )
                return None

        return None
