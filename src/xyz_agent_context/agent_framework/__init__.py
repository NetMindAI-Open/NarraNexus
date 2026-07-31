"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-07-24
@description: Agent Framework — the standalone framework layer. Module map:

- loop/       Agent-loop execution (driver abstraction, remote executor
              delegation, broker client, output transfer, circuit breaker)
- adapters/   Agent-framework adapters (claude/, codex/, openai_agents;
              the swap seam demanded by binding rule #9)
- llm/        Atomic LLM operations — single calls, no loop (helper SDK
              family, failure classification, embeddings api, transcription/)
- providers/  Provider & model catalog system (registry/resolver/readiness,
              system/user/slot services, model catalog+sync+probe, driver/)
- api_config.py    Cross-cutting per-adapter config dataclasses (root)

This __init__ is also the driver REGISTRATION point (claude_code /
codex_cli) and the stable public symbol surface — package-level imports
(`from xyz_agent_context.agent_framework import X`) survive internal
regroupings.
"""

from loguru import logger

from .adapters.claude.sdk import ClaudeAgentSDK
from .adapters.codex.cli_sdk import CodexSDK  # noqa: F401 — kept importable; not registered
from .api_config import CodexConfig, codex_config
from .loop.driver import (
    AgentLoopDriver,
    DEFAULT_AGENT_LOOP_FRAMEWORK,
    available_agent_loop_frameworks,
    get_agent_loop_driver,
    register_agent_loop_driver,
    resolve_framework_name,
)

# Single canonical Claude name.
register_agent_loop_driver("claude_code", ClaudeAgentSDK)

# Home-grown nexus loop (the third framework): lazily imported by the
# factory so registering it costs nothing at package import time.
def _nexus_power_factory(**factory_kwargs):
    from .adapters.nexus.nexus_agent import NexusAgent

    return NexusAgent(**factory_kwargs)


register_agent_loop_driver("nexus_power", _nexus_power_factory)

# Single canonical Codex name → official ``openai-codex`` SDK driver.
# The import is guarded so the package still loads on slim deploys
# that exclude ``openai-codex``; callers asking for ``codex_cli``
# on such a deploy get a clean ``ValueError`` from
# ``get_agent_loop_driver``.
try:
    from .adapters.codex.official_sdk import CodexSDKv2
    register_agent_loop_driver("codex_cli", CodexSDKv2)
except ImportError as _e:  # noqa: BLE001 — guard against any SDK shape
    CodexSDKv2 = None  # type: ignore[assignment]
    logger.warning(
        f"CodexSDKv2 not available ({_e}); ``codex_cli`` is unregistered "
        f"until the official ``openai-codex`` SDK is installed. Re-install "
        f"dependencies or revive the v1 fallback by registering "
        f"``CodexSDK`` against ``codex_cli`` in this file."
    )

__all__ = [
    "ClaudeAgentSDK",
    "CodexSDK",
    "CodexSDKv2",
    "CodexConfig",
    "codex_config",
    "AgentLoopDriver",
    "DEFAULT_AGENT_LOOP_FRAMEWORK",
    "available_agent_loop_frameworks",
    "get_agent_loop_driver",
    "register_agent_loop_driver",
    "resolve_framework_name",
]
