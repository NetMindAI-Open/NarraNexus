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

from xyz_agent_context.agent_framework.llm.transcription.backends.base import (
    TranscriptionBackend,
)
from xyz_agent_context.agent_framework.llm.transcription.backends._audio_url import (
    prepare_public_audio_url,
)
from xyz_agent_context.agent_framework.llm.transcription.credential import (
    TranscriptionBackendKind,
    TranscriptionCredential,
)
from xyz_agent_context.agent_framework.llm.transcription import url_signer


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
# Hard ceiling on transcoded audio. Same 25MB Whisper-style cap as the
# OpenAI backend — NetMind's own limits aren't published but big inputs
# tend to fail anyway, and we'd rather log the reason than time out.

# Audio container that NetMind's worker is happy with. mp3 because
# (a) it's universally produced by ffmpeg with ubiquitous codec
# support, (b) compresses speech well so we don't blow up the public
# URL traffic, and (c) the probe error message itself listed it as
# the recommended format.

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
        public_url = await prepare_public_audio_url(
            file_path,
            file_id=file_id,
            agent_id=agent_id,
            user_id=user_id,
            log_tag="netmind",
        )
        if public_url is None:
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
