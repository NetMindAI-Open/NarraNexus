"""
@file_name: gateway.py
@author: Bin Liang
@date: 2026-07-28
@description: Transcription through the deploy-side STT proxy.

The proxy sits beside the LLM gateway and holds the operator's NetMind
credential, so this backend carries only the USER's wallet key — the same key
their free-tier provider card already uses for chat. That is the whole point:
before this, the operator's STT key had to be present in the backend, mcp and
workers processes for transcription to work at all.

Contract (deliberately one synchronous call — the proxy hides NetMind's
submit-then-poll job API):

    POST {base}/v1/audio/transcriptions
         Authorization: Bearer <the user's wallet key>
         {"audio_url": "...", "language": "en"}
      -> {"text": "..."}

The public-URL requirement survives the proxy: NetMind PULLS the audio, so the
caller still mints a short-TTL signed URL and the resolver still gates this
credential on the deployment having public ingress.

Never-raise contract, same as every backend: any failure returns ``None`` and
the service walks to the next candidate.
"""
from __future__ import annotations

from typing import Optional

import httpx
from loguru import logger

from xyz_agent_context.agent_framework.llm.transcription.backends._audio_url import (
    prepare_public_audio_url,
)
from xyz_agent_context.agent_framework.llm.transcription.backends.base import (
    TranscriptionBackend,
)
from xyz_agent_context.agent_framework.llm.transcription.credential import (
    TranscriptionBackendKind,
    TranscriptionCredential,
)

# The proxy polls upstream on our behalf, so one request can legitimately run
# for a while. Kept just under the service-level budget in BACKEND_TIMEOUTS_S so
# our own timeout is what fires, with a readable message.
_HTTP_TIMEOUT_S = 150.0


class GatewayTranscriptionBackend(TranscriptionBackend):
    """Speech-to-text via the deploy-side proxy, authenticated by wallet key."""

    kind = TranscriptionBackendKind.GATEWAY.value

    async def transcribe(
        self,
        file_path: str,
        credential: TranscriptionCredential,
        *,
        file_id: str,
        agent_id: str,
        user_id: str,
        language: Optional[str] = None,
    ) -> Optional[str]:
        audio_url = await prepare_public_audio_url(
            file_path,
            file_id=file_id,
            agent_id=agent_id,
            user_id=user_id,
            log_tag="transcription[gateway]",
        )
        if audio_url is None:
            return None

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
                resp = await client.post(
                    f"{credential.base_url.rstrip('/')}/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {credential.api_key}"},
                    json={"audio_url": audio_url, "language": language},
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"transcription[gateway]: request failed: {e!r}")
            return None

        if resp.status_code != 200:
            # 401 here means the wallet key is dead — worth its own line,
            # because the remedy (re-login / re-provision) is different from
            # "the upstream hiccuped".
            logger.warning(
                f"transcription[gateway]: HTTP {resp.status_code} "
                f"via {credential.source_tag}"
            )
            return None
        try:
            text = (resp.json() or {}).get("text")
        except Exception:  # noqa: BLE001 — malformed body
            return None
        text = (text or "").strip()
        if not text:
            logger.warning("transcription[gateway]: empty transcript")
            return None
        logger.info(f"transcription[gateway]: {len(text)} chars for {file_id}")
        return text
