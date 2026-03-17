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
- Inbox related: InboxMessageResponse, InboxListResponse, etc.
- MCP related: MCPInfo, MCPCreateRequest, etc.
- Files related: FileInfo, FileListResponse, etc.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel


# ===== Auth Schemas =====

class LoginRequest(BaseModel):
    """Request model for login"""
    user_id: str


class LoginResponse(BaseModel):
    """Response model for login"""
    success: bool
    user_id: Optional[str] = None
    error: Optional[str] = None


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
    """Request model for creating user (requires admin secret key)"""
    user_id: str
    admin_secret_key: str
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
    entity_description: Optional[str] = None
    entity_type: str
    identity_info: Dict[str, Any] = {}
    contact_info: Dict[str, Any] = {}
    tags: List[str] = []
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


# ===== File Management Schemas =====

class FileInfo(BaseModel):
    """File information"""
    filename: str
    size: int
    modified_at: str


class FileListResponse(BaseModel):
    """Response for file list"""
    success: bool
    files: List[FileInfo] = []
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
    """Response for file deletion"""
    success: bool
    filename: Optional[str] = None
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
    """Response model for a single job"""
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
    next_run_time: Optional[str] = None
    last_run_time: Optional[str] = None
    last_error: Optional[str] = None
    notification_method: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # Dependencies (obtained from module_instances table)
    instance_id: Optional[str] = None
    depends_on: List[str] = []


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


# ===== Inbox Schemas =====

class MessageSourceResponse(BaseModel):
    """Response model for message source"""
    type: Optional[str] = None
    id: Optional[str] = None


class InboxMessageResponse(BaseModel):
    """Response model for an inbox message"""
    message_id: str
    user_id: str
    message_type: str
    title: str
    content: str
    source: Optional[MessageSourceResponse] = None
    event_id: Optional[str] = None
    is_read: bool = False
    created_at: Optional[str] = None


class InboxListResponse(BaseModel):
    """Response model for inbox list"""
    success: bool
    messages: List[InboxMessageResponse] = []
    count: int = 0
    total_count: int = 0
    unread_count: int = 0
    error: Optional[str] = None


class MarkReadResponse(BaseModel):
    """Response model for mark read operations"""
    success: bool
    marked_count: int = 0
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
    """Daily cost entry"""
    date: str
    cost: float = 0.0


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
