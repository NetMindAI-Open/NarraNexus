"""
@file_name: openrouter.py
@author: Bin Liang
@date: 2026-05-13
@description: Driver for OpenRouter aggregator one-key cards.

Same dual-row pattern as NetMind and Yunwu — OpenRouter quick-add
writes one anthropic-protocol row and one openai-protocol row, sharing
a ``linked_group`` and an ``api_key``.

OpenRouter is an aggregator — does not forward Anthropic's
server-side tools.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    EmbeddingConfig,
    OpenAIConfig,
)
from xyz_agent_context.agent_framework.provider_driver.base import _DriverBase
from xyz_agent_context.agent_framework.provider_driver.registry import register


@register
class OpenRouterDriver(_DriverBase):
    """OpenRouter one-key card."""

    @classmethod
    def driver_type(cls) -> str:
        return "openrouter"

    def _is_anthropic_row(self) -> bool:
        return (self.card.protocol or "").lower() == "anthropic"

    def _is_openai_row(self) -> bool:
        return (self.card.protocol or "").lower() == "openai"

    def build_claude_config(self, model: str) -> ClaudeConfig:
        if not self._is_anthropic_row():
            raise NotImplementedError(
                f"OpenRouterDriver instantiated on protocol={self.card.protocol!r} "
                f"cannot serve the agent slot."
            )
        return ClaudeConfig(
            api_key=self.card.api_key,
            base_url=self.card.base_url,
            model=model,
            auth_type=self.card.auth_type or "api_key",
            supports_anthropic_server_tools=False,
        )

    def build_openai_config(self, model: str) -> OpenAIConfig:
        if not self._is_openai_row():
            raise NotImplementedError(
                f"OpenRouterDriver instantiated on protocol={self.card.protocol!r} "
                f"cannot serve the helper_llm slot."
            )
        return OpenAIConfig(
            api_key=self.card.api_key,
            base_url=self.card.base_url,
            model=model,
        )

    def build_embedding_config(self, model: str) -> EmbeddingConfig:
        if not self._is_openai_row():
            raise NotImplementedError(
                f"OpenRouterDriver instantiated on protocol={self.card.protocol!r} "
                f"cannot serve the embedding slot."
            )
        return EmbeddingConfig(
            api_key=self.card.api_key,
            base_url=self.card.base_url,
            model=model,
        )
