"""
@file_name: netmind.py
@author: Bin Liang
@date: 2026-05-07
@description: NetMind /v1/generation Whisper backend (submit + poll)

Why this backend exists separately from openai_multipart
--------------------------------------------------------
NetMind exposes Whisper at a different shape entirely:

  POST {base}/v1/generation
       body: {"model": "openai/whisper",
              "config": {"audio_url": "...", "task": "transcribe", ...}}
       → returns immediately with a job ``id`` and ``status: "pending"``

  GET  {base}/v1/generation/{id}
       → poll; states: pending → initializing → completed | failed

The transcript text is at ``result.data[0].text`` once status is
``completed``. Probe data (see design doc §2.1): a 14-second mp3 went
through pending+processing in ~18s end-to-end, well within our 60s
overall timeout budget.

Two extra wrinkles vs. OpenAI multipart:

1. **NetMind's worker uses Python ``soundfile`` to decode audio.**
   That library accepts wav / flac / ogg / aiff but **NOT webm**, which
   is what every Chromium-based browser produces from MediaRecorder.
   So whenever the input is webm/m4a/mp4 we transcode to mp3 first,
   cache the result next to the original (``{file_id}.mp3``), and serve
   the mp3 on the public route.

2. **NetMind needs a publicly-fetchable URL.** Our chat attachments
   live behind JWT — NetMind can't authenticate. The
   ``url_signer`` module mints short-TTL HMAC URLs that the public
   transcription route validates without auth_middleware.

Never-raise contract
--------------------
Any failure (transcode error, ffmpeg missing, signed-URL secret
unconfigured, http error, polling timeout, ``status="failed"``,
empty/missing transcript field) returns ``None``. The service walks
to the next candidate.
"""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Optional, Tuple

import httpx
from loguru import logger

from xyz_agent_context.agent_framework.transcription.backends.base import (
    TranscriptionBackend,
)
from xyz_agent_context.agent_framework.transcription.backends.openai_multipart import (
    SUPPORTED_AUDIO_EXTENSIONS,
)
from xyz_agent_context.agent_framework.transcription.credential import (
    TranscriptionBackendKind,
    TranscriptionCredential,
)
from xyz_agent_context.agent_framework.transcription import url_signer


# Per-call HTTP timeouts. Submit + each poll are individually short
# because we drive them in a loop with our own overall budget on top.
_HTTPX_TIMEOUT = httpx.Timeout(connect=3.0, read=15.0, write=10.0, pool=3.0)

# Polling cadence picked from the probe — pending→initializing
# transition was caught at 8s and processing→completed at 18s with
# 0.8s polling. The latency vs. burn-rate sweet spot.
_POLL_INTERVAL_S: float = 0.8

# Overall wall-clock budget. The probe-observed 18s typical run leaves
# ~3× headroom for queue spikes; longer than this and we'd rather
# return None and let the user re-record than block the upload route.
_OVERALL_TIMEOUT_S: float = 55.0  # < base.BACKEND_TIMEOUTS_S[netmind]=60s

# Extensions soundfile (NetMind's decoder) accepts as-is.
_SOUNDFILE_NATIVE = frozenset({".mp3", ".wav", ".flac", ".ogg", ".oga", ".aiff"})

# Hard ceiling on transcoded audio. Same 25MB Whisper-style cap as the
# OpenAI backend — NetMind's own limits aren't published but big inputs
# tend to fail anyway, and we'd rather log the reason than time out.
NETMIND_MAX_FILE_BYTES = 25 * 1024 * 1024


# Audio container that NetMind's worker is happy with. mp3 because
# (a) it's universally produced by ffmpeg with ubiquitous codec
# support, (b) compresses speech well so we don't blow up the public
# URL traffic, and (c) the probe error message itself listed it as
# the recommended format.
_TRANSCODED_EXT = ".mp3"
_TRANSCODE_TIMEOUT_S = 30.0


