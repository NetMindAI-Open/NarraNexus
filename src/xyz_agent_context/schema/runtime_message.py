"""
@file_name: runtime_message.py
@author: NetMind.AI
@date: 2025-11-21
@description: Runtime message type definitions for agent runtime streaming output

This module defines typed messages that are yielded by the agent runtime
and consumed by the frontend (e.g., Streamlit app) for display.

Message Architecture:
- BaseRuntimeMessage: Abstract base class for all runtime messages
- ProgressMessage: Progress tracking messages (step-by-step execution)
- AgentTextDelta: Streaming text output from the agent
- AgentThinking: Agent's thinking process (for transparency)
- AgentToolCall: Tool/function calls made by the agent

Usage:
    # In agent_runtime.py
    yield ProgressMessage(
        step="1.0",
        title="Loading data",
        description="Reading from database",
        status=ProgressStatus.RUNNING
    )

    # In streamlit app
    async for message in runtime.run(...):
        if isinstance(message, ProgressMessage):
            display_progress(message)
        elif isinstance(message, AgentTextDelta):
            display_text(message)
"""

import time
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from enum import Enum
from abc import ABC


# ============================================================================
# Message Type Enums
# ============================================================================

class MessageType(str, Enum):
    """
    Runtime message type enumeration

    Defines all possible message types that can be yielded by the agent runtime.
    """
    PROGRESS = "progress"
    AGENT_RESPONSE = "agent_response"
    AGENT_THINKING = "agent_thinking"
    TOOL_CALL = "tool_call"
    ERROR = "error"


class ProgressStatus(str, Enum):
    """
    Progress message status

    Indicates the current state of a progress step.
    """
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ============================================================================
# Base Runtime Message
# ============================================================================

