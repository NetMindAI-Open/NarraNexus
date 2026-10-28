"""
@file_name: contributions.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.llm_clients — the helper-LLM protocol clients as the contributions the manifest names (model.clients).
"""
from __future__ import annotations

from typing import Any

from narranexus.kernel.plugins.registry import Contribution


def _load_anthropic_helper() -> Any:
    # Lazy: the SDK modules import api_config, so importing at module load would create a circular import.
    from narranexus_plugins.llm_clients.anthropic_helper import AnthropicHelperSDK

    return AnthropicHelperSDK()


def _load_openai_helper() -> Any:
    from narranexus.platform.agent_framework.adapters.openai_agents import OpenAIAgentsSDK

    return OpenAIAgentsSDK()


def _load_cli_helper() -> Any:
    from narranexus_plugins.llm_clients.cli_helper import CliHelperSDK

    return CliHelperSDK()


ANTHROPIC = Contribution("anthropic", _load_anthropic_helper, meta={"display_name": "Anthropic Messages"})
OPENAI = Contribution("openai", _load_openai_helper, meta={"display_name": "OpenAI protocol"})
CLI = Contribution("cli", _load_cli_helper, meta={"display_name": "Subscription CLI"})
CONTRIBUTIONS = (ANTHROPIC, OPENAI, CLI)

__all__ = ["ANTHROPIC", "CLI", "CONTRIBUTIONS", "OPENAI"]
