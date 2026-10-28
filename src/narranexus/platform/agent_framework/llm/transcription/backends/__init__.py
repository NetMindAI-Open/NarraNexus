"""Backend implementations for TranscriptionService.

Concrete backends are imported by :class:`~..service.TranscriptionService`
via the ``backend_kind`` field on :class:`~..credential.TranscriptionCredential`.
"""
from narranexus.platform.agent_framework.llm.transcription.backends.base import (
    BACKEND_TIMEOUTS_S,
    TranscriptionBackend,
)
from narranexus.platform.agent_framework.llm.transcription.backends.gateway import (
    GatewayTranscriptionBackend,
)
from narranexus.platform.agent_framework.llm.transcription.backends.netmind import (
    NetMindBackend,
)
from narranexus.platform.agent_framework.llm.transcription.backends.openai_multipart import (
    OpenAIMultipartBackend,
)


__all__ = [
    "BACKEND_TIMEOUTS_S",
    "GatewayTranscriptionBackend",
    "NetMindBackend",
    "OpenAIMultipartBackend",
    "TranscriptionBackend",
]
