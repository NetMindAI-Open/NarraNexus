"""
@file_name: a2a_schema.py
@author: NetMind.AI
@date: 2025-11-25
@description: A2A (Agent-to-Agent) protocol data models

A2A protocol is an open standard proposed by Google for communication and
interoperability between AI Agents.
This file defines all data models conforming to the A2A protocol specification.

Protocol Version: 0.3
Specification: https://a2a-protocol.org/latest/specification/
GitHub: https://github.com/google-a2a/A2A

Core Concepts:
-----------
1. Agent Card: Metadata description of an Agent, used for service discovery
2. Task: Work unit containing status, history, and artifacts
3. Message: Messages exchanged between Agents
4. Part: Components of a message (text, file, data)
5. JSON-RPC 2.0: Communication protocol format

Data Flow:
-------
Client                              Remote Agent
   |                                      |
   |  1. GET /.well-known/agent.json     |
   |------------------------------------->|  (Discover Agent capabilities)
   |<-------------------------------------|
   |              AgentCard               |
   |                                      |
   |  2. POST / (JSON-RPC: tasks/send)   |
   |------------------------------------->|  (Send task)
   |<-------------------------------------|
   |              Task                    |
   |                                      |
   |  3. SSE tasks/streamMessage         |
   |------------------------------------->|  (Streaming updates)
   |<.....................................|
   |      TaskStatusUpdateEvent           |
   |      TaskArtifactUpdateEvent         |
   |              ...                     |
"""

from typing import Optional, List, Dict, Any, Union, Literal
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
import uuid


# =============================================================================
# Enum Definitions
# =============================================================================

class TaskState(str, Enum):
    """
    Task state enum

    Task lifecycle states defined by the A2A protocol:
    - submitted: Task has been submitted, awaiting processing
    - working: Agent is processing the task
    - input-required: Additional user input is needed
    - completed: Task completed successfully
    - failed: Task execution failed
    - cancelled: Task was cancelled
    - rejected: Task was rejected (Agent cannot handle it)
    """
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class MessageRole(str, Enum):
    """
    Message role enum

    Identifies the sender of a message:
    - user: Message sent by the user/client Agent
    - agent: Response sent by the remote Agent
    """
    USER = "user"
    AGENT = "agent"


# =============================================================================
# Message Parts
# =============================================================================

class TextPart(BaseModel):
    """
    Text message part

    Used to transmit plain text content; the most commonly used message type.

    Attributes:
        type: Fixed as "text"
        text: Text content

    Example:
        ```json
        {
            "type": "text",
            "text": "Hello, please help me analyze this data"
        }
        ```
    """
    type: Literal["text"] = "text"
    text: str = Field(..., description="Text content")


class FilePart(BaseModel):
    """
    File message part

    Used to transmit file content, with files encoded in base64.

    Attributes:
        type: Fixed as "file"
        file: File info dictionary containing:
            - name: Filename (optional)
            - mimeType: MIME type
            - bytes: base64-encoded file content (mutually exclusive with uri)
            - uri: File URI (mutually exclusive with bytes)

    Example:
        ```json
        {
            "type": "file",
            "file": {
                "name": "report.pdf",
                "mimeType": "application/pdf",
                "bytes": "JVBERi0xLjQK..."
            }
        }
        ```
    """
    type: Literal["file"] = "file"
    file: Dict[str, Any] = Field(
        ...,
        description="File info containing name, mimeType, bytes/uri"
    )


class DataPart(BaseModel):
    """
    Data message part

    Used to transmit structured JSON data, suitable for forms, configurations, etc.

    Attributes:
        type: Fixed as "data"
        data: Structured data (arbitrary JSON object)
        mimeType: MIME type of the data, defaults to "application/json"

    Example:
        ```json
        {
            "type": "data",
            "data": {
                "temperature": 0.7,
                "max_tokens": 1000
            },
            "mimeType": "application/json"
        }
        ```
    """
    type: Literal["data"] = "data"
    data: Dict[str, Any] = Field(..., description="Structured data")
    mimeType: str = Field(
        default="application/json",
        description="MIME type of the data"
    )


# Union type: A message part can be text, file, or data
Part = Union[TextPart, FilePart, DataPart]


# =============================================================================
# Message
# =============================================================================

