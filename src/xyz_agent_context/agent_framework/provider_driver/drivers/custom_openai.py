"""
@file_name: custom_openai.py
@author: Bin Liang
@date: 2026-05-13
@description: Driver for user-configured OpenAI-protocol providers
              (custom_openai) — handles the helper_llm slot
              slots.

Anything the user adds via the "Add Custom OpenAI Provider" UI flow —
official OpenAI, Azure-on-OpenAI, self-hosted vLLM, etc. — lands here.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.api_config import (
    OpenAIConfig,
)
from xyz_agent_context.agent_framework.provider_driver.base import _DriverBase
from xyz_agent_context.agent_framework.provider_driver.registry import register


@register
class CustomOpenAIDriver(_DriverBase):
    """User-configured openai-protocol provider."""

    @classmethod
    def driver_type(cls) -> str:
        return "custom_openai"

    def build_openai_config(self, model: str) -> OpenAIConfig:
        return OpenAIConfig(
            api_key=self.card.api_key,
            base_url=self.card.base_url,
            model=model,
        )