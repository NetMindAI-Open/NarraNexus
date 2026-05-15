"""
@file_name: netmind.py
@author: Bin Liang
@date: 2026-05-13
@description: Driver for NetMind aggregator one-key cards.

NetMind quick-add writes TWO ``user_providers`` rows under a shared
``linked_group``: one for the anthropic protocol endpoint (used by the
agent slot) and one for the openai protocol endpoint (helper_llm +
embedding). Each row carries the protocol-specific ``base_url`` and
``auth_type``. The Driver therefore doesn't need to look up the
sibling row — it just builds the right config for whichever protocol
the card represents.

NetMind is an aggregator: it doesn't forward Anthropic's server-side
tools (WebSearch, text_editor, computer_use), so
``supports_anthropic_server_tools`` stays False.
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
class NetMindDriver(_DriverBase):
    """NetMind one-key card."""

    @classmethod
    def driver_type(cls) -> str:
        return "netmind"

    def _is_anthropic_row(self) -> bool:
        return (self.card.protocol or "").lower() == "anthropic"

    def _is_openai_row(self) -> bool:
        return (self.card.protocol or "").lower() == "openai"

    def build_claude_config(self, model: str) -> ClaudeConfig:
        if not self._is_anthropic_row():
            raise NotImplementedError(
                f"NetMindDriver instantiated on protocol={self.card.protocol!r} "
                f"cannot serve the agent slot. The agent slot must point at the "
                f"NetMind anthropic row."
            )
        return ClaudeConfig(
            api_key=self.card.api_key,
            base_url=self.card.base_url,
            model=model,
            auth_type=self.card.auth_type or "bearer_token",
            supports_anthropic_server_tools=False,
        )

    def build_openai_config(self, model: str) -> OpenAIConfig:
        if not self._is_openai_row():
            raise NotImplementedError(
                f"NetMindDriver instantiated on protocol={self.card.protocol!r} "
                f"cannot serve the helper_llm slot. helper_llm must point at the "
                f"NetMind openai row."
            )
        return OpenAIConfig(
            api_key=self.card.api_key,
            base_url=self.card.base_url,
            model=model,
        )

    def build_embedding_config(self, model: str) -> EmbeddingConfig:
        if not self._is_openai_row():
            raise NotImplementedError(
                f"NetMindDriver instantiated on protocol={self.card.protocol!r} "
                f"cannot serve the embedding slot. embedding must point at the "
                f"NetMind openai row."
            )
        return EmbeddingConfig(
            api_key=self.card.api_key,
            base_url=self.card.base_url,
            model=model,
        )