class Message(BaseModel):
    """
    A2A message object

    The basic unit for exchanging information between Agents.
    Each message contains a role identifier and one or more content parts.

    Attributes:
        role: Message sender role (user/agent)
        parts: List of message content parts
        messageId: Unique message identifier (optional, for idempotency)
        taskId: Associated task ID (optional)
        contextId: Context ID (optional, for associating multi-turn conversations)
        referenceTaskIds: List of referenced task IDs (optional)
        metadata: Custom metadata (optional)

    Example:
        ```json
        {
            "role": "user",
            "parts": [
                {"type": "text", "text": "Please analyze this image"},
                {"type": "file", "file": {"mimeType": "image/png", "bytes": "..."}}
            ],
            "contextId": "conv-123"
        }
        ```
    """
    role: MessageRole = Field(..., description="Message role")
    parts: List[Part] = Field(default_factory=list, description="Message content parts")
    messageId: Optional[str] = Field(
        default=None,
        description="Unique message identifier for idempotency checks"
    )
    taskId: Optional[str] = Field(default=None, description="Associated task ID")
    contextId: Optional[str] = Field(
        default=None,
        description="Context ID for associating multi-turn conversations"
    )
    referenceTaskIds: Optional[List[str]] = Field(
        default=None,
        description="Referenced task IDs"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Custom metadata"
    )

    @classmethod
    def create_user_message(cls, text: str, context_id: Optional[str] = None) -> "Message":
        """
        Convenience method: Create a user text message

        Args:
            text: Message text
            context_id: Context ID (optional)

        Returns:
            Message object
        """
        return cls(
            role=MessageRole.USER,
            parts=[TextPart(text=text)],
            contextId=context_id
        )

    @classmethod
    def create_agent_message(cls, text: str, task_id: Optional[str] = None) -> "Message":
        """
        Convenience method: Create an Agent response message

        Args:
            text: Response text
            task_id: Task ID (optional)

        Returns:
            Message object
        """
        return cls(
            role=MessageRole.AGENT,
            parts=[TextPart(text=text)],
            taskId=task_id
        )


# =============================================================================
# Task Status
# =============================================================================

class TaskStatus(BaseModel):
    """
    Task status object

    Describes the current execution state of a task, including the state value,
    an optional message, and a timestamp.

    Attributes:
        state: Task state enum value
        message: Status-related message (optional, e.g., error info)
        timestamp: Status update time (ISO 8601 format)

    Example:
        ```json
        {
            "state": "working",
            "message": {"role": "agent", "parts": [{"type": "text", "text": "Processing..."}]},
            "timestamp": "2025-11-25T10:30:00Z"
        }
        ```
    """
    state: TaskState = Field(..., description="Task state")
    message: Optional[Message] = Field(
        default=None,
        description="Status-related message"
    )
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="Status update time"
    )


# =============================================================================
# A2AArtifact
# =============================================================================

class A2AArtifact(BaseModel):
    """
    Task artifact

    Results produced during task execution, which can be files, data, or other content.

    Attributes:
        artifactId: Unique artifact identifier
        name: Artifact name (optional)
        description: Artifact description (optional)
        parts: Artifact content parts
        metadata: Custom metadata (optional)

    Example:
        ```json
        {
            "artifactId": "artifact-001",
            "name": "Analysis Report",
            "parts": [
                {"type": "text", "text": "## Analysis Results\\n..."},
                {"type": "file", "file": {"name": "chart.png", "mimeType": "image/png", "bytes": "..."}}
            ]
        }
        ```
    """
    artifactId: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique artifact identifier"
    )
    name: Optional[str] = Field(default=None, description="Artifact name")
    description: Optional[str] = Field(default=None, description="Artifact description")
    parts: List[Part] = Field(default_factory=list, description="Artifact content")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Metadata")


# =============================================================================
# Task
# =============================================================================

