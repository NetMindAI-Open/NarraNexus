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
Mirrors the in-repo driver_registry() / loop.driver registries.

Call sites never import a concrete SDK class; this keeps the helper
swappable per iron rule #9 (no hard binding to one LLM/protocol) and
lets a single Claude key serve both the agent and helper slots. Both
SDKs expose the same interface (llm_function / llm_stream) and return
the same result-wrapper shapes, so callers are dispatch-blind.
"""

from __future__ import annotations

from typing import Any

from narranexus.contracts import UnknownEntry
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.kernel.plugins.registry import Registry


# The kernel registry for slot ``model.clients`` (plugin platform, batch 1):
# protocol key -> zero-arg loader. Adding a helper protocol = a plugin
# contributing a ``Contribution`` here and the resolver marking that protocol
# on the helper config. The three builtin clients are named by the
# ``builtin.llm_clients`` manifest; import-time and manifest-driven
# registration register the same objects.
CLIENTS_SLOT = "model.clients"


def llm_client_registry(registries: Any = None) -> Registry[Any]:
    """The registry for slot ``model.clients``, resolved at call time; populated by the host boot from the builtin.llm_clients manifest."""
    return (registries or KERNEL_REGISTRIES).registry_for(CLIENTS_SLOT)

_DEFAULT_HELPER_PROTOCOL = "openai"


def _resolved_helper_protocol() -> str:
    """The single point that decides the current task's helper protocol.

    Precedence: ``cli`` (a subscription/OAuth helper installed via
    ``_cli_helper_ctx``) wins, then ``anthropic`` (an anthropic-protocol
    provider via ``_anthropic_helper_ctx``), else the openai-protocol
    ``OpenAIConfig`` default. The resolver installs exactly one of these per
    task; reading these ContextVars is the sole protocol signal.
    """
    from narranexus.platform.agent_framework.api_config import (
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
    registry = llm_client_registry()
    try:
        return registry.get(protocol)
    except UnknownEntry:  # defensive: an unregistered protocol is a wiring bug
        raise ValueError(
            f"No helper SDK registered for protocol {protocol!r}. "
            f"Known: {sorted(registry.names())}."
        ) from None


__all__ = ["CLIENTS_SLOT", "get_helper_sdk", "llm_client_registry"]
