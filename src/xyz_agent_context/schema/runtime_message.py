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
    """
    message_type: Literal[MessageType.ERROR] = MessageType.ERROR
    error_message: str
    error_type: str = "api_error"
