"""Backend implementations for TranscriptionService.

Concrete backends are imported by :class:`~..service.TranscriptionService`
via the ``backend_kind`` field on :class:`~..credential.TranscriptionCredential`.
"""
from xyz_agent_context.agent_framework.llm.transcription.backends.base import (
    BACKEND_TIMEOUTS_S,
    TranscriptionBackend,
)
from xyz_agent_context.agent_framework.llm.transcription.backends.gateway import (
    GatewayTranscriptionBackend,
)
from xyz_agent_context.agent_framework.llm.transcription.backends.netmind import (
    NetMindBackend,
)
from xyz_agent_context.agent_framework.llm.transcription.backends.openai_multipart import (
    OpenAIMultipartBackend,
)


__all__ = [
    "BACKEND_TIMEOUTS_S",
    "GatewayTranscriptionBackend",
    "NetMindBackend",
    "OpenAIMultipartBackend",
    "TranscriptionBackend",
]
