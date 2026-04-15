"""
Job Module - Background Task Management

@file_name: job_module.py
@author: NetMind.AI
@date: 2025-11-25
@description: Provides background task capabilities for Agent

=============================================================================
Module Overview
=============================================================================

JobModule enables Agents to create and manage background tasks that execute
asynchronously without blocking user interactions.

Capabilities:
1. **Instructions** - Guide Agent on when/how to create Jobs
2. **Tools (MCP)** - job_create, job_retrieval_semantic, job_retrieval_by_id, job_retrieval_by_keywords
3. **Data** - Job history stored in job_table
4. **Hooks**:
    - hook_data_gathering: Load user's active jobs for context
    - hook_after_event_execution: LLM-powered job status update after execution

Use Cases:
- Scheduled tasks: "Every day at 8am..."
- Recurring tasks: "Weekly summary every Friday..."
- Delayed tasks: "Remind me in 1 hour..."

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                         JobModule                            │
    ├─────────────────────────────────────────────────────────────┤
    │  MCP Tools:                                                  │
    │    job_create, job_retrieval_semantic,                       │
    │    job_retrieval_by_id, job_retrieval_by_keywords            │
    ├─────────────────────────────────────────────────────────────┤
    │  Hooks:                                                      │
    │    hook_data_gathering → Load active jobs to instructions    │
    │    hook_after_event_execution → LLM analyze & update status  │
    └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
    ┌─────────────────────────────────────────────────────────────┐
    │               create_instance_jobs_table.py                   │
    │                    TableManager definition                    │
    └─────────────────────────────────────────────────────────────┘

Related:
- job_trigger.py: Background polling service that triggers job execution
- job_schema.py: Data models (JobModel, JobExecutionResult, TriggerConfig)
"""


import json
from typing import Optional, List, Dict, Any

from loguru import logger
from fastmcp import Client

# Module (same package)
from xyz_agent_context.module import XYZBaseModule, mcp_host

# Schema
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
    WorkingSource,
)
from xyz_agent_context.schema.module_schema import HookCallbackResult
from xyz_agent_context.schema.job_schema import JobModel

# Utils
from xyz_agent_context.utils import DatabaseClient, get_db_client

# Repository
from xyz_agent_context.repository import JobRepository

# Extracted sub-modules
from xyz_agent_context.module.job_module._job_mcp_tools import create_job_mcp_server
from xyz_agent_context.module.job_module._job_lifecycle import (
    handle_job_execution_result,
    update_ongoing_jobs_from_chat,
)


