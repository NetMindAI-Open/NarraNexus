"""
@file_name: context.py
@author: NetMind.AI
@date: 2025-12-22
@description: AgentRuntime execution context

RunContext is a dataclass used to pass state between the various steps of the run() method.
This design avoids passing a large number of parameters to each step function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from narranexus.platform.narrative import Event, Narrative, Session
    from narranexus.platform.schema import PathExecutionResult, ModuleLoadResult
    from narranexus.platform.module_system import ModuleService
    from narranexus.platform.agent_runtime.cancellation import CancellationToken


@dataclass
class RunContext:
    """
    Execution context for AgentRuntime.run()

    Contains all state and data shared between the various steps.
    Each step function receives this object and can modify its fields.

    Attributes:
        # ===== Input Parameters (read-only) =====
        agent_id: Agent unique identifier
        user_id: User unique identifier
        input_content: User input content
        working_source: Working source identifier
        pass_mcp_servers: Passed-in MCP server specs

        # ===== Core Data Objects =====
        agent_data: Agent configuration info
        event: Event record for this conversation
        narrative_list: Selected Narrative list
        module_list: Module instance list after loading
        session: Session object
        user_chat_instances: User ChatModule instance mapping per Narrative {narrative_id: chat_instance_id}

        # ===== Manager Instances =====
        module_service: Module service

        # ===== Execution Path Related =====
        mcp_servers: MCP server spec mapping ({name: {"url": str, "headers": {str: str}?}})
        load_result: Module load result
        execution_result: Execution path result
        created_job_ids: Job IDs created in Step 2.5.3 (passed to Context Runtime)

        # ===== Markdown and Trajectory =====
        markdown_history: Markdown history content
        previous_instances: Instances before decision

        # ===== Event Logs =====
        event_log_entries: Event log entry list
        module_instances: Module instance metadata list

        # ===== Sub-step Lists for Each Step =====
        substeps_*: Sub-step lists for each step
    """

    # ===== Input Parameters (set at initialization) =====
    agent_id: str
    user_id: str
    input_content: str
    working_source: Any  # WorkingSource enum or string
    pass_mcp_servers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    job_instance_id: Optional[str] = None  # Instance ID when executing a Job
    forced_narrative_id: Optional[str] = None  # Forced Narrative ID (used for Job triggers)
    trigger_extra_data: Dict[str, Any] = field(default_factory=dict)  # Extra data from the trigger layer (e.g. channel_tag)
    turn_profile: Optional[Any] = None  # Per-turn fast-mode knobs (schema.turn_profile.TurnProfile); None = normal path

    # ===== Cancellation =====
    cancellation: Optional["CancellationToken"] = None  # Cooperative cancellation token

    # ===== Live steering =====
    # The run's SteerChannel (agent_runtime.steer_channel.SteerChannel) when the
    # orchestrator started this run steerable; None = no mid-run injection. Live
    # object, threaded run-start-to-loop exactly like `cancellation`.
    steering: Optional[Any] = None

    # ===== Core Data Objects =====
    agent_data: Optional[Dict[str, Any]] = None
    event: Optional["Event"] = None
    narrative_list: List["Narrative"] = field(default_factory=list)
    # The routing tier judged this turn to carry no durable topic (C-1). The
    # turn may still be FILED on a thread (the active one), but it must not be
    # allowed to rewrite that thread's retrieval surface — step_4 reads this.
    no_durable_topic: bool = False
    module_list: List[Any] = field(default_factory=list)
    session: Optional["Session"] = None
    awareness: str = ""  # Agent self-awareness content
    user_chat_instances: Dict[str, str] = field(default_factory=dict)  # narrative_id -> chat_instance_id

    # ===== Manager Instances =====
    module_service: Optional["ModuleService"] = None

    # ===== Execution Path Related =====
    mcp_servers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    load_result: Optional["ModuleLoadResult"] = None
    execution_result: Optional["PathExecutionResult"] = None
    # Output of the Assemble stage (ContextRuntimeOutput): set by
    # step_3_assemble_context so the Act stage (step_3_agent_loop) consumes a
    # prebuilt context instead of building its own. None = legacy single-step path.
    assembled: Optional[Any] = None
    run_id: str = ""  # trace id of this run (hooks receive it)
    registries: Optional[Any] = None  # the runtime's plugin registries (None → process KERNEL_REGISTRIES)

    # ===== Jobs Created This Round (set in Step 2.5.3, for context passing) =====
    created_job_ids: List[str] = field(default_factory=list)

    # ===== Markdown and Trajectory =====
    markdown_history: str = ""
    previous_instances: List[Any] = field(default_factory=list)

    # ===== Event Logs =====
    event_log_entries: List[Any] = field(default_factory=list)
    module_instances: List[Any] = field(default_factory=list)

    # ===== Sub-step Lists for Each Step =====
    substeps_0: List[str] = field(default_factory=list)  # Step 0: Initialization
    substeps_1: List[str] = field(default_factory=list)
    substeps_1_5: List[str] = field(default_factory=list)
    substeps_2: List[str] = field(default_factory=list)
    substeps_2_5: List[str] = field(default_factory=list)  # Step 2.5: Sync Instances
    substeps_4: List[str] = field(default_factory=list)
    substeps_5: List[str] = field(default_factory=list)

    def __post_init__(self):
        """Post-initialization processing: merge pass_mcp_servers into mcp_servers"""
        if self.pass_mcp_servers:
            self.mcp_servers.update(self.pass_mcp_servers)

    @property
    def main_narrative(self) -> Optional["Narrative"]:
        """Get the main Narrative (the first one)"""
        return self.narrative_list[0] if self.narrative_list else None

    @property
    def active_instances(self) -> List[Any]:
        """Get active Module Instances"""
        if self.load_result:
            return self.load_result.active_instances
        return []

    @property
    def execution_type(self) -> Optional[Any]:
        """Get execution path type"""
        if self.load_result:
            return self.load_result.execution_type
        return None
