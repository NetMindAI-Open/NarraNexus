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
    # NexusPower-only surfaces (other frameworks never emit these, so
    # existing consumers are untouched):
    AGENT_REPLY_DELTA = "agent_reply_delta"  # the reply itself, streaming
    AGENT_PLAN = "agent_plan"                # the agent's live plan


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
        monologue: The subset of ``thinking_content`` that is NexusPower
            monologue — the framework's assistant plain text, displayed
            as thinking under the monologue/expression contract. Empty
            for provider chain-of-thought and for every non-NexusPower
            driver. Consumers that relay "what the agent said" (e.g.
            ``collect_run.output_text``) read this field.

            The WS path reads it too, since 2026-08-30: the frontend
            renders a frame at the "progress" tier when this subset
            EQUALS ``thinking_content`` (see ``monologueTier.ts`` and
            ``chat_history_timeline.is_monologue_step`` — same rule, both sides).
            So this is NOT a redundant copy that can be dropped to slim
            the frame: removing it silently downgrades live narration
            back to chain-of-thought tone, and the frontend tier tests
            construct their own frames, so none of them would go red.
            It stays a SUBSET STRING, not a bool — ``collect_run`` relays
            this text as "what the agent said".
    """
    message_type: Literal[MessageType.AGENT_THINKING] = MessageType.AGENT_THINKING
    thinking_content: str
    monologue: str = ""


class AgentReplyDelta(BaseRuntimeMessage):
    """
    A chunk of the agent's user-facing reply, streamed as it is written.

    NexusPower-only. Under the monologue/expression contract the agent's
    plain text is working narration — visible to its owner, never delivered
    to anyone; the reply exists only as the argument
    of an expression tool. NexusPower streams that argument field as the
    model types it, so the user reads the answer live instead of waiting
    for the completed tool call. Frontends append deltas of the same
    ``call_id`` into one growing reply bubble; the eventual tool_call
    event carries the identical final text (idempotent by call_id).

    Attributes:
        delta: text chunk to append
        call_id: the expression tool call this reply belongs to
        tool_name: which expression tool is speaking
    """
    message_type: Literal[MessageType.AGENT_REPLY_DELTA] = MessageType.AGENT_REPLY_DELTA
    delta: str
    call_id: str
    tool_name: str


class AgentPlan(BaseRuntimeMessage):
    """
    The agent's current plan (full snapshot, replace-on-write).

    NexusPower-only. Steps carry ``step``/``status`` and the whole list
    is replaced on every update (no incremental patch protocol — the
    simplest semantics models handle reliably).

    Attributes:
        steps: ordered steps, each {"step": str, "status": str}
        note: optional one-line rationale for the update
    """
    message_type: Literal[MessageType.AGENT_PLAN] = MessageType.AGENT_PLAN
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    note: str = ""


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
          - "recovered_after_reply": the agent had already delivered a
            reply before a fatal hit — through one of its delivery tools,
            or, on a turn with no delivery tool, by writing the reply as
            plain text. Named generically because this schema is shared by
            every surface and each one has a different tool. No fallback
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
    # Can the USER clear this error alone — wait out / upgrade a rate limit,
    # re-login, top up — as opposed to a provider outage or a platform bug?
    # True for auth_expired / config_actionable by construction, and for a
    # driver-classified rate_limit / billing error (the claude driver stamps
    # it on every response.error). None = the driver did not classify
    # (codex / nexus_power today) — never a fabricated False. Lets the
    # frontend / inbox tell "you can fix this" from "the platform broke"
    # without parsing free-form provider text.
    self_serviceable: Optional[bool] = None


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

# error_type marker for a framework turn whose model spent its whole output
# budget on hidden reasoning and never produced an answer (NexusPower's
# OUTPUT_TRUNCATED, after its one budget-doubling replay). The cause is the
# platform's own budget choice meeting a thinking-by-default model the user
# picked — waiting does not heal it, and cooling the agent would only reject the
# user's next message. The circuit breaker therefore stays out of it entirely
# (binding rule #15: never be the interruption source). Carried as a STRUCTURED
# error_type, never matched in the message: the message can echo provider- and
# user-controlled text, so a phrase match would let caller content switch the
# breaker off. Only NexusPower's event adapter emits it (mapped from the loop's
# own ErrorType.OUTPUT_TRUNCATED, which no provider error classifies into).
OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE = "output_budget_exhausted"


# Step-3 pipeline phase identity. Step 3 splits into two user-visible phases
# so the process panel can tell "still assembling context" from "the model is
# actually running". The old single "Execute Agent Loop" phase was emitted
# BEFORE context was even built (3.1/3.2/3.3), so users saw "entered the agent
# loop" while the heavy context engineering was still ahead — misleading. Now:
#   * PHASE_BUILD_CONTEXT (step "3")  — 3.1/3.2/3.3 context assembly
#   * PHASE_RUN_AGENT     (step "3.4") — the LLM loop is actually running
# These step ids are a cross-file contract: the frontend whitelists them
# (processShared PHASE_STEP_IDS / PHASE_LABEL_KEYS) and tool sub-steps nest
# under the run-agent phase as f"{PHASE_RUN_AGENT_STEP}.{n}" (response_processor).
# Kept in this leaf schema module — same reason as the error-type markers above
# — so BOTH step_3_agent_loop (emits them) and run_recorder (derives
# current_stage from them) import without a circular import; run_recorder must
# NOT import from step_3_agent_loop, whose package __init__ eager-imports it.
PHASE_BUILD_CONTEXT_STEP = "3"
PHASE_BUILD_CONTEXT_TITLE = "Build Context"
PHASE_RUN_AGENT_STEP = "3.4"
PHASE_RUN_AGENT_TITLE = "Run Agent"
