"""
@file_name: helper_sdk.py
@author: NarraNexus
@date: 2026-06-10
@description: Protocol-keyed helper_llm SDK factory

Single entry point for every helper_llm call site. The helper SDK is
chosen by ONE thing — the protocol of the helper config the resolver
installed for the current asyncio task — looked up in a registry:

  protocol "anthropic" -> AnthropicHelperSDK (Messages API)
  protocol "openai"    -> OpenAIAgentsSDK    (Chat Completions)

Why a registry keyed on protocol (not a scattered if/elif): the SAME
protocol that BUILT the config (in the single-point Provider Driver
resolver) is the one that PICKS the SDK here, so an anthropic provider
can never end up on the OpenAI SDK — that mismatch is unrepresentable.
Mirrors the in-repo DRIVER_REGISTRY / agent_loop_driver registries.

Call sites never import a concrete SDK class; this keeps the helper
swappable per iron rule #9 (no hard binding to one LLM/protocol) and
lets a single Claude key serve both the agent and helper slots. Both
SDKs expose the same interface (llm_function / llm_stream) and return
the same result-wrapper shapes, so callers are dispatch-blind.
"""

from __future__ import annotations

from typing import Any, Callable, Dict


def _load_anthropic_helper() -> Any:
    # Lazy: both SDK modules import api_config, so importing at module load
    # would create a circular import.
    from xyz_agent_context.agent_framework.anthropic_helper_sdk import (
        AnthropicHelperSDK,
    )
    return AnthropicHelperSDK()


def _load_openai_helper() -> Any:
    from xyz_agent_context.agent_framework.openai_agents_sdk import OpenAIAgentsSDK
    return OpenAIAgentsSDK()


def _load_cli_helper() -> Any:
    from xyz_agent_context.agent_framework.cli_helper_sdk import CliHelperSDK
    return CliHelperSDK()


# protocol -> zero-arg loader. Adding a helper protocol = register a loader
# here and have the resolver mark that protocol on the helper config.
_HELPER_SDK_BY_PROTOCOL: Dict[str, Callable[[], Any]] = {
    "anthropic": _load_anthropic_helper,
    "openai": _load_openai_helper,
    "cli": _load_cli_helper,
}

_DEFAULT_HELPER_PROTOCOL = "openai"


def _resolved_helper_protocol() -> str:
    """The single point that decides the current task's helper protocol.

    Precedence: ``cli`` (a subscription/OAuth helper installed via
    ``_cli_helper_ctx``) wins, then ``anthropic`` (an anthropic-protocol
    provider via ``_anthropic_helper_ctx``), else the openai-protocol
    ``OpenAIConfig`` default. The resolver installs exactly one of these per
    task; reading these ContextVars is the sole protocol signal.
    """
    from xyz_agent_context.agent_framework.api_config import (
        _anthropic_helper_ctx,
        _cli_helper_ctx,
    )

    if _cli_helper_ctx.get() is not None:
        return "cli"
    if _anthropic_helper_ctx.get() is not None:
        return "anthropic"
    return _DEFAULT_HELPER_PROTOCOL


def get_helper_sdk():
    """Return the helper-LLM SDK instance for the current asyncio task."""
    protocol = _resolved_helper_protocol()
    loader = _HELPER_SDK_BY_PROTOCOL.get(protocol)
    if loader is None:  # defensive: an unregistered protocol is a wiring bug
        raise ValueError(
            f"No helper SDK registered for protocol {protocol!r}. "
            f"Known: {sorted(_HELPER_SDK_BY_PROTOCOL)}."
        )
    return loader()


__all__ = ["get_helper_sdk"]
