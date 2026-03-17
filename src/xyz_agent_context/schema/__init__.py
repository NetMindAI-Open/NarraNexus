"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2025-11-15
@description: Schema package exports

Centralized management of all data models for convenient reference by other modules

Usage:
    from xyz_agent_context.schema import (
        ModuleConfig,
        ContextData,
        Event,
        Narrative,
        ...
    )
"""

# ===== Module Schema =====
from .module_schema import (
    ModuleConfig,
    MCPServerConfig,
    ModuleInstructions,
)

# ===== Instance Schema (ModuleInstance standalone) =====
from .instance_schema import (
    InstanceStatus,
    LinkType,
    ModuleInstanceRecord,
    ModuleInstance,
    InstanceNarrativeLink,
)

# ===== Context Schema =====
from .context_schema import (
    ContextData,
    ContextRuntimeOutput,
)

# ===== Runtime Message Schema =====
from .runtime_message import (
    # Enums
    MessageType,
    ProgressStatus,
    # Messages
    ProgressMessage,
    AgentTextDelta,
    AgentThinking,
    AgentToolCall,
    ErrorMessage,
)

# ===== Job Schema =====
from .job_schema import (
    JobType,
    JobStatus,
    JobModel,
    TriggerConfig,
)

# ===== Inbox Schema (belongs to ChatModule) =====
from .inbox_schema import (
    InboxMessageType,
    MessageSource,
    InboxMessage,
)

# ===== Hook Schema =====
from .hook_schema import (
    WorkingSource,  # Execution source enum
    HookExecutionContext,
    HookIOData,
    HookExecutionTrace,
    HookAfterExecutionParams,
)

# ===== Channel Tag Schema =====
from .channel_tag import ChannelTag

# ===== RAG Store Schema =====
from .rag_store_schema import (
    RAGStoreModel,
    KeywordsUpdateRequest,
)

# ===== Decision Schema (Approach 2: Intelligent Decision) =====
from .decision_schema import (
    ExecutionPath,
    DirectTriggerConfig,
    ModuleLoadResult,
    PathExecutionResult,
)

# ===== Entity Schema (Data Entity Models) =====
from .entity_schema import (
    # Enums
    UserStatus,
    # Entities
    SocialNetworkEntity,
    User,
    Agent,
    MCPUrl,
)

# ===== API Schema (API Request/Response Models) =====
from .api_schema import (
    # Auth
    LoginRequest,
    LoginResponse,
    AgentInfo,
    AgentListResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    UpdateAgentRequest,
    UpdateAgentResponse,
    DeleteAgentResponse,
    CreateUserRequest,
    CreateUserResponse,
    UpdateTimezoneRequest,
    UpdateTimezoneResponse,
    # Awareness
    AwarenessResponse,
    AwarenessUpdateRequest,
    # Social Network
    SocialNetworkEntityInfo,
    SocialNetworkResponse,
    SocialNetworkListResponse,
    SocialNetworkSearchResponse,
    # Chat History
    EventInfo,
    NarrativeInfo,
    ChatHistoryResponse,
    ClearHistoryResponse,
    # Simple Chat History
    SimpleChatMessage,
    SimpleChatHistoryResponse,
    # Files
    FileInfo,
    FileListResponse,
    FileUploadResponse,
    FileDeleteResponse,
    # MCP
    MCPInfo,
    MCPListResponse,
    MCPCreateRequest,
    MCPUpdateRequest,
    MCPResponse,
    MCPValidateResponse,
    MCPValidateAllResponse,
    # Jobs
    JobResponse,
    JobListResponse,
    JobDetailResponse,
    # Inbox
    MessageSourceResponse,
    InboxMessageResponse,
    InboxListResponse,
    MarkReadResponse,
    # RAG Files
    RAGFileInfo,
    RAGFileListResponse,
    RAGFileUploadResponse,
    RAGFileDeleteResponse,
    # Cost
    CostModelBreakdown,
    CostDailyEntry,
    CostSummary,
    CostRecord,
    CostResponse,
)

# ===== Skill Schema =====
from .skill_schema import (
    SkillInfo,
    SkillListResponse,
    SkillOperationResponse,
    SkillStudyResponse,
)

# ===== A2A Protocol Schema =====
from .a2a_schema import (
    # Enums
    TaskState,
    MessageRole,
    # Message Parts
    TextPart,
    FilePart,
    DataPart,
    Part,
    # Core Objects
    Message as A2AMessage,
    TaskStatus,
    Artifact,
    Task,
    # Agent Card
    ProviderInfo,
    AgentCapabilities,
    AgentSkill,
    AgentCard,
    # JSON-RPC
    JSONRPCRequest,
    JSONRPCError,
    JSONRPCResponse,
    # Method Params
    TaskSendConfiguration,
    TaskSendParams,
    TaskGetParams,
    TaskCancelParams,
    # SSE Events
    TaskStatusUpdateEvent,
    TaskArtifactUpdateEvent,
    # Error Codes
    A2AErrorCodes,
)

# ===== Export All =====
__all__ = [
    # Module Schema
    "ModuleConfig",
    "MCPServerConfig",
    "ModuleInstructions",

    # Instance Schema (ModuleInstance standalone)
    "InstanceStatus",
    "LinkType",
    "ModuleInstanceRecord",
    "ModuleInstance",
    "InstanceNarrativeLink",

    # Context Schema
    "ContextData",
    "ContextRuntimeOutput",

    # Runtime Message Schema
    "MessageType",
    "ProgressStatus",
    "ProgressMessage",
    "AgentTextDelta",
    "AgentThinking",
    "AgentToolCall",

    # A2A Protocol Schema
    "TaskState",
    "MessageRole",
    "TextPart",
    "FilePart",
    "DataPart",
    "Part",
    "A2AMessage",
    "TaskStatus",
    "Artifact",
    "Task",
    "ProviderInfo",
    "AgentCapabilities",
    "AgentSkill",
    "AgentCard",
    "JSONRPCRequest",
    "JSONRPCError",
    "JSONRPCResponse",
    "TaskSendConfiguration",
    "TaskSendParams",
    "TaskGetParams",
    "TaskCancelParams",
    "TaskStatusUpdateEvent",
    "TaskArtifactUpdateEvent",
    "A2AErrorCodes",
    
    # Job Schema
    "JobType",
    "JobStatus",
    "JobModel",
    "TriggerConfig",

    # Inbox Schema (belongs to ChatModule)
    "InboxMessageType",
    "MessageSource",
    "InboxMessage",

    # Hook Schema
    "WorkingSource",
    "HookExecutionContext",
    "HookIOData",
    "HookExecutionTrace",
    "HookAfterExecutionParams",

    # Channel Tag Schema
    "ChannelTag",

    # RAG Store Schema
    "RAGStoreModel",
    "KeywordsUpdateRequest",

    # Decision Schema (Approach 2: Intelligent Decision)
    "ExecutionPath",
    "DirectTriggerConfig",
    "ModuleLoadResult",
    "PathExecutionResult",

    # Entity Schema (Data Entity Models)
    "UserStatus",
    "SocialNetworkEntity",
    "User",
    "Agent",
    "MCPUrl",

    # API Schema (API Request/Response Models)
    # Auth
    "LoginRequest",
    "LoginResponse",
    "AgentInfo",
    "AgentListResponse",
    "CreateAgentRequest",
    "CreateAgentResponse",
    "UpdateAgentRequest",
    "UpdateAgentResponse",
    "DeleteAgentResponse",
    "CreateUserRequest",
    "CreateUserResponse",
    "UpdateTimezoneRequest",
    "UpdateTimezoneResponse",
    # Awareness
    "AwarenessResponse",
    "AwarenessUpdateRequest",
    # Social Network
    "SocialNetworkEntityInfo",
    "SocialNetworkResponse",
    "SocialNetworkListResponse",
    "SocialNetworkSearchResponse",
    # Chat History
    "EventInfo",
    "NarrativeInfo",
    "ChatHistoryResponse",
    "ClearHistoryResponse",
    # Simple Chat History
    "SimpleChatMessage",
    "SimpleChatHistoryResponse",
    # Files
    "FileInfo",
    "FileListResponse",
    "FileUploadResponse",
    "FileDeleteResponse",
    # MCP
    "MCPInfo",
    "MCPListResponse",
    "MCPCreateRequest",
    "MCPUpdateRequest",
    "MCPResponse",
    "MCPValidateResponse",
    "MCPValidateAllResponse",
    # Jobs (API)
    "JobResponse",
    "JobListResponse",
    "JobDetailResponse",
    # Inbox (API)
    "MessageSourceResponse",
    "InboxMessageResponse",
    "InboxListResponse",
    "MarkReadResponse",
    # RAG Files
    "RAGFileInfo",
    "RAGFileListResponse",
    "RAGFileUploadResponse",
    "RAGFileDeleteResponse",
    # Skill Schema
    "SkillInfo",
    "SkillListResponse",
    "SkillOperationResponse",
    "SkillStudyResponse",
]
