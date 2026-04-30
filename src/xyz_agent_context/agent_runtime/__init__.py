"""
Agent Runtime Package

Core implementation of the Agent runtime, responsible for coordinating the entire execution flow.

Architecture:
- AgentRuntime: Core orchestrator, coordinates the Agent execution flow
- ExecutionState: Execution state management (immutable design)
- ResponseProcessor: Response processor (pure functions, no side effects)

Logging is owned by xyz_agent_context.utils.logging.setup_logging() which
each process calls once at startup. AgentRuntime no longer manages a
per-run file sink — the previous LoggingService design leaked file
descriptors on EC2 and was removed in M4 / T15.
"""

from .agent_runtime import AgentRuntime
from .execution_state import ExecutionState
from .response_processor import ResponseProcessor, ResponseType, ProcessedResponse

__all__ = [
    # Core orchestrator
    "AgentRuntime",
    # Execution state
    "ExecutionState",
    # Response processing
    "ResponseProcessor",
    "ResponseType",
    "ProcessedResponse",
]
