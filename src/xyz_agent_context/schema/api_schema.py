"""
@file_name: api_schema.py
@author: NetMind.AI
@date: 2025-12-02
@description: API Request/Response Schema

Centralized management of all API route request and response models

Includes:
- Auth related: LoginRequest, LoginResponse, AgentInfo, etc.
- Agents related: AwarenessResponse, SocialNetworkEntityInfo, etc.
- Jobs related: JobResponse, JobListResponse, etc.
- RAG File related: RAGFileInfo, etc.
- MCP related: MCPInfo, MCPCreateRequest, etc.
- Files related: FileInfo, FileListResponse, etc.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel


# ===== Auth Schemas =====

class LoginRequest(BaseModel):
    """Request model for login (local: user_id only, cloud: user_id + password)"""
    user_id: str
    password: Optional[str] = None  # Required in cloud mode, optional in local


class LoginResponse(BaseModel):
    """Response model for login"""
    success: bool
    user_id: Optional[str] = None
    token: Optional[str] = None  # JWT token (cloud mode only)
    role: Optional[str] = None  # User role (cloud mode only)
    error: Optional[str] = None


class RegisterRequest(BaseModel):
    """Request model for cloud user registration"""
    user_id: str
    password: str
    invite_code: str
    display_name: Optional[str] = None


class RegisterResponse(BaseModel):
    """Response model for registration"""
    success: bool
    user_id: Optional[str] = None
    token: Optional[str] = None
    error: Optional[str] = None
    # Populated only when the system-default free-tier quota feature is
    # enabled and a quota row was successfully seeded for the new user.
    # The frontend uses these to render a welcome toast on successful
    # cloud registration.
    has_system_quota: bool = False
    initial_input_tokens: int = 0
    initial_output_tokens: int = 0


class ActiveRunInfo(BaseModel):
    """Phase C — summary of the agent's currently running run, if any.

    The frontend uses this to render the "Running" badge on the agent
    card (pulse + glow, sharing the visual language of the Jobs
    status badges). When no run is active, the parent AgentInfo
    carries ``active_run = None``.
    """
    run_id: str
    state: str  # running / cancelling / completed / cancelled / failed
    started_at: Optional[str] = None
    last_event_at: Optional[str] = None
    tool_call_count: int = 0
    current_stage: Optional[str] = None


class AgentInfo(BaseModel):
    """Response model for agent info"""
    agent_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    is_public: bool = False
    created_by: Optional[str] = None
    bootstrap_active: bool = False
    # Phase C (2026-05-13): summarise the agent's active run for the
    # frontend "running" badge. None means the agent is not currently
    # running for this user. The query is one event-table SELECT per
    # agent in the list — bounded by agent count which is small.
    active_run: Optional[ActiveRunInfo] = None


class AgentListResponse(BaseModel):
    """Response model for agent list"""
    success: bool
    agents: List[AgentInfo] = []
    count: int = 0
    error: Optional[str] = None


class CreateAgentRequest(BaseModel):
    """Request model for creating agent"""
    agent_name: Optional[str] = None
    agent_description: Optional[str] = None
    created_by: str


class CreateAgentResponse(BaseModel):
    """Response model for creating agent"""
    success: bool
    agent: Optional[AgentInfo] = None
    error: Optional[str] = None


class UpdateAgentRequest(BaseModel):
    """Request model for updating agent"""
    agent_name: Optional[str] = None
    agent_description: Optional[str] = None
    is_public: Optional[bool] = None


class UpdateAgentResponse(BaseModel):
    """Response model for updating agent"""
    success: bool
    agent: Optional[AgentInfo] = None
    error: Optional[str] = None


class DeleteAgentResponse(BaseModel):
    """Response model for deleting agent (cascade)"""
    success: bool
    agent_id: Optional[str] = None
    deleted_counts: Dict[str, int] = {}
    error: Optional[str] = None


class CreateUserRequest(BaseModel):
    """Request model for creating a local user"""
    user_id: str
    display_name: Optional[str] = None


class CreateUserResponse(BaseModel):
    """Response model for creating user"""
    success: bool
    user_id: Optional[str] = None
    error: Optional[str] = None


class UpdateTimezoneRequest(BaseModel):
    """Request model for updating user timezone"""
    user_id: str
    timezone: str  # IANA timezone format, e.g., 'Asia/Shanghai'


class UpdateTimezoneResponse(BaseModel):
    """Response model for updating user timezone"""
    success: bool
    user_id: Optional[str] = None
    timezone: Optional[str] = None
    error: Optional[str] = None


# ===== Awareness Schemas =====

class AwarenessResponse(BaseModel):
    """Response model for awareness endpoint"""
    success: bool
    awareness: Optional[str] = None
    create_time: Optional[str] = None
    update_time: Optional[str] = None
    error: Optional[str] = None


class AwarenessUpdateRequest(BaseModel):
    """Request model for updating awareness"""
    awareness: str


# ===== Social Network Schemas =====

class SocialNetworkEntityInfo(BaseModel):
    """Social network entity info"""
    entity_id: str
    entity_name: Optional[str] = None
    aliases: List[str] = []                    # Cross-system IDs and alternate names
    entity_description: Optional[str] = None
    entity_type: str
    familiarity: str = "known_of"              # direct | known_of
    identity_info: Dict[str, Any] = {}
    contact_info: Dict[str, Any] = {}
    tags: List[str] = []                       # Kept for backward compat
    keywords: List[str] = []                   # Same data as tags, new name
    relationship_strength: float = 0.0
    interaction_count: int = 0
    last_interaction_time: Optional[str] = None
    # New fields (Feature 2.2, 2.3)
    persona: Optional[str] = None              # Communication style/characteristics
    related_job_ids: List[str] = []            # Associated Job IDs
    expertise_domains: List[str] = []          # Expertise domains
    similarity_score: Optional[float] = None   # Similarity score in semantic search


class SocialNetworkResponse(BaseModel):
    """Response model for social network endpoint (single entity)"""
    success: bool
    entity: Optional[SocialNetworkEntityInfo] = None
    error: Optional[str] = None


class SocialNetworkListResponse(BaseModel):
    """Response model for social network list endpoint (all entities)"""
    success: bool
    entities: List[SocialNetworkEntityInfo] = []
    count: int = 0
    error: Optional[str] = None


class SocialNetworkSearchResponse(BaseModel):
    """Response model for social network search endpoint"""
    success: bool
    entities: List[SocialNetworkEntityInfo] = []
    count: int = 0
    search_type: str = "keyword"  # "keyword" or "semantic"
    error: Optional[str] = None


# ===== Chat History Schemas =====

class EventInfo(BaseModel):
    """Event info for chat history"""
    event_id: str
    narrative_id: Optional[str] = None
    narrative_name: Optional[str] = None
    trigger: str
    trigger_source: str
    user_id: Optional[str] = None
    final_output: str
    created_at: str
    event_log: List[Dict[str, Any]] = []


class InstanceInfo(BaseModel):
    """Instance info for displaying in Narrative"""
    instance_id: str
    module_class: str
    description: str = ""
    status: str = "active"
    dependencies: List[str] = []
    config: Dict[str, Any] = {}
    created_at: Optional[str] = None
    user_id: Optional[str] = None  # Used by frontend to filter events by user_id


class NarrativeInfo(BaseModel):
    """Narrative info for chat history"""
    narrative_id: str
    name: str
    description: str
    current_summary: str
    actors: List[Dict[str, str]] = []
    created_at: str
    updated_at: str
    instances: List[InstanceInfo] = []  # Associated Module Instances


class ChatHistoryResponse(BaseModel):
    """Response model for chat history endpoint"""
    success: bool
    narratives: List[NarrativeInfo] = []
    events: List[EventInfo] = []
    narrative_count: int = 0
    event_count: int = 0
    error: Optional[str] = None


class ClearHistoryResponse(BaseModel):
    """Response model for clear history endpoint"""
    success: bool
    narrative_ids_deleted: list = []
    narratives_count: int = 0
    events_count: int = 0
    error: Optional[str] = None


# ===== Simple Chat History Schemas =====

class SimpleChatMessage(BaseModel):
    """Simplified chat message"""
    role: str  # "user" | "assistant"
    content: str
    timestamp: Optional[str] = None
    narrative_id: Optional[str] = None  # Source Narrative
    working_source: Optional[str] = None  # "chat" | "job" | "lark" | etc.
    message_type: Optional[str] = None  # "chat" (default) | "activity"
    event_id: Optional[str] = None  # Associated Event ID (for loading event_log on demand)
    # User-uploaded attachments referenced by this message (kept as plain
    # dicts to match the JSON shape stored in instance_json_format_memory_chat
    # — the frontend types this as Attachment[]).
    attachments: Optional[List[dict]] = None


class SimpleChatHistoryResponse(BaseModel):
    """
    Simplified chat history response

    Used by the frontend to display recent interaction history with the Agent,
    without distinguishing by Narrative.
    """
    success: bool
    messages: List[SimpleChatMessage] = []
    total_count: int = 0
    error: Optional[str] = None


class EventLogToolCall(BaseModel):
    """A single tool call extracted from event_log"""
    tool_name: str
    tool_input: Dict[str, Any] = {}
    tool_output: Optional[str] = None


class EventLogTimelineEntry(BaseModel):
    """A single entry in the original event_log timeline.

    Preserves the chronological order of thinking / tool_call / tool_output /
    native_output events so the frontend can render history with the same
    inline "think → tool → think → tool → reply" cadence as the live
    streaming TurnTimeline, instead of the legacy "all thinking on top,
    all tools below" grouping that lost time ordering.
    """
    # Discriminator: "thinking" | "tool_call" | "tool_output" | "native_output" | "reply"
    type: str
    # Plain-text content (thinking / native_output / reply); empty for tool entries.
    content: Optional[str] = None
    # Tool-call fields (only set when type == "tool_call" or "tool_output").
    tool_name: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    tool_output: Optional[str] = None
    # Optional tag preserved from progress events (e.g. "helper_llm_fallback")
    # so the UI can mark fallback replies in history just like live streams.
    reply_via: Optional[str] = None


class EventLogResponse(BaseModel):
    """Response for event log detail endpoint (on-demand loading)"""
    success: bool
    event_id: str = ""
    thinking: Optional[str] = None
    tool_calls: List[EventLogToolCall] = []
    # Ordered, time-preserving view of the same data. The frontend prefers
    # this when present; the legacy thinking / tool_calls fields remain for
    # back-compat with any older client builds still in the wild.
    timeline: List[EventLogTimelineEntry] = []
    error: Optional[str] = None


# ===== File Management Schemas =====

class FileInfo(BaseModel):
    """One node in the agent-workspace directory tree.

    Returned recursively: directories carry ``is_dir=True`` and a ``children``
    list (which may be empty); regular files carry ``is_dir=False`` and
    ``children=None``. Dotfolders (name starts with ``.``) are filtered out
    on the server side and never appear in the tree.
    """
    name: str                          # basename (e.g. "index.html")
    path: str                          # workspace-relative path (e.g. "report/index.html")
    is_dir: bool
    size: int                          # 0 for directories
    modified_at: str
    children: Optional[List["FileInfo"]] = None


# Resolve the self-referential ``children: Optional[List[FileInfo]]``.
FileInfo.model_rebuild()


class FileListResponse(BaseModel):
    """Response for the workspace tree GET. ``tree`` is the top-level node list."""
    success: bool
    tree: List[FileInfo] = []
    workspace_path: str = ""
    error: Optional[str] = None


class FileUploadResponse(BaseModel):
    """Response for file upload"""
    success: bool
    filename: Optional[str] = None
    size: Optional[int] = None
    workspace_path: Optional[str] = None
    error: Optional[str] = None


class FileDeleteResponse(BaseModel):
    """Response for file/folder deletion"""
    success: bool
    path: Optional[str] = None
    error: Optional[str] = None


# ===== MCP Schemas =====

class MCPInfo(BaseModel):
    """MCP URL information"""
    mcp_id: str
    agent_id: str
    user_id: str
    name: str
    url: str
    description: Optional[str] = None
    is_enabled: bool = True
    connection_status: Optional[str] = None
    last_check_time: Optional[str] = None
    last_error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MCPListResponse(BaseModel):
    """Response for MCP list"""
    success: bool
    mcps: List[MCPInfo] = []
    count: int = 0
    error: Optional[str] = None


class MCPCreateRequest(BaseModel):
    """Request to create MCP"""
    name: str
    url: str
    description: Optional[str] = None
    is_enabled: bool = True


class MCPUpdateRequest(BaseModel):
    """Request to update MCP"""
    name: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    is_enabled: Optional[bool] = None


class MCPResponse(BaseModel):
    """Response for single MCP operation"""
    success: bool
    mcp: Optional[MCPInfo] = None
    error: Optional[str] = None


class MCPValidateResponse(BaseModel):
    """Response for MCP validation"""
    success: bool
    mcp_id: str
    connected: bool
    error: Optional[str] = None


class MCPValidateAllResponse(BaseModel):
    """Response for validating all MCPs"""
    success: bool
    results: List[MCPValidateResponse] = []
    total: int = 0
    connected: int = 0
    failed: int = 0
    error: Optional[str] = None


# ===== Job Schemas =====

class JobResponse(BaseModel):
    """
    Response model for a single job.

    v2 timezone protocol (2026-04-21): frontend/UI sees only user-local beta
    fields (next_run_at + next_run_timezone). UTC alpha fields
    (next_run_time, last_run_time) are poller-internal and NOT exposed here.
    """
    job_id: str
    agent_id: str
    user_id: str
    job_type: str
    title: str
    description: Optional[str] = None
    status: str
    payload: Optional[str] = None
    trigger_config: Optional[dict] = None
    process: Optional[List[str]] = None
    next_run_at: Optional[str] = None
    next_run_timezone: Optional[str] = None
    last_run_at: Optional[str] = None
    last_run_timezone: Optional[str] = None
    last_error: Optional[str] = None
    notification_method: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # Dependencies (obtained from module_instances table)
    instance_id: Optional[str] = None
    depends_on: List[str] = []
    # Parent narrative (jobs are owned by a narrative; bundle export uses
    # this to group jobs under their narrative for selection).
    narrative_id: Optional[str] = None


class JobListResponse(BaseModel):
    """Response model for job list"""
    success: bool
    jobs: List[JobResponse] = []
    count: int = 0
    error: Optional[str] = None


class JobDetailResponse(BaseModel):
    """Response model for job detail"""
    success: bool
    job: Optional[JobResponse] = None
    error: Optional[str] = None


# ===== RAG File Schemas =====

class RAGFileInfo(BaseModel):
    """RAG file information with upload status"""
    filename: str
    size: int
    modified_at: str
    upload_status: str  # "pending", "uploading", "completed", "failed"
    error_message: Optional[str] = None


class RAGFileListResponse(BaseModel):
    """Response for RAG file list"""
    success: bool
    files: List[RAGFileInfo] = []
    total_count: int = 0
    completed_count: int = 0
    pending_count: int = 0
    error: Optional[str] = None


class RAGFileUploadResponse(BaseModel):
    """Response for RAG file upload"""
    success: bool
    filename: Optional[str] = None
    size: Optional[int] = None
    upload_status: Optional[str] = None
    error: Optional[str] = None


class RAGFileDeleteResponse(BaseModel):
    """Response for RAG file deletion"""
    success: bool
    filename: Optional[str] = None
    error: Optional[str] = None


# ===== Cost Schemas =====

class CostModelBreakdown(BaseModel):
    """Cost breakdown for a single model"""
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    call_count: int = 0


class CostDailyEntry(BaseModel):
    """Daily token usage entry"""
    date: str
    input_tokens: int = 0
    output_tokens: int = 0


class CostSummary(BaseModel):
    """Aggregated cost summary"""
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    by_model: Dict[str, CostModelBreakdown] = {}
    daily: List[CostDailyEntry] = []


class CostRecord(BaseModel):
    """Single cost record"""
    id: int
    agent_id: str
    event_id: Optional[str] = None
    call_type: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: float = 0.0
    created_at: Optional[str] = None


class CostResponse(BaseModel):
    """Response for cost endpoint"""
    success: bool
    summary: Optional[CostSummary] = None
    records: List[CostRecord] = []
    total_count: int = 0
    error: Optional[str] = None