class Task(BaseModel):
    """
    A2A task object

    A task is the core concept of the A2A protocol, representing a unit of work.
    Tasks have a complete lifecycle from creation to completion/failure/cancellation.

    Attributes:
        id: Unique task identifier
        contextId: Context ID for associating multi-turn conversations
        status: Current task status
        artifacts: List of task artifacts
        history: Message history
        metadata: Custom metadata

    Lifecycle:
        submitted -> working -> completed/failed/cancelled
                  -> input-required -> (user input) -> working -> ...

    Example:
        ```json
        {
            "id": "task-abc123",
            "contextId": "ctx-xyz789",
            "status": {
                "state": "completed",
                "timestamp": "2025-11-25T10:35:00Z"
            },
            "artifacts": [...],
            "history": [...]
        }
        ```
    """
    id: str = Field(
        default_factory=lambda: f"task-{uuid.uuid4().hex[:12]}",
        description="Unique task identifier"
    )
    contextId: Optional[str] = Field(
        default_factory=lambda: f"ctx-{uuid.uuid4().hex[:12]}",
        description="Context ID"
    )
    status: TaskStatus = Field(
        default_factory=lambda: TaskStatus(state=TaskState.SUBMITTED),
        description="Task status"
    )
    artifacts: List[A2AArtifact] = Field(
        default_factory=list,
        description="Task artifacts"
    )
    history: List[Message] = Field(
        default_factory=list,
        description="Message history"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Custom metadata"
    )

    def update_status(self, state: TaskState, message: Optional[Message] = None) -> None:
        """
        Update task status

        Args:
            state: New state
            message: Status-related message (optional)
        """
        self.status = TaskStatus(state=state, message=message)

    def add_artifact(self, artifact: A2AArtifact) -> None:
        """Add an artifact"""
        self.artifacts.append(artifact)

    def add_message(self, message: Message) -> None:
        """Add a message to history"""
        self.history.append(message)


# =============================================================================
# Agent Card (Service Discovery)
# =============================================================================

class ProviderInfo(BaseModel):
    """
    Agent provider information

    Attributes:
        organization: Organization name
        url: Organization website (optional)
    """
    organization: str = Field(..., description="Organization name")
    url: Optional[str] = Field(default=None, description="Organization website")


class AgentCapabilities(BaseModel):
    """
    Agent capability declaration

    Declares the protocol features supported by the Agent.

    Attributes:
        streaming: Whether SSE streaming responses are supported
        pushNotifications: Whether Webhook push notifications are supported
        stateTransitionHistory: Whether state transition history is included in responses
    """
    streaming: bool = Field(default=True, description="Whether streaming responses are supported")
    pushNotifications: bool = Field(default=False, description="Whether push notifications are supported")
    stateTransitionHistory: bool = Field(
        default=False,
        description="Whether state transition history is supported"
    )


class AgentSkill(BaseModel):
    """
    Agent skill definition

    Describes a specific capability of the Agent.

    Attributes:
        id: Unique skill identifier
        name: Skill name
        description: Skill description
        tags: Skill tags (for classification and search)
        examples: Usage examples
        inputModes: Supported input modes
        outputModes: Supported output modes
    """
    id: str = Field(..., description="Skill ID")
    name: str = Field(..., description="Skill name")
    description: str = Field(..., description="Skill description")
    tags: List[str] = Field(default_factory=list, description="Skill tags")
    examples: List[str] = Field(default_factory=list, description="Usage examples")
    inputModes: List[str] = Field(
        default_factory=lambda: ["text/plain"],
        description="Supported input MIME types"
    )
    outputModes: List[str] = Field(
        default_factory=lambda: ["text/plain"],
        description="Supported output MIME types"
    )


class AgentCard(BaseModel):
    """
    Agent Card - Metadata description of an Agent

    Agent Card is the service discovery mechanism of the A2A protocol.
    Clients obtain the Agent Card to learn about the Agent's capabilities,
    skills, and connection information.

    Agent Card is typically hosted at:
    - /.well-known/agent.json (static file)
    - Or retrieved via the agentCard/get JSON-RPC method

    Attributes:
        name: Agent name
        description: Agent description
        url: Agent service endpoint URL
        version: Agent version
        protocolVersion: A2A protocol version
        provider: Provider information
        capabilities: Agent capability declaration
        skills: Agent skill list
        defaultInputModes: Default input modes
        defaultOutputModes: Default output modes
        documentationUrl: Documentation URL (optional)

    Example:
        ```json
        {
            "name": "XYZ Chat Agent",
            "description": "Intelligent conversational agent with multi-turn dialogue and task processing",
            "url": "https://agent.example.com",
            "version": "1.0.0",
            "protocolVersion": "0.3",
            "capabilities": {
                "streaming": true,
                "pushNotifications": false
            },
            "skills": [
                {
                    "id": "chat",
                    "name": "Intelligent Dialogue",
                    "description": "Conduct multi-turn intelligent dialogue"
                }
            ]
        }
        ```
    """
    name: str = Field(..., description="Agent name")
    description: str = Field(..., description="Agent description")
    url: str = Field(..., description="Agent service endpoint URL")
    version: str = Field(default="1.0.0", description="Agent version")
    protocolVersion: str = Field(default="0.3", description="A2A protocol version")
    provider: Optional[ProviderInfo] = Field(default=None, description="Provider information")
    capabilities: AgentCapabilities = Field(
        default_factory=AgentCapabilities,
        description="Agent capabilities"
    )
    skills: List[AgentSkill] = Field(default_factory=list, description="Agent skills")
    defaultInputModes: List[str] = Field(
        default_factory=lambda: ["text/plain"],
        description="Default input modes"
    )
    defaultOutputModes: List[str] = Field(
        default_factory=lambda: ["text/plain"],
        description="Default output modes"
    )
    documentationUrl: Optional[str] = Field(
        default=None,
        description="Documentation URL"
    )


