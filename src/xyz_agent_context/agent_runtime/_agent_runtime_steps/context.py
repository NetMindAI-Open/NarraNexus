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
    from xyz_agent_context.narrative import Event, Narrative, Session
    from xyz_agent_context.schema import PathExecutionResult, ModuleLoadResult
    from xyz_agent_context.module import ModuleService
    from xyz_agent_context.agent_runtime.cancellation import CancellationToken


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
        pass_mcp_urls: Passed-in MCP URLs

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
        mcp_urls: MCP server URL mapping
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
    pass_mcp_urls: Dict[str, str] = field(default_factory=dict)
    job_instance_id: Optional[str] = None  # Instance ID when executing a Job
    forced_narrative_id: Optional[str] = None  # Forced Narrative ID (used for Job triggers)
    trigger_extra_data: Dict[str, Any] = field(default_factory=dict)  # Trigger 层传入的附加数据（如 channel_tag）

    # ===== Cancellation =====
    cancellation: Optional["CancellationToken"] = None  # Cooperative cancellation token

    # ===== Core Data Objects =====
    agent_data: Optional[Dict[str, Any]] = None
    event: Optional["Event"] = None
    narrative_list: List["Narrative"] = field(default_factory=list)
    module_list: List[Any] = field(default_factory=list)
    session: Optional["Session"] = None
    awareness: str = ""  # Agent self-awareness content
    user_chat_instances: Dict[str, str] = field(default_factory=dict)  # narrative_id -> chat_instance_id

    # ===== Manager Instances =====
    module_service: Optional["ModuleService"] = None

    # ===== Execution Path Related =====
    mcp_urls: Dict[str, str] = field(default_factory=dict)
    load_result: Optional["ModuleLoadResult"] = None
    execution_result: Optional["PathExecutionResult"] = None
    query_embedding: Optional[Any] = None

    # ===== Jobs Created This Round (set in Step 2.5.3, for context passing) =====
    created_job_ids: List[str] = field(default_factory=list)

    # ===== Phase 2: EverMemOS Cache (for MemoryModule use) =====
    evermemos_memories: Dict[str, Any] = field(default_factory=dict)

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
        """Post-initialization processing: merge pass_mcp_urls into mcp_urls"""
        if self.pass_mcp_urls:
            self.mcp_urls.update(self.pass_mcp_urls)

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