class BaseRuntimeMessage(BaseModel, ABC):
    """
    Base class for all runtime messages

    All messages inherit from this class and include:
    - message_type: The type of message (from MessageType enum)
                    Serialized as "type" field name (frontend API convention)
    - timestamp: Unix timestamp when the message was created

    This base class provides:
    - Pydantic validation
    - Automatic timestamp generation
    - Common serialization methods
    """
    message_type: MessageType = Field(serialization_alias="type")
    timestamp: float = Field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert message to dictionary

        Uses mode='json' to ensure enums are serialized as their string values.
        Serializes 'message_type' as 'type' (frontend API convention)

        Returns:
            Dict[str, Any]: Dictionary representation of the message
        """
        data = self.model_dump(mode='json')
        # Serialize message_type as type (frontend API convention)
        if 'message_type' in data:
            data['type'] = data.pop('message_type')
        return data

    class Config:
        """Pydantic configuration"""
        use_enum_values = True  # Automatically convert enums to their values


# ============================================================================
# Progress Messages
# ============================================================================

class ProgressMessage(BaseRuntimeMessage):
    """
    Progress tracking message

    Used to report progress through multi-step processes.
    Each step has an ID, title, description, status, and optional substeps.

    Example:
        >>> msg = ProgressMessage(
        ...     step="1.0",
        ...     title="Initialize Database",
        ...     description="Connecting to PostgreSQL",
        ...     status=ProgressStatus.RUNNING,
        ...     substeps=["Create connection pool", "Run migrations"]
        ... )

    Attributes:
        step: Step identifier (e.g., "1.0", "2.1", "3")
        title: Human-readable step title
        description: Detailed description of what's happening
        status: Current status (running/completed/failed)
        substeps: List of substep descriptions (optional)
        details: Additional structured data (optional)
    """
    message_type: Literal[MessageType.PROGRESS] = MessageType.PROGRESS
    step: str
    title: str
    description: str
    status: ProgressStatus
    substeps: List[str] = Field(default_factory=list)
    details: Optional[Dict[str, Any]] = None


# ============================================================================
# Agent Response Messages
# ============================================================================

class AgentTextDelta(BaseRuntimeMessage):
    """
    Agent text output delta

    Represents a chunk of streaming text output from the agent.
    Multiple deltas are concatenated to form the complete response.

    Example:
        >>> msg = AgentTextDelta(delta="Hello ")
        >>> msg2 = AgentTextDelta(delta="world!")
        >>> # Frontend concatenates: "Hello " + "world!" = "Hello world!"

    Attributes:
        delta: The text chunk to append
        response_type: Type of response (always "text" for now)
    """
    message_type: Literal[MessageType.AGENT_RESPONSE] = MessageType.AGENT_RESPONSE
    delta: str
    response_type: Literal["text"] = "text"


class AgentThinking(BaseRuntimeMessage):
    """
    Agent thinking process message

    Contains the agent's internal reasoning/thinking process.
    Can be displayed in an expandable section for transparency.

    Example:
        >>> msg = AgentThinking(
        ...     thinking_content="I need to query the database first..."
        ... )

    Attributes:
        thinking_content: The thinking/reasoning text
    """
    message_type: Literal[MessageType.AGENT_THINKING] = MessageType.AGENT_THINKING
    thinking_content: str


class AgentToolCall(BaseRuntimeMessage):
    """
    Agent tool/function call message

    Represents a tool or function call made by the agent.
    Includes the tool name, input parameters, and optional output.

    Example:
        >>> msg = AgentToolCall(
        ...     tool_name="search_database",
        ...     tool_input={"query": "SELECT * FROM users"},
        ...     tool_output="[{'id': 1, 'name': 'Alice'}]"
        ... )

    Attributes:
        tool_name: Name of the tool being called
        tool_input: Input parameters (as dict)
        tool_output: Output result (optional, may be set after call completes)
    """
    message_type: Literal[MessageType.TOOL_CALL] = MessageType.TOOL_CALL
    tool_name: str
    tool_input: Dict[str, Any]
    tool_output: Optional[str] = None


# ============================================================================
# Error Messages
# ============================================================================

class ErrorMessage(BaseRuntimeMessage):
    """
    Runtime error message

    Sent to the frontend when the agent encounters an error during execution
    (e.g., rate limit, API authentication failure, quota exhaustion).
    Frontend should display these prominently so the user understands what happened.

    Attributes:
        error_message: Human-readable error description
        error_type: Error category (e.g., "rate_limit", "api_error", "execution_error")
        severity:
          - "fatal": framework-level (SDK crash, CLI timeout, auth failure) —
            the turn cannot recover. ChatModule writes this as a failed
            user-only row and skips the assistant side.
          - "recoverable": surfaced as information for the agent to react to
            (transient rate-limit blip, single 5xx, etc.) — the agent loop
            keeps yielding and we DON'T tear the whole turn down.
          - "recovered": a fatal-class error happened, but the helper_llm
            after-error fallback produced a user-facing reply that masks the
            failure operationally. Frontend renders the reply as normal and
            surfaces this error as a warning badge.
          - "recovered_after_reply": the agent already called
            send_message_to_user_directly before a fatal hit. No fallback
            runs (we already spoke), but the badge tells the user the turn
            didn't finish all planned work.
          Default is "fatal" to preserve historical behaviour; new error
          sites should mark themselves explicitly.
    """
    message_type: Literal[MessageType.ERROR] = MessageType.ERROR
    error_message: str
    error_type: str = "api_error"
    severity: Literal[
        "fatal", "recoverable", "recovered", "recovered_after_reply"
    ] = "fatal"
    # Only set when error_type == SELF_SERVICEABLE_ERROR_TYPE: the concrete
    # self-serviceable reason ("context_window" / "insufficient_balance" /
    # "model_not_found") so the frontend can pick actionable "what you can do"
    # copy instead of a generic "turn failed". None for every other error.
    action_reason: Optional[str] = None


# error_type marker for credential/auth failures (codex OAuth token
# expired / "refresh token already used", 401, etc.). Defined in this leaf
# schema module — NOT in response_processor — so both response_processor
# and step_3_agent_loop can import it without a circular import. (Putting
# it in response_processor closed a cycle: response_processor →
# step_display → _agent_runtime_steps → step_3_agent_loop →
# response_processor, where the constant wasn't bound yet — incident
# 2026-06-11.)
AUTH_EXPIRED_ERROR_TYPE = "auth_expired"

# error_type marker for DETERMINISTIC, user-self-serviceable failures — the
# turn cannot run until the user changes their OWN config (model with a
# bigger context window / add credits / fix the model id). Unlike a transient
# blip (retry fixes it) or our-own bug, these WILL recur every turn with the
# same config, so a helper-LLM fallback reply that papers over them is a lie
# about what happened (the "black box" incident: a 32k model that can't hold
# the ~75k platform context failed every turn, and DeepSeek fabricated a
# normal-looking reply — the user never knew the agent didn't run). Keyed on
# here (leaf schema module, same reason as AUTH_EXPIRED_ERROR_TYPE) so both
# response_processor and step_3_agent_loop import it without a circular
# import. The concrete reason (context_window / insufficient_balance /
# model_not_found) rides in ErrorMessage.action_reason so the frontend can
# pick actionable copy. NOTE (binding rule #15): this only surfaces the truth
# + an actionable hint — it never force-stops a run, injects a prompt, judges
# the model, or switches the user's model. Whether to act is the user's call.
SELF_SERVICEABLE_ERROR_TYPE = "config_actionable"

# error_type marker for PLATFORM-side executor infrastructure failures — the
# per-user execution container ran out of memory (subprocess SIGKILL/SIGABRT)
# or became unreachable (container not up / broker down / :8020 dropped). Unlike
# SELF_SERVICEABLE_ERROR_TYPE, the user CANNOT fix these by changing their config
# (there is no setting to change); the correct owner-facing guidance is "retry /
# split the task", so the frontend renders a distinct "execution environment"
# badge rather than the "Action needed → Settings" one. Like the two markers
# above it must skip the helper-LLM fallback: fabricating a reply over an OOM /
# dropped container hides the real infrastructure failure. The concrete reason
# (executor_oom / executor_unreachable) rides in ErrorMessage.action_reason so
# the frontend can pick the right copy. Kept here (leaf schema module) so both
# the classifier consumers import it without a circular import. NOTE (binding
# rules #14/#15): surfaces the truth + a retry hint only — never a force-stop.
EXECUTOR_INFRA_ERROR_TYPE = "infra_transient"
