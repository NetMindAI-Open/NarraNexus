"""
Hook Schema - Hook system data models

@file_name: hook_schema.py
@author: NetMind.AI
@date: 2025-11-27
@description: Defines data models used by the Hook system for more structured parameter passing

=============================================================================
Design Goals
=============================================================================

Structuring the **kwargs parameters of after_turn into several data models:

1. HookExecutionContext - Execution context (required)
   - event_id, agent_id, user_id, working_source

2. HookIOData - Input/output data (required)
   - input_content, final_output

3. HookExecutionTrace - Execution trace (optional)
   - event_log, agent_loop_response

4. ctx_data: ContextData - Complete context (existing, optional)

Usage example:
    await hook_manager.after_turn(
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

from narranexus.platform.schema.open_enum import OpenStrEnum

if TYPE_CHECKING:
    from narranexus.platform.schema.module_schema import ModuleInstance
    from narranexus.platform.narrative.models import Event, Narrative


# ctx_data.extra_data marker: stamped by MessageBusTrigger on team-room
# turns (same MESSAGE_BUS working_source as ordinary bus turns, different
# reply VERB — a team room takes `message_team`, a peer DM takes
# `message_agent`). Consumer: MessageBusModule's desk hooks, which declare one
# verb and suppress the other on this marker. Lives here (not in message_bus)
# because both sides of the platform read it and schema is the shared base layer.
#
# It used to mean the OPPOSITE: while the room auto-posted plain text and its
# prompt forbade delivery tools, `context_runtime` emptied the turn's whole
# expressive surface on this key. That exception is gone — the room takes a tool
# call like every other surface — and the comment describing it outlived the
# mechanism.
BUS_TEAM_ROOM_EXTRA_KEY = "bus_team_room"

# extra_data marker: this turn's reply IS its plain text, so it has no reply
# tool at all. Patrol is the only such surface — the platform asks the lead to
# compose the room's status line and posts it under the room's own marker, so a
# `message_team` call would be the lead chatting mid-patrol, which is what the
# patrol prompt forbids in so many words.
#
# It exists because "forbid it in prose" is the thing this redesign is a
# reaction to. A patrol turn carries BUS_TEAM_ROOM_EXTRA_KEY, and on that key
# `message_team` is declared as the turn's default reply tool and named by both
# frameworks' reply reminders — contradicting the prompt three lines above it,
# and on NexusPower actively nudging the lead to do the forbidden thing. On
# this marker the module declares nothing and takes both send verbs off the
# desk, and the trigger withholds the mute-turn nudge.
BUS_PLAIN_TEXT_TURN_EXTRA_KEY = "bus_plain_text_turn"


def is_plain_text_turn(ctx_data: Any) -> bool:
    """Does this turn deliver by SPEAKING (patrol) rather than via a reply tool?

    The single home of the ``BUS_PLAIN_TEXT_TURN_EXTRA_KEY`` predicate. Every
    module that declares reply tools must withhold that declaration on such a
    turn, or its reply reminder names a tool the patrol prompt forbids — so the
    check lives once, next to the marker it reads, instead of being copied into
    each declarer.
    """
    extra = getattr(ctx_data, "extra_data", None) or {}
    return bool(extra.get(BUS_PLAIN_TEXT_TURN_EXTRA_KEY))


class WorkingSource(OpenStrEnum):
    """
    Agent execution source - Identifies the origin that triggered Agent execution

    An OPEN enum (plugin platform batch 4): the core sources are class
    attributes exactly like the old ``Enum`` members, and EVERY IM channel —
    the builtins included — registers its own value from its ChannelDescriptor
    (``WorkingSource.register("lark")``): the platform holds no channel-name
    table, and a channel plugin needs no platform release to name its turns.
    Registering a channel source also registers the matching
    ``narrative.models.TriggerType`` member, so its events are labelled by
    surface like every other.
    The enum surface is kept: ``.value`` / ``.name``, ``WorkingSource("job")``,
    ``from_string``, iteration, membership, ``is_automated`` /
    ``is_user_initiated`` / ``is_from_human``, pydantic and JSON (it is a
    ``str``, so ``json.dumps`` writes the value).

    Values:
        CHAT: Triggered by user conversation (default)
        JOB: Triggered by JobTrigger task
        A2A: Triggered by Agent-to-Agent call
        CALLBACK: Triggered by callback after Job completion (dependency chain activation)
        … and one value per IM channel (builtin or plugin).
    """

    # Core members — declared here for type checkers; bound by ``_add`` below.
    CHAT: "WorkingSource"
    JOB: "WorkingSource"
    A2A: "WorkingSource"
    CALLBACK: "WorkingSource"
    SKILL_STUDY: "WorkingSource"
    MESSAGE_BUS: "WorkingSource"
    MANYFOLD: "WorkingSource"

    @classmethod
    def register(cls, value: str, *, name: str | None = None) -> "WorkingSource":
        """Register an IM channel's source (idempotent) — and its ``TriggerType`` twin."""
        from narranexus.platform.narrative.models import TriggerType

        member = super().register(value, name=name)
        TriggerType.register(str.__str__(member), name=member.name)
        return member

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
        """
        try:
            return cls(value.lower())
        except ValueError:
            valid = [e.value for e in cls]
            raise ValueError(f"Invalid WorkingSource: '{value}'. Valid values: {valid}")

    def is_automated(self) -> bool:
        """
        Check if this is an automated (not directly user-triggered) execution

        Returns:
            True if triggered by JOB, A2A, CALLBACK, MESSAGE_BUS, MANYFOLD or any IM channel.
        """
        return self in (
            WorkingSource.JOB,
            WorkingSource.A2A,
            WorkingSource.CALLBACK,
            WorkingSource.MESSAGE_BUS,
            WorkingSource.MANYFOLD,
        ) or WorkingSource.is_channel(self)

    def is_user_initiated(self) -> bool:
        """
        Check if this is a user-initiated execution

        Returns:
            True if triggered by CHAT
        """
        return self == WorkingSource.CHAT

    def is_from_human(self) -> bool:
        """Return True iff the run is replying to a human (not an agent /
        background system).

        Rule of thumb (set by Bin哥, 2026-05-19):
          - Anything that ultimately delivers a reply to a real person —
            CHAT (UI), LARK / SLACK / TELEGRAM (IM channels, builtin or
            plugin) — is "from human". Reply with warmth; even a one-line
            ACK is better than cold silence.
          - JOB (cron / dependency triggers) / MESSAGE_BUS (peer agent) /
            CALLBACK (post-job hook) / SKILL_STUDY (internal maintenance)
            are NOT from a human. Reply tersely or stay silent when there's
            nothing of substance to add.

        Used by:
          - `narrative_service.select()` and Step 4's last_response writer:
            only human-source runs anchor Session.last_query / last_response /
            current_narrative_id. Background runs leave them frozen so the
            next real user message gets continuity scored against the prior
            real exchange.
          - `chat_module` and `message_bus_module` prompts: switch between
            warm / concise reply discipline on this signal.
        """
        return self not in (
            WorkingSource.JOB,
            WorkingSource.MESSAGE_BUS,
            WorkingSource.CALLBACK,
            WorkingSource.SKILL_STUDY,
        )


# Core members (the former Enum body). IM channels — builtin and plugin alike —
# get theirs from their ChannelDescriptor, registered in exactly ONE place:
# ``channel/contributions.py:register_working_source``, called by
# ``contributions_from`` when the channel plugin's ``contribution.py`` is imported
# at boot. It used to also fire from ``descriptor.py`` at import and from the READ
# paths of the channel/trigger views (a cache miss on ``SUPPORTED_CHANNELS``), so
# whether ``WorkingSource("lark")`` resolved depended on who had read what.
WorkingSource._add("CHAT", "chat")
WorkingSource._add("JOB", "job")
WorkingSource._add("A2A", "a2a")
WorkingSource._add("CALLBACK", "callback")  # Callback triggered after Job completion
WorkingSource._add("SKILL_STUDY", "skill_study")  # Skill study trigger
WorkingSource._add("MESSAGE_BUS", "message_bus")  # Triggered by MessageBus message
WorkingSource._add("MANYFOLD", "manyfold")  # Triggered by Manyfold platform via OpenAI-compat endpoint


BUS_ERRAND_TURN_SOURCE = "message_bus_errand"

#: working_source values MessageBusTrigger produces for peer-agent (A2A) and
#: team turns — the single source of truth for "a bus-produced turn". Consumers
#: that special-case these (the Activity Log's peer surfacing, chat_module's
#: activity summary) import this instead of re-hardcoding the pair, so adding a
#: new bus transport updates every consumer at once. Values (not enum members)
#: because the stored working_source is a JSON string.
#:
#: NOTE: today ``_invoke_runtime`` writes MESSAGE_BUS for EVERY bus turn
#: (A2A DM, team room, team patrol) — production never emits ``"a2a"``. The
#: A2A member is kept as a reserved slot for a future dedicated transport, so
#: any consumer already handles it. Tests that assert on ``"a2a"`` are pinning
#: this contract, not exercising a live path.
BUS_PRODUCED_SOURCES = (WorkingSource.A2A.value, WorkingSource.MESSAGE_BUS.value)


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
        interrupted: True when the user stopped the run mid-turn — the
            output above is partial-but-real (interrupt continuity)
    """
    input_content: str
    final_output: str
    interrupted: bool = False


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
    Complete parameter package for after_turn

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
        async def after_turn(self, params: HookAfterExecutionParams):
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

    # === Narrative related (for MemoryModule writing, etc.) ===
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
