"""
Agent Framework Package

Provides integration interfaces with different AI Agent SDKs.

Two abstraction axes live here:
  - ``provider_driver/``    — provider axis (which endpoint / key)
  - ``agent_loop_driver``   — framework axis (which agent-loop protocol)

``ClaudeAgentSDK`` is the reference framework driver and is registered
under the name "claude" at import time. New frameworks register their
own driver the same way; nothing downstream (step_3) hard-codes a class.
"""

from .xyz_claude_agent_sdk import ClaudeAgentSDK
from .agent_loop_driver import (
    AgentLoopDriver,
    DEFAULT_AGENT_LOOP_FRAMEWORK,
    available_agent_loop_frameworks,
    get_agent_loop_driver,
    register_agent_loop_driver,
    resolve_framework_name,
)

# Register the built-in Claude driver. ``ClaudeAgentSDK(working_path=...)``
# already matches the factory contract, so the class itself is the factory.
register_agent_loop_driver("claude", ClaudeAgentSDK)

__all__ = [
    "ClaudeAgentSDK",
    "AgentLoopDriver",
    "DEFAULT_AGENT_LOOP_FRAMEWORK",
    "available_agent_loop_frameworks",
    "get_agent_loop_driver",
    "register_agent_loop_driver",
    "resolve_framework_name",
]
