"""
Agent Framework Package

Provides integration interfaces with different AI Agent SDKs.

Two abstraction axes live here:
  - ``provider_driver/``    — provider axis (which endpoint / key)
  - ``agent_loop_driver``   — framework axis (which agent-loop protocol)

Drivers register themselves under a name; ``step_3_agent_loop`` reads
``user_slots.agent_framework`` and looks up the matching driver via
``get_agent_loop_driver``. Nothing downstream hard-codes a class.

Currently registered drivers — ONE canonical name per framework:
  - ``"claude_code"`` → ``ClaudeAgentSDK``
  - ``"codex_cli"``   → ``CodexSDKv2`` (official ``openai-codex`` SDK)

**A/B aliases removed (2026-06-08 cleanup)**: the A/B period
registered ``codex_cli_v2`` / ``codex_official`` to ease the cutover.
Now that v2 is the only registered codex driver, those aliases are
gone. Any DB row still carrying them (legacy from the A/B window)
fails resolution with ``ValueError`` — the user re-picks "Codex CLI"
from Settings to fix.

**Legacy shorthand removed**: ``"claude"`` and ``"codex"`` shorthand
aliases (intended for env / CLI overrides) had zero in-tree callers
and were just clutter. Use the canonical names.

**Revival fallback**: the hand-rolled v1 ``CodexSDK`` lives in
``xyz_codex_cli_sdk.py`` and is still importable (the file is kept
intentionally), but is NOT registered. To bring it back online (e.g.
if the official SDK ships a critical regression), add
``register_agent_loop_driver("codex_cli", CodexSDK)`` below.

The ``codex_cli`` framework also carries a per-call ``codex_config``
ContextVar — see ``api_config.CodexConfig`` for the auth + model
shape the resolver fills before each turn.
"""

from loguru import logger

from .xyz_claude_agent_sdk import ClaudeAgentSDK
from .xyz_codex_cli_sdk import CodexSDK  # noqa: F401 — kept importable; not registered
from .api_config import CodexConfig, codex_config
from .agent_loop_driver import (
    AgentLoopDriver,
    DEFAULT_AGENT_LOOP_FRAMEWORK,
    available_agent_loop_frameworks,
    get_agent_loop_driver,
    register_agent_loop_driver,
    resolve_framework_name,
)

# Single canonical Claude name.
register_agent_loop_driver("claude_code", ClaudeAgentSDK)

# Single canonical Codex name → official ``openai-codex`` SDK driver.
# The import is guarded so the package still loads on slim deploys
# that exclude ``openai-codex``; callers asking for ``codex_cli``
# on such a deploy get a clean ``ValueError`` from
# ``get_agent_loop_driver``.
try:
    from .xyz_codex_official_sdk import CodexSDKv2
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
