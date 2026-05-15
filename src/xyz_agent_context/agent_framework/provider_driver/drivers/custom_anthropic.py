"""
@file_name: custom_anthropic.py
@author: Bin Liang
@date: 2026-05-13
@description: Driver for user-configured Anthropic-protocol providers
              (custom_anthropic).

Anything the user adds via the "Add Custom Anthropic Provider" UI
flow — official Anthropic API, transparent forward proxy, self-hosted
gateway — lands here. The agent slot only.

The ``supports_anthropic_server_tools`` flag is read off the card and
propagated to the ClaudeConfig so the tool-policy hook can decide
whether to allow WebSearch / text_editor / etc.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.api_config import ClaudeConfig
from xyz_agent_context.agent_framework.provider_driver.base import (
    ProviderCard,
    _DriverBase,
)
from xyz_agent_context.agent_framework.provider_driver.registry import register


@register
class CustomAnthropicDriver(_DriverBase):
    """User-configured anthropic-protocol provider."""

    @classmethod
    def driver_type(cls) -> str:
        return "custom_anthropic"

    def build_claude_config(self, model: str) -> ClaudeConfig:
        return ClaudeConfig(
            api_key=self.card.api_key,
            base_url=self.card.base_url,
            model=model,
            auth_type=self.card.auth_type or "api_key",
            supports_anthropic_server_tools=bool(
                self.card.supports_anthropic_server_tools
            ),
        )