class JobModule(XYZBaseModule):
    """
    Job Module - Background task management module

    Provides background task capabilities:
    1. **Instructions** - Tells Agent when to create Jobs and how to fill in parameters
    2. **Tools (MCP)** - Provides job_create, job_retrieval_* and other tools
    3. **Data** - Jobs stored in job_table
    4. **Hooks**:
        - hook_data_gathering: Load user's active Job list into instructions
        - hook_after_event_execution: After Job execution, use LLM to analyze results and update status

    Collaboration with JobTrigger:
    - JobTrigger is responsible for polling and triggering Job execution
    - JobModule is responsible for creating Jobs and post-execution status updates
    """

    # =========================================================================
    # Initialization
    # =========================================================================

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None
    ):
        """
        Initialize JobModule

        Args:
            agent_id: Agent ID, used for data isolation
            user_id: User ID, the user who owns the Job
            database_client: Database client
            instance_id: Instance ID (if provided, indicates this is a specific instance operation)
            instance_ids: All instance IDs associated with the Narrative
        """
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)
        self.port = 7803  # MCP Server port

        # Initialize repository (lazy initialization)
        self._job_repo: Optional[JobRepository] = None

        # Build instructions
        agent_id_note = f"""**IMPORTANT**: Your agent_id is `{agent_id}`. When calling job tools, ALWAYS pass `agent_id="{agent_id}"` as the first parameter."""

        self.instructions = """
#### Job Module · Background Task Management

##### What is a Job?
A Job is an abstraction for background tasks, used to execute work that requires delay, scheduling, or continuous operation. Each Job has explicit trigger conditions and execution logic.

---

##### Job Types (Common Knowledge)

| Type | Description | Trigger Condition | Use Case |
|------|-------------|-------------------|----------|
| **ONE_OFF** | One-time task | Execute immediately or at scheduled_time | Single reminder, one-time report |
| **SCHEDULED** | Scheduled task | At scheduled_time | Reminders or reports at specific time points |
| **RECURRING** | Recurring task | Repeats by interval_seconds | Daily reports, periodic checks |
| **ONGOING** | Continuous task | Checks by interval_seconds until end_condition is met | Sales follow-up, goal achievement monitoring |

**ONGOING Type Details**:
ONGOING type is designed for continuous tasks (e.g., sales follow-up), features:
- **Continuous execution**: Does not complete after one run, but keeps checking until condition is met
- **End condition**: Describes the task completion criteria via `end_condition`
- **Max iterations**: Optional `max_iterations` to limit maximum execution count
- **CHAT interaction update**: When target user chats with Agent, system automatically analyzes whether end condition is met

---

##### Job Status Flow

```
PENDING --after creation--> ACTIVE --after execution--+--> COMPLETED (success)
                              |                       +--> FAILED (failure)
                              |
                              +-- ONGOING type continues until condition is met
```

---

##### Current Job Status

{jobs_information}

---

##### Job Creation Rules

**1. Existing Jobs**
If there are jobs listed above:
- Reference them when relevant to the user's query
- DO NOT create duplicate jobs that match existing ones

**2. When to Create Jobs**
Use `job_create` when the user requests a task that requires delayed, scheduled, or continuous execution:
- "Remind me to drink water every day at 8 AM" → recurring job
- "Keep following up with customer Xiaoming until they show purchase intent" → ongoing job
- "Research competitors and send me a report tomorrow" → one_off job
- Multi-step workflows → create jobs with `depends_on_job_ids` for chaining

Before creating, check existing jobs to avoid duplicates.

**3. When NOT to Create Jobs**
- Immediate questions or conversations (just respond directly)
- Tasks that can be completed right now in this conversation

**4. How to Create a Job**
Required fields:
- `title`: Task title
- `description`: Task description
- `job_type`: "one_off" / "scheduled" / "recurring" / "ongoing"
- `trigger_config`: Trigger configuration
- `payload`: Execution parameters

ONGOING type required fields:
- `trigger_config.interval_seconds`: Check interval (seconds)
- `trigger_config.end_condition`: End condition description (natural language)
- `trigger_config.max_iterations`: Optional, maximum execution count

Optional context parameters:
- **related_entity_id**: str - Target user ID for this job. IMPORTANT rules:
  - **Self-service task** (Agent works, reports back to requester): put requester's user_id
    - Example: User asks "research competitors" → "user_requester_id"
  - **Target-oriented task** (Agent acts on other user): put target user's ID
    - Example: Manager says "sell to xiaoming" → "user_xiaoming"
  - This ID will be used as the main identity when job executes (loading their context, Narrative, etc.)
  - For ONGOING jobs, target user becomes PARTICIPANT of the Narrative

- **narrative_id**: str - Link job to conversation narrative for context loading
  - When job executes, it will load the conversation summary and progress
  - Use when job needs to understand the conversation history/context
  - Typically use the current narrative_id from the ongoing conversation

---

##### Job Capabilities
- **Retrieve**: job_retrieval_semantic, job_retrieval_by_id, job_retrieval_by_keywords
- **Create**: job_create (check for duplicates before creating)
- **Modify**: job_update, job_pause, job_cancel

---

##### Job Modification Permissions

**CRITICAL: Only the Job CREATOR can modify a Job!**

Each Job has a `user_id` field that records who created it (the creator). Only the creator has permission to command the Agent to perform the following operations:

| Operation | Tool | Permission |
|-----------|------|------------|
| Add guidance | job_update (guidance_text) | Creator only |
| Reschedule | job_update (next_run_time) | Creator only |
| Pause job | job_pause | Creator only |
| Cancel job | job_cancel | Creator only |

**Permission Rules**:
- When a user requests to modify a Job, you MUST verify: **current user == Job's creator (user_id)**
- If mismatch, politely decline: "This task was created by [creator]. Only they can modify it."
- The target user (related_entity_id) has **NO** modification rights - they are only the subject of the task

---

##### Context Loading
When a job executes with context parameters, JobTrigger automatically loads:

1. **Target User Context** (if related_entity_id provided):
   - Uses related_entity_id as the main user_id for execution
   - Loads that user's Narrative, context, and preferences
   - For ONGOING jobs, target user is added as PARTICIPANT to the Narrative

2. **Narrative Summary** (if narrative_id provided):
   - Current conversation progress summary
   - Historical context from this narrative
   - Helps job understand what has been discussed

3. **Dependency Outputs** (if depends_on_job_ids provided):
   - Results from prerequisite jobs
   - Helps job chain complex workflows

---

##### ONGOING Job and CHAT Interaction
When the ONGOING Job's target user (PARTICIPANT) chats with the Agent:
1. System automatically detects ONGOING Jobs associated with the user
2. Analyzes whether conversation content satisfies the Job's `end_condition`
3. Updates Job status based on analysis:
   - Condition met -> Job marked as COMPLETED
   - Not met -> Continue follow-up, record progress

"""
        # Replace placeholders
        # self.instructions = self.instructions.replace("{agent_id_note}", agent_id_note)
        self.instance_ids = instance_ids

    def _get_repo(self) -> JobRepository:
        """Get or create JobRepository instance"""
        if self._job_repo is None:
            self._job_repo = JobRepository(self.db)
        return self._job_repo

    # =========================================================================
    # Module Config
    # =========================================================================

    def get_config(self) -> ModuleConfig:
        """
        Return JobModule configuration

        Returns:
            ModuleConfig instance
        """
        return ModuleConfig(
            name="JobModule",
            priority=4,  # Lower priority than Chat, Awareness, SocialNetwork
            enabled=True,
            description="Provides background task creation and management capabilities",
            module_type="task"  # Task module, requires LLM to decide whether to create
        )

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Collect Job information associated with the current Narrative, populate ctx_data.jobs_information

        Query strategy: prefer narrative_id, fallback to instance_ids
        Filtering: only show jobs relevant to the current user (by related_entity_id or user_id)
        """
        # Get current user ID for filtering
        current_user_id = ctx_data.user_id if ctx_data else None

        # Collect all Jobs (deduplicated by job_id), filtered by current user
        jobs_map = await self._collect_jobs(ctx_data.narrative_id, current_user_id=current_user_id)

        # With skip_module_decision_llm, there are no "created this turn" jobs from the LLM.
        # Jobs created by Claude Code via job_create MCP tool during execution won't appear here
        # since hook_data_gathering runs before the agent loop.
        existing = list(jobs_map.values())

        ctx_data.jobs_information = await self._format_jobs_information([], existing)

        if jobs_map:
            logger.info(f"JobModule: {len(existing)} active jobs for user {current_user_id}")

        return ctx_data

    async def _collect_jobs(self, narrative_id: Optional[str], current_user_id: Optional[str] = None) -> Dict[str, JobModel]:
        """
        Collect Jobs: prefer via narrative_id, fallback via instance_ids.

        When current_user_id is provided, filters to only include jobs where:
        - related_entity_id matches current user (job targets this user), OR
        - user_id matches current user (user created this job), OR
        - related_entity_id is not set (job has no specific target)

        This replaces the module decision LLM's job-filtering-by-user function.
        """
        jobs_map: Dict[str, JobModel] = {}

        # Strategy 1: Query via narrative_id
        if narrative_id:
            try:
                jobs = await self._get_repo().get_active_jobs_by_narrative(narrative_id, limit=50)
                for job in jobs:
                    jobs_map[job.job_id] = job
            except Exception as e:
                logger.warning(f"JobModule: narrative_id query failed: {e}")

        # Strategy 2: Query via instance_ids (fallback)
        for instance_id in (self.instance_ids or []):
            try:
                job = await self.get_job_instance_object_by_id(self.agent_id, instance_id)
                if job and job.job_id not in jobs_map:
                    jobs_map[job.job_id] = job
            except Exception:
                pass

        # Filter by current user if provided
        if current_user_id and jobs_map:
            filtered = {}
            for job_id, job in jobs_map.items():
                job_related = getattr(job, 'related_entity_id', None)
                job_creator = getattr(job, 'user_id', None)
                # Include if: targets current user, created by current user, or has no specific target
                if (job_related == current_user_id or
                        job_creator == current_user_id or
                        not job_related):
                    filtered[job_id] = job
                else:
                    logger.debug(f"JobModule: filtered out job {job_id} (related={job_related}, creator={job_creator}, current={current_user_id})")
            jobs_map = filtered

        return jobs_map

    async def _format_jobs_information(
        self,
        newly_created: List[JobModel],
        existing: List[JobModel]
    ) -> str:
        """Format Jobs information as Markdown"""
        if not newly_created and not existing:
            return "*No jobs for this conversation.*"

        sections = []

        if newly_created:
            rows = [await self._format_job_row(j) for j in newly_created]
            sections.append(f"""###### New Jobs ({len(newly_created)})

