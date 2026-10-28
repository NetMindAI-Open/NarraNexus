"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-04
@description: The platform layer (``narranexus.platform``) — the former ``xyz_agent_context`` domains (agent runtime, context runtime, narrative, memory, message bus, module system, channels, …) plus the turn pipeline (``platform.turn``).

Depends on the kernel and the contracts; never imports a plugin (import-linter
enforces it). ``xyz_agent_context`` remains importable for one release as an
alias of this package (plugin platform batch 6a, D8).
"""


# Single source of truth for the app version: read it from the installed
# package metadata (driven by pyproject [project].version, one of the 5 release
# anchors) instead of hand-maintaining a literal that silently goes stale.
try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("narranexus")
except Exception:  # noqa: BLE001 — source tree with no install metadata
    __version__ = "0.0.0+unknown"

# Export core components - organized by dependency order
# 1. Schema (data structures, no dependencies)
from .schema import (
    ProgressMessage,
    ProgressStatus,
    AgentTextDelta,
    ModuleConfig,
    MCPServerConfig,
    ContextData,
)

# 2. Utils (utilities, low dependencies)
from .utils import DatabaseClient

# 3. Narrative (narrative and event management)
from .narrative import (
    Narrative,
    Event,
    EventService,
    NarrativeService,
)

# 4. Module (module system)
from .module_system import (
    XYZBaseModule,
    ModuleService,
    HookManager,
)

# 5. Agent Framework (Agent SDK integration).
# ``ClaudeAgentSDK`` is intentionally NOT eagerly imported here: on the
# lightweight local build ``claude-agent-sdk`` is an optional plugin, so
# importing this top-level package must not require it. It stays importable
# (`from narranexus.platform import ClaudeAgentSDK`) via the lazy __getattr__
# below, which only pulls the SDK on actual attribute access.

# 6. Context Runtime (context building)
from .context_runtime import ContextRuntime

# 7. Agent Runtime (runtime coordination)
from .agent_runtime import AgentRuntime

__all__ = [
    "__version__",
    "ProgressMessage",
    "ProgressStatus",
    "AgentTextDelta",
    "ModuleConfig",
    "MCPServerConfig",
    "ContextData",
    "DatabaseClient",
    "Narrative",
    "Event",
    "EventService",
    "NarrativeService",
    "XYZBaseModule",
    "ModuleService",
    "HookManager",
    "ContextRuntime",
    "AgentRuntime",
]

