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

from narranexus.platform.agent_framework.api_config import (
    OpenAIConfig,
)
from narranexus.platform.agent_framework.providers.driver.base import _DriverBase
from narranexus.platform.agent_framework.providers.driver.registry import register


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


# Plugin-platform contributions named by ``builtin.providers``'s manifest; the
# loader registers these same objects. ``model.providers`` is a MANY-arity
# slot, so the symbol is the plural ``CONTRIBUTIONS`` (``docs/API_POLICY.md``
# §8) — the nine provider modules used to be split between two spellings of
# the same thing on the same slot.
CONTRIBUTIONS = (CustomOpenAIDriver.contribution,)
