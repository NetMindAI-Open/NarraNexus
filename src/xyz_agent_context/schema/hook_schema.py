"""
Hook Schema - Hook system data models

@file_name: hook_schema.py
@author: NetMind.AI
@date: 2025-11-27
@description: Defines data models used by the Hook system for more structured parameter passing

=============================================================================
Design Goals
=============================================================================

Structuring the **kwargs parameters of hook_after_event_execution into several data models:

1. HookExecutionContext - Execution context (required)
   - event_id, agent_id, user_id, working_source

2. HookIOData - Input/output data (required)
   - input_content, final_output

3. HookExecutionTrace - Execution trace (optional)
   - event_log, agent_loop_response

4. ctx_data: ContextData - Complete context (existing, optional)

Usage example:
    await hook_manager.hook_after_event_execution(
        execution_ctx=HookExecutionContext(...),
        io_data=HookIOData(...),
        trace=HookExecutionTrace(...),  # optional
        ctx_data=ctx_data,              # optional
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from xyz_agent_context.schema.module_schema import ModuleInstance
    from xyz_agent_context.narrative.models import Event, Narrative


class WorkingSource(str, Enum):
    """
    Agent execution source - Identifies the origin that triggered Agent execution

    Uses enum instead of magic strings, providing:
    - Type safety: Typos are caught at compile/import time
    - IDE support: Auto-completion and refactoring support
    - Self-documenting: All valid values are clearly visible

    Inherits from str so it can:
    - Be compared directly with strings
    - Be automatically converted to string values during JSON serialization

    Values:
        CHAT: Triggered by user conversation (default)
        JOB: Triggered by JobTrigger task
        A2A: Triggered by Agent-to-Agent call
        CALLBACK: Triggered by callback after Job completion (dependency chain activation)

    Usage:
        # Type-safe comparison
        if source == WorkingSource.JOB:
            handle_job()

        # Also supports string comparison
        if source == "job":
            handle_job()

        # JSON serialization
        json.dumps({"source": WorkingSource.JOB})  # {"source": "job"}
    """
    CHAT = "chat"
    JOB = "job"
    A2A = "a2a"
    CALLBACK = "callback"  # Callback triggered after Job completion
    SKILL_STUDY = "skill_study"  # Skill study trigger
    MATRIX = "matrix"  # Triggered by Matrix message (MatrixTrigger)
    IM = "im"  # IM 渠道触发（Telegram / WhatsApp / Discord 等）
    @classmethod
    def from_string(cls, value: str) -> "WorkingSource":
        """
        Safely convert from string to enum

        Args:
            value: String value (e.g., "job", "chat")

        Returns:
            Corresponding WorkingSource enum

        Raises:
            ValueError: Invalid value

        Example:
            source = WorkingSource.from_string("job")  # WorkingSource.JOB
        """
        try:
            return cls(value.lower())
        except ValueError:
            valid = [e.value for e in cls]
            raise ValueError(f"Invalid working_source '{value}'. Must be one of: {valid}")

    def is_automated(self) -> bool:
        """
        Check if this is an automated (not directly user-triggered) execution

        Returns:
            True if triggered by JOB, A2A, or CALLBACK
        """
        return self in (WorkingSource.JOB, WorkingSource.A2A, WorkingSource.CALLBACK, WorkingSource.MATRIX)

    def is_user_initiated(self) -> bool:
        """
        Check if this is a user-initiated execution

        Returns:
            True if triggered by CHAT or IM
        """
        return self in (WorkingSource.CHAT, WorkingSource.IM)


@dataclass
class HookExecutionContext:
    """
    Execution context - Basic identification information for this execution

    This is the most fundamental hook information, identifying who, where, and what.

    Attributes:
        event_id: Event ID of this execution
        agent_id: Agent ID performing the execution
        user_id: User ID
        working_source: Execution source
            - "chat": Triggered by user conversation
            - "job": Triggered by JobTrigger
            - "a2a": Agent-to-Agent call
    """
    event_id: str
    agent_id: str
    user_id: str
    working_source: WorkingSource = WorkingSource.CHAT  # Uses enum type


@dataclass
class HookIOData:
    """
    Input/output data - Agent's input and final output

    Attributes:
        input_content: User/system input content
        final_output: Agent's final text output
    """
    input_content: str
    final_output: str


@dataclass
class HookExecutionTrace:
    """
    Execution trace - Detailed record of the Agent's execution process

    Used for scenarios requiring deep analysis of the execution process, such as:
    - JobModule analyzing which tools were executed
    - Debugging and logging
    - Execution auditing

    Attributes:
        event_log: Event log list, recording key steps during execution
        agent_loop_response: Agent Loop's raw response list
            - Contains AgentTextDelta, ProgressMessage, etc.
            - Can be used to extract tool calls, thinking process, etc.
    """
    event_log: List[Any] = field(default_factory=list)
    agent_loop_response: List[Any] = field(default_factory=list)


@dataclass
class HookAfterExecutionParams:
    """
    Complete parameter package for hook_after_event_execution

    Packages all parameters into a single object for convenient passing and usage.
    Modules can access individual parts as needed.

    Attributes:
        execution_ctx: Execution context (required)
        io_data: Input/output data (required)
        trace: Execution trace (optional, for deep analysis)
        ctx_data: Complete context data (optional, ContextData instance)
        instance: Currently executing ModuleInstance (optional, for state checking)

    Usage:
        # In a Module's hook
        async def hook_after_event_execution(self, params: HookAfterExecutionParams):
            if params.execution_ctx.working_source == "job":
                # Handle post-Job execution logic
                job_id = params.ctx_data.extra_data.get("job_id")
                ...
    """
    execution_ctx: HookExecutionContext
    io_data: HookIOData
    trace: Optional[HookExecutionTrace] = None
    ctx_data: Optional[Any] = None  # ContextData, using Any to avoid circular imports
    instance: Optional["ModuleInstance"] = None  # Currently executing instance

    # === Narrative related (for EverMemOS writing, etc.) ===
    event: Optional["Event"] = None  # Current Event object
    narrative: Optional["Narrative"] = None  # Main Narrative object

    # === Convenience access properties ===

    @property
    def event_id(self) -> str:
        return self.execution_ctx.event_id

    @property
    def agent_id(self) -> str:
        return self.execution_ctx.agent_id

    @property
    def user_id(self) -> str:
        return self.execution_ctx.user_id

    @property
    def working_source(self) -> str:
        return self.execution_ctx.working_source

    @property
    def input_content(self) -> str:
        return self.io_data.input_content

    @property
    def final_output(self) -> str:
        return self.io_data.final_output

    @property
    def event_log(self) -> List[Any]:
        return self.trace.event_log if self.trace else []

    @property
    def agent_loop_response(self) -> List[Any]:
        return self.trace.agent_loop_response if self.trace else []
