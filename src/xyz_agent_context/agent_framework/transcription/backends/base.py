"""
@file_name: base.py
@author: Bin Liang
@date: 2026-05-07
@description: TranscriptionBackend abstract base + per-backend timeout matrix

Each concrete backend lives in its own module under this package and
implements a single async method: ``transcribe(file_path, cred, language)``.

The contract is "never raise" — any failure (network, auth, timeout,
malformed audio, missing file, transcoding error) returns ``None``. The
caller (TranscriptionService) walks a candidate list and gives the next
backend a chance only when the current one returned ``None`` for an
error reason; a legitimate empty transcript on a silent clip is still
``None`` and stops the chain (further backends will say the same thing).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from xyz_agent_context.agent_framework.transcription.credential import (
    TranscriptionCredential,
)


# Per-backend overall timeout. Mirrors the probe-derived budget in the
# design doc (§3): OpenAI multipart is one round-trip + at most one
# retry, ~35s is enough; NetMind is submit+poll with a typical 18s
# wall time, ~60s gives headroom for queue spikes.
BACKEND_TIMEOUTS_S: dict[str, float] = {
    "openai_multipart": 35.0,
    "netmind": 60.0,
}


class TranscriptionBackend(ABC):
    """Sole interface every backend implements."""

    #: Used for logs and the BACKEND_TIMEOUTS_S lookup. Concrete subclasses
    #: must set this to the matching ``TranscriptionBackendKind`` string value.
    kind: str = ""

    @abstractmethod
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
        """Return the transcript text, or ``None`` on any failure.

        Implementations MUST NOT raise. ``language`` is an optional ISO
        639-1 hint (e.g. "en", "zh") — backends that don't support it
        ignore the argument silently.

        ``file_path`` is the absolute path to the audio file inside the
        agent workspace. Backends that send the bytes directly
        (OpenAI multipart) just open the file. Backends that need a
        publicly-fetchable URL (NetMind) use ``file_id``, ``agent_id``,
        and ``user_id`` to mint a signed URL via :mod:`..url_signer`.
        Backends that don't need the IDs ignore them.
        """
        ...
