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
- plugin_paths.py  WHERE optional framework plugins install + are they present
- api_config.py    Cross-cutting per-adapter config dataclasses (root)

This __init__ is also the driver REGISTRATION point (claude_code / codex_cli /
nexus_power) and the stable public symbol surface.

Lightweight-plugin build
------------------------
On the local build ``claude-agent-sdk`` (Claude) and ``openai-codex`` (Codex)
are OPTIONAL plugins the user installs on demand — importing this package must
NOT require either. So all three frameworks register LAZY factories: the SDK
import happens inside the factory (and inside ``__getattr__`` for the public
class names), never at package import. Each factory first calls
``plugin_paths.activate_pyenv()`` so a plugin installed while the app runs
resolves without a restart. ``framework_installed`` (in ``loop.driver``) is
the separate "is it actually present" gate; registration only means "knows how
to build it once installed". The public names ``ClaudeAgentSDK`` / ``CodexSDK``
/ ``CodexSDKv2`` stay importable via module ``__getattr__`` (PEP 562), still
lazily.
"""

from . import plugin_paths
from .api_config import CodexConfig, codex_config

from .loop.driver import (
    AgentLoopDriver,
    DEFAULT_AGENT_LOOP_FRAMEWORK,
    framework_registry,
    FrameworkNotInstalledError,
    available_agent_loop_frameworks,
    get_agent_loop_driver,
    register_agent_loop_driver,
    resolve_framework_name,
)


# The three builtin frameworks are plugins under plugins/ (batch 6b):
# builtin.frameworks.{nexus_power,claude_code,codex_cli}. Their contributions
# (driver factory + install spec) live in each package's ``contribution.py``;
# ``loop.driver`` registers them through the kernel on first lookup. The
# plugin pyenv is put on sys.path here so an already-installed SDK resolves.
plugin_paths.activate_pyenv()

__all__ = [
    "CodexConfig",
    "codex_config",
    "AgentLoopDriver",
    "DEFAULT_AGENT_LOOP_FRAMEWORK",
    "FrameworkNotInstalledError",
    "available_agent_loop_frameworks",
    "get_agent_loop_driver",
    "register_agent_loop_driver",
    "resolve_framework_name",
    "plugin_paths",
]
