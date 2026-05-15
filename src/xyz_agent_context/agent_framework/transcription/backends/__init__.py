"""Backend implementations for TranscriptionService.

Concrete backends are imported by :class:`~..service.TranscriptionService`
via the ``backend_kind`` field on :class:`~..credential.TranscriptionCredential`.
"""
from xyz_agent_context.agent_framework.transcription.backends.base import (
    BACKEND_TIMEOUTS_S,
    TranscriptionBackend,
)
from xyz_agent_context.agent_framework.transcription.backends.netmind import (
    NetMindBackend,
)
from xyz_agent_context.agent_framework.transcription.backends.openai_multipart import (
    OpenAIMultipartBackend,
)


__all__ = [
    "BACKEND_TIMEOUTS_S",
    "NetMindBackend",
    "OpenAIMultipartBackend",
    "TranscriptionBackend",
]