| Title | ID | Status | Trigger |
|-------|-----|--------|---------|
""" + "\n".join(rows))

        if existing:
            rows = [await self._format_job_row(j) for j in existing]
            sections.append(f"""###### Active Jobs ({len(existing)})

| Title | ID | Status | Trigger |
|-------|-----|--------|---------|
""" + "\n".join(rows))

        return "\n\n".join(sections)

    async def _format_job_row(self, job: JobModel) -> str:
        """Format a single Job as a table row"""
        status = job.status.value if hasattr(job.status, 'value') else str(job.status)

        # Trigger info
        trigger = "immediate"
        if job.trigger_config:
            tc = job.trigger_config
            if getattr(tc, 'cron', None):
                trigger = f"cron: {tc.cron}"
            elif getattr(tc, 'run_at', None):
                trigger = f"at: {tc.run_at}"
            elif getattr(tc, 'interval_seconds', None):
                trigger = f"every {tc.interval_seconds}s"

        return f"| {job.title} | `{job.job_id}` | {status} | {trigger} |"

        # === Plan C: Read related_job_ids from extra_data and load Job context ===
        # SocialNetworkModule has already written related_job_ids to extra_data above
        related_job_ids = ctx_data.extra_data.get("related_job_ids", [])
        if related_job_ids:
            logger.info(f"          → JobModule: Found related_job_ids in extra_data: {related_job_ids}")
            related_jobs_context = await self._load_related_jobs_context(related_job_ids, ctx_data)
            if related_jobs_context:
                ctx_data.extra_data["related_jobs_context"] = related_jobs_context
                # Also add to jobs_information so Agent can see it in instructions
                entity_name = ctx_data.extra_data.get("current_entity_name", "this user")
                ctx_data.jobs_information += f"""