class NetMindBackend(TranscriptionBackend):
    """NetMind /v1/generation Whisper (submit + poll)."""

    kind = TranscriptionBackendKind.NETMIND.value

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
        path = Path(file_path)
        if not path.is_file():
            logger.warning(f"netmind: file missing {file_path}")
            return None
        if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            logger.debug(f"netmind: unsupported ext {path.suffix}")
            return None

        try:
            served_path, variant = await self._prepare_servable(path)
        except _NotTranscodableError as e:
            logger.warning(f"netmind: {e}")
            return None
        if served_path is None:
            return None  # logged inside _prepare_servable

        if served_path.stat().st_size > NETMIND_MAX_FILE_BYTES:
            logger.warning(
                f"netmind: file too large after preparation "
                f"({served_path.stat().st_size}B > {NETMIND_MAX_FILE_BYTES}B): "
                f"{served_path.name}"
            )
            return None

        try:
            token = url_signer.mint(
                file_id=file_id,
                agent_id=agent_id,
                user_id=user_id,
                variant=variant,
            )
        except RuntimeError as e:
            # Secret unconfigured — fail loudly in cloud (resolver should
            # have skipped us) but stay never-raise toward the caller.
            logger.error(f"netmind: cannot mint signed URL: {e}")
            return None

        public_url = url_signer.public_url_for(token)
        if not public_url:
            logger.error(
                "netmind: public_base_url is unset — cannot give NetMind a "
                "URL it can fetch. Resolver should have skipped this candidate."
            )
            return None

        deadline = time.monotonic() + _OVERALL_TIMEOUT_S
        async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
            job_id = await self._submit(client, cred, public_url, language)
            if job_id is None:
                return None

            while True:
                if time.monotonic() >= deadline:
                    logger.warning(
                        f"netmind: overall timeout {_OVERALL_TIMEOUT_S}s "
                        f"waiting on job {job_id}"
                    )
                    return None

                status, payload = await self._poll(client, cred, job_id)
                if status == "completed":
                    return _extract_transcript(payload)
                if status in ("failed", "cancelled", "error"):
                    log_excerpt = ""
                    logs = payload.get("logs") if isinstance(payload, dict) else None
                    if isinstance(logs, list) and logs:
                        last = logs[-1]
                        if isinstance(last, dict):
                            log_excerpt = str(last.get("text", ""))[:300]
                    logger.warning(
                        f"netmind: job {job_id} ended status={status} "
                        f"{log_excerpt!r}"
                    )
                    return None
                if status is None:
                    # poll error — already logged. Don't tight-loop.
                    return None

                await asyncio.sleep(_POLL_INTERVAL_S)

    # ------------------------------------------------------------------
    # File preparation (transcode if needed)
    # ------------------------------------------------------------------

    async def _prepare_servable(
        self, original: Path
    ) -> Tuple[Optional[Path], str]:
        """Return ``(path_to_serve, variant)``.

        ``variant`` matches the URL signer's vocabulary:
          - "original" when NetMind can decode the upload as-is.
          - "mp3" when we transcoded into the cached sibling file.

        On unrecoverable transcode failure returns ``(None, "")`` —
        caller bails. This is **not** an exception path: ffmpeg simply
        not being installed is one of the routine failure modes we
        gracefully degrade through.
        """
        ext = original.suffix.lower()
        if ext in _SOUNDFILE_NATIVE:
            return original, "original"

        cached = original.with_suffix(_TRANSCODED_EXT)
        if cached.exists() and cached.stat().st_size > 0:
            # Reuse a previous transcode — same input always produces the
            # same output, so this is purely a CPU-saver. Stale-cache
            # concerns don't apply: source filenames are immutable
            # ({file_id}.{ext}) and never overwritten.
            return cached, "mp3"

        if shutil.which("ffmpeg") is None:
            logger.warning(
                "netmind: ffmpeg not found on PATH — cannot transcode "
                f"{original.name} for NetMind. Install ffmpeg or skip NetMind."
            )
            return None, ""

        try:
            await _ffmpeg_to_mp3(original, cached)
        except Exception as e:
            logger.error(
                f"netmind: transcode {original.name} → mp3 failed: {e}"
            )
            # Don't leave a partial / 0-byte file behind so a future
            # invocation re-tries cleanly.
            try:
                cached.unlink(missing_ok=True)
            except Exception:
                pass
            return None, ""

        return cached, "mp3"

    # ------------------------------------------------------------------
    # HTTP wrappers
    # ------------------------------------------------------------------

    async def _submit(
        self,
        client: httpx.AsyncClient,
        cred: TranscriptionCredential,
        audio_url: str,
        language: Optional[str],
    ) -> Optional[str]:
        url = f"{cred.base_url.rstrip('/')}/v1/generation"
        body: dict = {
            "model": cred.model,
            "config": {
                "audio_url": audio_url,
                "task": "transcribe",
                "chunk_level": "segment",
                "version": "3",
                "batch_size": 64,
                "num_speakers": None,
            },
        }
        # NetMind's whisper accepts a `language` hint via task config —
        # leaving it out lets the model auto-detect, which is what we
        # want by default.
        if language:
            body["config"]["language"] = language

        headers = {
            "Authorization": f"Bearer {cred.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as e:
            logger.warning(f"netmind submit via {cred.source_tag}: {e}")
            return None

        if resp.status_code != 200:
            logger.error(
                f"netmind submit {resp.status_code} via {cred.source_tag}: "
                f"{resp.text[:200]}"
            )
            return None

        try:
            data = resp.json()
        except Exception as e:
            logger.error(f"netmind submit non-json via {cred.source_tag}: {e}")
            return None

        job_id = data.get("id") if isinstance(data, dict) else None
        if not job_id:
            logger.error(f"netmind submit missing id: {data}")
            return None
        return job_id

    async def _poll(
        self,
        client: httpx.AsyncClient,
        cred: TranscriptionCredential,
        job_id: str,
    ) -> Tuple[Optional[str], dict]:
        url = f"{cred.base_url.rstrip('/')}/v1/generation/{job_id}"
        headers = {"Authorization": f"Bearer {cred.api_key}"}
        try:
            resp = await client.get(url, headers=headers)
        except httpx.HTTPError as e:
            logger.warning(f"netmind poll {job_id}: {e}")
            return None, {}

        if resp.status_code != 200:
            logger.warning(
                f"netmind poll {job_id} {resp.status_code}: {resp.text[:200]}"
            )
            return None, {}

        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"netmind poll {job_id} non-json: {e}")
            return None, {}

        if not isinstance(data, dict):
            return None, {}
        return data.get("status"), data


# ---------------------------------------------------------------------------
# Helpers (module-private)
# ---------------------------------------------------------------------------


class _NotTranscodableError(Exception):
    """Raised when the input file's extension makes transcoding impossible."""


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


def _extract_transcript(payload: dict) -> Optional[str]:
    """Best-effort extraction of ``result.data[0].text`` from a NetMind
    completed-job payload. Returns ``None`` if anything along the path
    is missing — silently, the caller already logged the success status.
    """
    try:
        result = payload.get("result") or {}
        data = result.get("data") or []
        if not isinstance(data, list) or not data:
            return None
        first = data[0]
        if not isinstance(first, dict):
            return None
        text = first.get("text")
        if not isinstance(text, str):
            return None
        text = text.strip()
        return text or None
    except Exception:
        return None