# =============================================================================
# JSON-RPC 2.0 Protocol
# =============================================================================

class JSONRPCRequest(BaseModel):
    """
    JSON-RPC 2.0 request object

    The A2A protocol uses JSON-RPC 2.0 as its communication protocol.

    Attributes:
        jsonrpc: Protocol version, fixed as "2.0"
        id: Request ID (string or integer)
        method: Method name
        params: Method parameters

    Supported Methods:
        - tasks/send: Send a message and create/continue a task
        - tasks/sendSubscribe: Send a message and subscribe to streaming updates
        - tasks/get: Get task status
        - tasks/list: List tasks
        - tasks/cancel: Cancel a task
        - agentCard/get: Get Agent Card

    Example:
        ```json
        {
            "jsonrpc": "2.0",
            "id": "req-001",
            "method": "tasks/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Hello"}]
                }
            }
        }
        ```
    """
    jsonrpc: Literal["2.0"] = "2.0"
    id: Union[str, int] = Field(..., description="Request ID")
    method: str = Field(..., description="Method name")
    params: Optional[Dict[str, Any]] = Field(default=None, description="Method parameters")


class JSONRPCError(BaseModel):
    """
    JSON-RPC 2.0 error object

    Attributes:
        code: Error code
        message: Error message
        data: Additional data (optional)

    Standard Error Codes:
        -32700: Parse error
        -32600: Invalid Request
        -32601: Method not found
        -32602: Invalid params
        -32603: Internal error

    A2A Custom Error Codes:
        -32000: Task not found
        -32001: Task cancelled
        -32002: Push notification not supported
    """
    code: int = Field(..., description="Error code")
    message: str = Field(..., description="Error message")
    data: Optional[Any] = Field(default=None, description="Additional data")


class JSONRPCResponse(BaseModel):
    """
    JSON-RPC 2.0 response object

    Attributes:
        jsonrpc: Protocol version, fixed as "2.0"
        id: Corresponding request ID
        result: Result on success (mutually exclusive with error)
        error: Error on failure (mutually exclusive with result)

    Example (Success):
        ```json
        {
            "jsonrpc": "2.0",
            "id": "req-001",
            "result": {
                "id": "task-abc123",
                "status": {"state": "completed"},
                ...
            }
        }
        ```

    Example (Error):
        ```json
        {
            "jsonrpc": "2.0",
            "id": "req-001",
            "error": {
                "code": -32000,
                "message": "Task not found"
            }
        }
        ```
    """
    jsonrpc: Literal["2.0"] = "2.0"
    id: Union[str, int, None] = Field(..., description="Request ID")
    result: Optional[Any] = Field(default=None, description="Result")
    error: Optional[JSONRPCError] = Field(default=None, description="Error")

    @classmethod
    def success(cls, id: Union[str, int], result: Any) -> "JSONRPCResponse":
        """Create a success response"""
        return cls(id=id, result=result)

    @classmethod
    def error(
        cls,
        id: Union[str, int, None],
        code: int,
        message: str,
        data: Optional[Any] = None
    ) -> "JSONRPCResponse":
        """Create an error response"""
        return cls(
            id=id,
            error=JSONRPCError(code=code, message=message, data=data)
        )


# =============================================================================
# Task Send Params
# =============================================================================

class TaskSendConfiguration(BaseModel):
    """
    Task send configuration

    Parameters that control task execution.

    Attributes:
        acceptedOutputModes: Output modes supported by the client
        historyLength: Number of history messages to include
        blocking: Whether to block until completion (only for tasks/send)
        pushNotificationConfig: Push notification configuration (optional)
    """
    acceptedOutputModes: List[str] = Field(
        default_factory=lambda: ["text/plain"],
        description="Supported output modes"
    )
    historyLength: Optional[int] = Field(
        default=None,
        description="Number of history messages to include"
    )
    blocking: bool = Field(
        default=False,
        description="Whether to block until completion"
    )
    pushNotificationConfig: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Push notification configuration"
    )