###### Related Tasks for {entity_name}

**Note: This user is a target of the following sales tasks. Keep this context in mind during the conversation.**

{related_jobs_context}
"""
                logger.info(f"          → JobModule: Injected related jobs context for {len(related_job_ids)} jobs")

        return ctx_data

    async def _load_related_jobs_context(self, job_ids: List[str], ctx_data: ContextData) -> Optional[str]:
        """
        Load Job information for related_job_ids and generate context text

        Plan C implementation: Load Job details from Entity's related_job_ids

        Args:
            job_ids: List of Job IDs
            ctx_data: ContextData (for getting entity_name and other info)

        Returns:
            Formatted Job context text, or None
        """
        if not job_ids:
            return None

        try:
            from xyz_agent_context.repository import JobRepository
            from xyz_agent_context.utils.db_factory import get_db_client

            db = await get_db_client()
            job_repo = JobRepository(db)

            jobs_info = []
            for job_id in job_ids:
                job = await job_repo.get_job(job_id)
                if job:
                    status_str = job.status.value if hasattr(job.status, 'value') else str(job.status)
                    job_type_str = job.job_type.value if hasattr(job.job_type, 'value') else str(job.job_type)

                    # Build Job info
                    job_info = f"""**{job.title}** (`{job.job_id}`)
