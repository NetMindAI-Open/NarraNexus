"""
@file_name: credential.py
@author: Bin Liang
@date: 2026-05-07
@description: Resolved transcription credential + backend tag

A ``TranscriptionCredential`` is what the resolver hands to the service:
enough to identify *which* backend implementation to invoke and the
key/url/model that backend needs.

The resolver derives the ``backend_kind`` from the source ``ProviderConfig``
(by base_url) — there is no separate "transcription protocol" field on
the provider, and the user does not see this distinction in the UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TranscriptionBackendKind(str, Enum):
    """Which backend implementation handles a given credential."""

    OPENAI_MULTIPART = "openai_multipart"
    """OpenAI's /audio/transcriptions multipart contract.
    Covers OpenAI official, Yunwu, and any self-hosted whisper.cpp
    behind an OpenAI-shaped endpoint."""

    NETMIND = "netmind"
    """NetMind's /v1/generation submit-then-poll contract with a
    JSON ``{audio_url}`` body. Requires a public-URL service to host
    the audio file the worker fetches."""


@dataclass(frozen=True)
class TranscriptionCredential:
    """One resolved candidate for transcribing audio.

    The resolver returns an ordered list of these; the service tries
    them in order until one succeeds (or all fail → ``None`` upstream).
    """
    backend_kind: TranscriptionBackendKind
    api_key: str
    base_url: str
    model: str
    # Free-form tag for logs only — e.g. "user_provider:user:openai",
    # "user_provider:netmind", "system_default:netmind", "settings.openai".
    # Operators read this to tell which fallback tier produced the
    # credential when triaging a failed transcription.
    source_tag: str
    # When True, this credential is the cloud free tier — the service
    # MUST NOT consult the LLM-token quota for it. Transcription is not
    # currently metered; this flag is the explicit assertion that the
    # decision is intentional, not "we forgot to wire quota in".
    is_system_free_tier: bool = False