class TaskSendParams(BaseModel):
    """
    tasks/send method parameters

    Attributes:
        message: Message to send
        taskId: Task ID (optional, for continuing an existing task)
        configuration: Task configuration
        metadata: Custom metadata
    """
    message: Message = Field(..., description="Message to send")
    taskId: Optional[str] = Field(
        default=None,
        description="Task ID (provided when continuing an existing task)"
    )
    configuration: Optional[TaskSendConfiguration] = Field(
        default=None,
        description="Task configuration"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Custom metadata"
    )


class TaskGetParams(BaseModel):
    """
    tasks/get method parameters

    Attributes:
        taskId: Task ID
        historyLength: Number of history messages to return (optional)
    """
    taskId: str = Field(..., description="Task ID")
    historyLength: Optional[int] = Field(
        default=None,
        description="Number of history messages to return"
    )


class TaskCancelParams(BaseModel):
    """
    tasks/cancel method parameters

    Attributes:
        taskId: ID of the task to cancel
        message: Cancellation reason (optional)
    """
    taskId: str = Field(..., description="Task ID")
    message: Optional[str] = Field(default=None, description="Cancellation reason")


# =============================================================================
# SSE Event Types (Server-Sent Events)
# =============================================================================

class TaskStatusUpdateEvent(BaseModel):
    """
    Task status update event

    Status update event pushed via SSE streaming.

    SSE Event Format:
        event: taskStatusUpdate
        data: {"taskId": "...", "status": {...}, "final": false}

    Attributes:
        taskId: Task ID
        contextId: Context ID
        status: New task status
        final: Whether this is the final status
    """
    taskId: str = Field(..., description="Task ID")
    contextId: Optional[str] = Field(default=None, description="Context ID")
    status: TaskStatus = Field(..., description="Task status")
    final: bool = Field(default=False, description="Whether this is the final status")


class TaskArtifactUpdateEvent(BaseModel):
    """
    Task artifact update event

    Artifact update event pushed via SSE streaming.

    SSE Event Format:
        event: taskArtifactUpdate
        data: {"taskId": "...", "artifact": {...}}

    Attributes:
        taskId: Task ID
        artifact: Newly added or updated artifact
        append: Whether to append to existing content (for streaming text)
    """
    taskId: str = Field(..., description="Task ID")
    artifact: A2AArtifact = Field(..., description="Artifact")
    append: bool = Field(
        default=False,
        description="Whether to append to existing content"
    )


# =============================================================================
# Error Code Constants
# =============================================================================

class A2AErrorCodes:
    """
    A2A protocol error code constants

    Contains JSON-RPC standard error codes and A2A custom error codes.
    """
    # JSON-RPC standard error codes
    PARSE_ERROR = -32700          # Parse error
    INVALID_REQUEST = -32600      # Invalid request
    METHOD_NOT_FOUND = -32601     # Method not found
    INVALID_PARAMS = -32602       # Invalid parameters
    INTERNAL_ERROR = -32603       # Internal error

    # A2A custom error codes
    TASK_NOT_FOUND = -32000       # Task not found
    TASK_CANCELLED = -32001       # Task cancelled
    PUSH_NOT_SUPPORTED = -32002   # Push notification not supported
    UNSUPPORTED_OPERATION = -32003  # Unsupported operation
    CONTENT_TYPE_NOT_SUPPORTED = -32004  # Content type not supported
    AGENT_UNAVAILABLE = -32005    # Agent unavailable


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Enums
    "TaskState",
    "MessageRole",

    # Message Parts
    "TextPart",
    "FilePart",
    "DataPart",
    "Part",

    # Core Objects
    "Message",
    "TaskStatus",
    "A2AArtifact",
    "Task",

    # Agent Card
    "ProviderInfo",
    "AgentCapabilities",
    "AgentSkill",
    "AgentCard",

    # JSON-RPC
    "JSONRPCRequest",
    "JSONRPCError",
    "JSONRPCResponse",

    # Method Parameters
    "TaskSendConfiguration",
    "TaskSendParams",
    "TaskGetParams",
    "TaskCancelParams",

    # SSE Events
    "TaskStatusUpdateEvent",
    "TaskArtifactUpdateEvent",

    # Error Codes
    "A2AErrorCodes",
]