- Type: {job_type_str}
- Status: {status_str}
- Description: {job.description[:200] + '...' if job.description and len(job.description) > 200 else job.description or 'N/A'}
- Payload: {job.payload[:100] + '...' if job.payload and len(job.payload) > 100 else job.payload or 'N/A'}"""

                    jobs_info.append(job_info)

            if jobs_info:
                return "\n\n".join(jobs_info)

        except Exception as e:
            logger.error(f"          ❌ Error loading related jobs context: {e}")

        return None

    async def hook_after_event_execution(self, params: HookAfterExecutionParams) -> Optional[HookCallbackResult]:
        """
        Post-Job execution processing - Use LLM to analyze results and determine completion status

        Trigger conditions:
        1. working_source == JOB: Job-triggered execution -> LLM analysis
        2. working_source == CHAT with active JobModule instances -> update ONGOING jobs

        Delegates heavy lifting to _job_lifecycle module.
        """
        logger.debug("          JobModule.hook_after_event_execution()")

        # Collect active JobModule instance IDs from the current Narrative
        active_job_instance_ids = [
            inst_id for inst_id in (self.instance_ids or [])
            if inst_id.startswith("job_")
        ]

        # Skip if not JOB-triggered and no active Job instances
        if params.working_source != WorkingSource.JOB and not active_job_instance_ids:
            logger.debug("Not a job and no active job instances, skipping")
            return None

        # CHAT trigger: update ONGOING jobs
        if params.working_source == WorkingSource.CHAT and active_job_instance_ids:
            logger.info(f"          CHAT trigger with {len(active_job_instance_ids)} active job instances")
            await update_ongoing_jobs_from_chat(
                active_job_instance_ids=active_job_instance_ids,
                chat_content=params.final_output,
                ctx_data=params.ctx_data,
                agent_id=self.agent_id,
                repo=self._get_repo(),
                get_job_by_instance_id=self.get_job_instance_object_by_id,
            )
            return None

        # JOB trigger: LLM analysis of execution results
        if params.working_source != WorkingSource.JOB:
            return None

        async def _get_job_via_instance(instance_obj):
            """Adapter: get job by instance object's instance_id"""
            if not instance_obj or not instance_obj.instance_id:
                return None
            return await self.get_job_instance_object_by_id(self.agent_id, instance_obj.instance_id)

        return await handle_job_execution_result(
            params=params,
            repo=self._get_repo(),
            get_job_by_instance_id=_get_job_via_instance,
        )

    # =========================================================================
    # MCP Server
    # =========================================================================

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """
        Return MCP Server configuration

        JobModule provides an MCP Server containing:
        - job_create: Create Job
        - job_retrieval: Query Job

        Returns:
            MCPServerConfig instance
        """
        return MCPServerConfig(
            server_name="job_module",
            server_url=f"http://{mcp_host()}:{self.port}/sse",
            type="sse"
        )

    def create_mcp_server(self) -> Optional[Any]:
        """
        Create MCP Server instance

        Tool definitions have been extracted to _job_mcp_tools.py.
        """
        return create_job_mcp_server(self.port, JobModule.get_mcp_db_client)


    # =========================================================================
    # Instance Parts
    # =========================================================================

    async def get_instance_object_candidates(self, agent_id: str, user_id: str, user_query: str) -> List[Any]:
        """
        Return a list of Job candidates semantically related to the user query.

        Calls the job_retrieval_semantic tool via FastMCP Client for semantic search.
        Uses in-memory invocation, no network calls.

        Args:
            agent_id: Agent ID
            user_id: User ID
            user_query: User query text

        Returns:
            List of matching Jobs
        """
        mcp_server = self.create_mcp_server()

        if mcp_server is None:
            return []

        try:
            # Use Client wrapping Server for in-memory invocation
            client = Client(mcp_server)
            async with client:
                result = await client.call_tool("job_retrieval_semantic", {
                    "agent_id": agent_id,
                    "query": user_query,
                    "user_id": user_id
                })

            # Parse return result
            # call_tool returns CallToolResult object, data in content[0].text is a JSON string
            if hasattr(result, 'content') and result.content:
                text_content = result.content[0].text
                data = json.loads(text_content)
                if data.get("success"):
                    return data.get("jobs", [])

            return []

        except Exception as e:
            logger.error(f"Error in get_instance_object_candidates: {e}")
            return []
        
    async def get_job_instance_object_by_id(self, agent_id: str, instance_id: str) -> Any:
        """
        Get the associated Job object by instance_id

        Note: instance_id and job_id are different!
        - instance_id: ModuleInstance's ID
        - job_id: Job record's ID
        Job is linked to ModuleInstance via the instance_id field.

        Args:
            agent_id: Agent ID
            instance_id: Instance ID (not job_id!)

        Returns:
            Job object, or None if not found
        """
        try:
            # Use JobRepository to query by instance_id
            jobs = await self._get_repo().get_jobs_by_instance(instance_id, limit=1)
            if jobs:
                return jobs[0]
            return None

        except Exception as e:
            logger.error(f"Error in get_job_instance_object_by_id: {e}")
            return None
            
    
    def create_instance_object(self, **kwargs) -> Any:
        raise NotImplementedError("create_instance_object is not implemented")
    
    def update_instance_object(self, **kwargs) -> None:
        raise NotImplementedError("update_instance_object is not implemented")
    
    def delete_instance_object(self, **kwargs) -> None:
        raise NotImplementedError("delete_instance_object is not implemented")
    
