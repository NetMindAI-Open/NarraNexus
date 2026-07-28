"""Transcription provider abstraction.

Public entry point: :class:`TranscriptionService`. The upload route
imports it via this package, never reaches into the submodules.

Capability is **derived** from existing user/system providers — there
is no separate transcription-provider concept in user-facing config.
"""
from xyz_agent_context.agent_framework.llm.transcription.credential import (
    TranscriptionBackendKind,
    TranscriptionCredential,
)
from xyz_agent_context.agent_framework.llm.transcription.service import (
    TranscriptionAvailability,
    TranscriptionService,
)


__all__ = [
    "TranscriptionAvailability",
    "TranscriptionBackendKind",
    "TranscriptionCredential",
    "TranscriptionService",
]
