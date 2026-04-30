"""
@file_name: chat_trigger.py
@author: NetMind.AI
@date: 2025-11-25
@description: A2A protocol compatible Chat Trigger API

This module implements an HTTP API service compliant with Google A2A (Agent-to-Agent) protocol.
Through this service, external Agents or clients can interact with AgentRuntime in a standardized way.

A2A protocol version: 0.3
Specification document: https://a2a-protocol.org/latest/specification/

=============================================================================
Protocol Overview
=============================================================================

A2A protocol is an open standard led by Google, designed to enable interoperability between different AI Agents.
Key features:
- Uses JSON-RPC 2.0 as the communication protocol
- Supports synchronous requests, SSE streaming responses, and Webhook push
- Service discovery through Agent Card
- Task as the core abstraction, managing the complete lifecycle of Agent interactions

=============================================================================
Supported Endpoints
=============================================================================

1. Agent Card (Service Discovery)
   - GET  /.well-known/agent.json    Get Agent Card (static)
   - POST /  method=agentCard/get    Get Agent Card (JSON-RPC)

2. Task Management
   - POST /  method=tasks/send           Send message, synchronously wait for response
   - POST /  method=tasks/sendSubscribe  Send message, subscribe to SSE stream
   - POST /  method=tasks/get            Get task status
   - POST /  method=tasks/cancel         Cancel task

3. Health Check
   - GET  /health                        Health check endpoint

=============================================================================
Usage Examples
=============================================================================

1. Get Agent Card:
   ```bash
   curl http://localhost:8000/.well-known/agent.json
   ```

2. Send message (synchronous):
   ```bash
   curl -X POST http://localhost:8000 \\
     -H "Content-Type: application/json" \\
     -d '{
       "jsonrpc": "2.0",
       "id": "1",
       "method": "tasks/send",
       "params": {
         "message": {
           "role": "user",
           "parts": [{"type": "text", "text": "Hello"}]
         },
         "configuration": {
           "blocking": true
         }
       }
     }'
   ```

3. Send message (streaming):
   ```bash
   curl -X POST http://localhost:8000 \\
     -H "Content-Type: application/json" \\
     -H "Accept: text/event-stream" \\
     -d '{
       "jsonrpc": "2.0",
       "id": "1",
       "method": "tasks/sendSubscribe",
       "params": {
         "message": {
           "role": "user",
           "parts": [{"type": "text", "text": "Please introduce yourself in detail"}]
         }
       }
     }'
   ```
"""

from typing import Dict, Any, Optional
import json
import uuid

from xyz_agent_context.utils import utc_now

# FastAPI
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from loguru import logger

# A2A Schema
from xyz_agent_context.schema import (
    # Enums
    TaskState,
    MessageRole,
    # Message Parts
    TextPart,
    # Core Objects
    A2AMessage,
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
    JSONRPCResponse,
    # Method Params
    TaskSendParams,
    TaskGetParams,
    TaskCancelParams,
    # SSE Events
    TaskStatusUpdateEvent,
    TaskArtifactUpdateEvent,
    # Error Codes
    A2AErrorCodes,
)

# Agent Runtime
from xyz_agent_context.agent_runtime import AgentRuntime

# Utils
from xyz_agent_context.utils import DatabaseClient, get_db_client_sync


# =============================================================================
# A2A Server Class
# =============================================================================

class A2AServer:
    """
    A2A Protocol Server

    Implements the complete HTTP service per A2A protocol specification, including:
    - JSON-RPC 2.0 endpoint handling
    - Agent Card service discovery
    - Task lifecycle management
    - SSE streaming response support

    Architecture:
    -------------
    ┌─────────────────────────────────────────────────────────────────┐
    │                        A2AServer                                │
    │  ┌───────────────┐  ┌──────────────┐  ┌───────────────────┐   │
    │  │  Agent Card   │  │  Task Store  │  │  Method Handlers  │   │
    │  │  (Discovery)  │  │  (Storage)   │  │  (Dispatchers)    │   │
    │  └───────────────┘  └──────────────┘  └───────────────────┘   │
    │           │                │                    │              │
    │           └────────────────┼────────────────────┘              │
    │                            │                                   │
    │                     ┌──────▼──────┐                           │
    │                     │   FastAPI   │                           │
    │                     │   Router    │                           │
    │                     └─────────────┘                           │
    └─────────────────────────────────────────────────────────────────┘
                                │
                         HTTP Requests
                                │
    ┌───────────────────────────▼───────────────────────────────────┐
    │                     AgentRuntime                               │
    │            (Actual Agent execution engine)                     │
    └───────────────────────────────────────────────────────────────┘

    Attributes:
        host: Server host address to bind
        port: Server port to bind
        agent_card: Agent Card configuration
        tasks: Task storage (task_id -> Task)
        db: Database client
        app: FastAPI application instance
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8000,
        agent_name: str = "XYZ Agent",
        agent_description: str = "XYZ Agent Context - Intelligent Conversational Agent",
        database_client: Optional[DatabaseClient] = None
    ):
        """
        Initialize A2A Server

        Args:
            host: Server host address, default "0.0.0.0" (listen on all interfaces)
            port: Server port, default 8000
            agent_name: Agent name, displayed in Agent Card
            agent_description: Agent description, displayed in Agent Card
            database_client: Database client for persistent storage
        """
        self.host = host
        self.port = port
        self.db = database_client or get_db_client_sync()

        # Initialize Agent Card
        self.agent_card = self._create_agent_card(agent_name, agent_description)

        # Task storage (in-memory; production should use persistent storage)
        # key: task_id, value: Task
        self.tasks: Dict[str, Task] = {}

        # Create FastAPI application
        self.app = self._create_app()

        logger.info(f"A2AServer initialized: {agent_name}")

    # =========================================================================
    # Agent Card Configuration
    # =========================================================================

    def _create_agent_card(self, name: str, description: str) -> AgentCard:
        """
        Create Agent Card

        Agent Card is the A2A protocol's service discovery mechanism, describing the Agent's capabilities and connection information.
        Clients should first obtain the Agent Card to understand its capabilities before interacting with the Agent.

        Args:
            name: Agent name
            description: Agent description

        Returns:
            AgentCard: Complete Agent Card configuration
        """
        return AgentCard(
            name=name,
            description=description,
            url=f"http://{self.host}:{self.port}",
            version="1.0.0",
            protocolVersion="0.3",
            provider=ProviderInfo(
                organization="XYZ Agent Context",
                url="https://github.com/NetMindAI-Open/NarraNexus"
            ),
            capabilities=AgentCapabilities(
                streaming=True,           # Support SSE streaming responses
                pushNotifications=False,  # Webhook push not supported yet
                stateTransitionHistory=True  # Support state transition history
            ),
            skills=[
                AgentSkill(
                    id="chat",
                    name="Intelligent Chat",
                    description="Multi-turn intelligent conversation with context memory and personalized responses",
                    tags=["chat", "conversation", "dialogue"],
                    examples=[
                        "Hello, please introduce yourself",
                        "Help me analyze this problem",
                        "Summarize our conversation"
                    ],
                    inputModes=["text/plain", "application/json"],
                    outputModes=["text/plain", "application/json"]
                ),
                AgentSkill(
                    id="task",
                    name="Task Processing",
                    description="Execute complex tasks with tool calling and multi-step reasoning",
                    tags=["task", "tool", "reasoning"],
                    examples=[
                        "Help me search for relevant information",
                        "Analyze this data",
                        "Execute this operation"
                    ],
                    inputModes=["text/plain", "application/json"],
                    outputModes=["text/plain", "application/json"]
                )
            ],
            defaultInputModes=["text/plain"],
            defaultOutputModes=["text/plain"],
            documentationUrl="https://github.com/NetMindAI-Open/NarraNexus"
        )

    # =========================================================================
    # FastAPI Application Configuration
    # =========================================================================

    def _create_app(self) -> FastAPI:
        """
        Create and configure FastAPI application

        Configuration includes:
        - CORS middleware (allow cross-origin requests)
        - Route registration (health check, Agent Card, JSON-RPC)
        - Error handling

        Returns:
            FastAPI: Fully configured FastAPI application instance
        """
        app = FastAPI(
            title="A2A Chat Agent",
            description="A2A protocol compatible intelligent conversational agent",
            version="1.0.0",
            docs_url="/docs",      # Swagger UI
            redoc_url="/redoc"     # ReDoc
        )

        # CORS middleware configuration
        # Note: Production should restrict allow_origins
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Register routes
        self._register_routes(app)

        return app

    def _register_routes(self, app: FastAPI) -> None:
        """
        Register API routes

        Route structure:
        - GET  /health                    Health check
        - GET  /.well-known/agent.json    Agent Card (static)
        - POST /                          JSON-RPC endpoint (all A2A methods)

        Args:
            app: FastAPI application instance
        """

        # ---------------------------------------------------------------------
        # Health check endpoint
        # ---------------------------------------------------------------------
        @app.get("/health")
        async def health():
            """
            Health check

            Returns:
                Service status information
            """
            return {
                "status": "healthy",
                "service": "A2A Chat Agent",
                "version": "1.0.0",
                "protocol": "A2A/0.3",
                "timestamp": utc_now().isoformat()
            }

        # ---------------------------------------------------------------------
        # Agent Card endpoint (static file method)
        # ---------------------------------------------------------------------
        @app.get("/.well-known/agent.json")
        async def get_agent_card_static():
            """
            Get Agent Card (static endpoint)

            This is the A2A protocol's recommended service discovery method.
            Clients should first access this endpoint to understand the Agent's capabilities.

            Returns:
                AgentCard: Agent's metadata description
            """
            return self.agent_card.model_dump()

        # ---------------------------------------------------------------------
        # JSON-RPC 2.0 endpoint
        # ---------------------------------------------------------------------
        @app.post("/")
        async def jsonrpc_handler(request: Request):
            """
            JSON-RPC 2.0 main processing endpoint

            All A2A protocol methods are handled through this endpoint:
            - agentCard/get: Get Agent Card
            - tasks/send: Send message (synchronous)
            - tasks/sendSubscribe: Send message (streaming)
            - tasks/get: Get task status
            - tasks/cancel: Cancel task

            Request Format:
                ```json
                {
                    "jsonrpc": "2.0",
                    "id": "request-id",
                    "method": "tasks/send",
                    "params": {...}
                }
                ```

            Response Format (Success):
                ```json
                {
                    "jsonrpc": "2.0",
                    "id": "request-id",
                    "result": {...}
                }
                ```

            Response Format (Error):
                ```json
                {
                    "jsonrpc": "2.0",
                    "id": "request-id",
                    "error": {
                        "code": -32600,
                        "message": "Invalid Request"
                    }
                }
                ```
            """
            try:
                # Parse request body
                body = await request.json()
                logger.debug(f"JSON-RPC Request: {json.dumps(body, ensure_ascii=False)[:500]}")

                # Validate JSON-RPC format
                if not isinstance(body, dict):
                    return self._error_response(
                        None,
                        A2AErrorCodes.INVALID_REQUEST,
                        "Request must be a JSON object"
                    )

                # Parse JSON-RPC request
                rpc_request = JSONRPCRequest(**body)

                # Route to corresponding method handler
                return await self._dispatch_method(rpc_request, request)

            except json.JSONDecodeError:
                return self._error_response(
                    None,
                    A2AErrorCodes.PARSE_ERROR,
                    "Invalid JSON"
                )
            except Exception as e:
                logger.exception(f"JSON-RPC handler error: {e}")
                return self._error_response(
                    body.get("id") if isinstance(body, dict) else None,
                    A2AErrorCodes.INTERNAL_ERROR,
                    str(e)
                )

    # =========================================================================
    # JSON-RPC Method Dispatch
    # =========================================================================

    async def _dispatch_method(
        self,
        rpc_request: JSONRPCRequest,
        http_request: Request
    ) -> JSONResponse:
        """
        Dispatch JSON-RPC request to corresponding method handler

        Supported methods:
        - agentCard/get: Get Agent Card
        - tasks/send: Send message (synchronous mode)
        - tasks/sendSubscribe: Send message (SSE streaming mode)
        - tasks/get: Get task status and history
        - tasks/cancel: Cancel an executing task

        Args:
            rpc_request: Parsed JSON-RPC request
            http_request: Raw HTTP request (used to determine if SSE is needed)

        Returns:
            JSONResponse or EventSourceResponse (streaming)
        """
        method = rpc_request.method
        params = rpc_request.params or {}
        request_id = rpc_request.id

        logger.info(f"Dispatching method: {method}")

        # Method routing table
        method_handlers = {
            "agentCard/get": self._handle_agent_card_get,
            "tasks/send": self._handle_tasks_send,
            "tasks/sendSubscribe": self._handle_tasks_send_subscribe,
            "tasks/get": self._handle_tasks_get,
            "tasks/cancel": self._handle_tasks_cancel,
        }

        handler = method_handlers.get(method)
        if handler is None:
            return self._error_response(
                request_id,
                A2AErrorCodes.METHOD_NOT_FOUND,
                f"Method not found: {method}"
            )

        try:
            # Streaming method needs special handling
            if method == "tasks/sendSubscribe":
                return await handler(request_id, params, http_request)
            else:
                result = await handler(request_id, params)
                return self._success_response(request_id, result)
        except Exception as e:
            logger.exception(f"Method {method} error: {e}")
            return self._error_response(
                request_id,
                A2AErrorCodes.INTERNAL_ERROR,
                str(e)
            )

    # =========================================================================
    # Method Handlers
    # =========================================================================

    async def _handle_agent_card_get(
        self,
        request_id: str,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle agentCard/get method

        Return Agent Card for service discovery.

        Args:
            request_id: JSON-RPC request ID
            params: Method parameters (this method requires no parameters)

        Returns:
            Agent Card dictionary
        """
        return self.agent_card.model_dump()

    async def _handle_tasks_send(
        self,
        request_id: str,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle tasks/send method (synchronous mode)

        Create or continue a task, wait for execution to complete and return results.
        Suitable for simple tasks with quick responses.

        Flow:
        1. Parse parameters, create/get Task
        2. Extract user message
        3. Call AgentRuntime for execution
        4. Collect complete response
        5. Update Task status
        6. Return Task

        Args:
            request_id: JSON-RPC request ID
            params: Method parameters, containing message, taskId, configuration, etc.

        Returns:
            Task object dictionary

        Raises:
            ValueError: Parameter validation failed
        """
        # Parse parameters
        send_params = TaskSendParams(**params)
        message = send_params.message
        task_id = send_params.taskId

        # Create or get Task
        if task_id and task_id in self.tasks:
            task = self.tasks[task_id]
            logger.info(f"Continuing existing task: {task_id}")
        else:
            task = Task(
                contextId=message.contextId or f"ctx-{uuid.uuid4().hex[:12]}"
            )
            self.tasks[task.id] = task
            logger.info(f"Created new task: {task.id}")

        # Add user message to history
        task.add_message(message)

        # Update status to working
        task.update_status(TaskState.WORKING)

        # Extract user input text
        user_input = self._extract_text_from_message(message)

        # Extract agent_id and user_id from message
        metadata = message.metadata or {}
        agent_id = metadata.get("agent_id", "default_agent")
        user_id = metadata.get("user_id", "default_user")

        # Execute Agent
        try:
            from xyz_agent_context.agent_runtime.run_collector import collect_run
            from xyz_agent_context.schema.hook_schema import WorkingSource

            agent_runtime = AgentRuntime()
            collection = await collect_run(
                agent_runtime,
                agent_id=agent_id,
                user_id=user_id,
                input_content=user_input,
                working_source=WorkingSource.CHAT,
                trigger_extra_data={"trigger_id": f"a2a_{task.id}"},
            )

            # Error path (Bug 2): previously this trigger collected on
            # `if hasattr(response, 'delta')` which meant ErrorMessage
            # (no `delta` attribute) was silently skipped. The A2A task
            # would then finish with an empty completion, masking the
            # failure to the caller. Now we surface the error as a FAILED
            # task with the underlying message intact.
            if collection.is_error:
                logger.warning(
                    f"A2A task {task.id} runtime error: "
                    f"{collection.error.error_type}: {collection.error.error_message}"
                )
                error_message = A2AMessage.create_agent_message(
                    text=(
                        f"Execution error ({collection.error.error_type}): "
                        f"{collection.error.error_message}"
                    ),
                    task_id=task.id,
                )
                task.update_status(TaskState.FAILED, message=error_message)
                return task.model_dump()

            final_output = collection.output_text

            # Create Agent response message
            agent_message = A2AMessage.create_agent_message(
                text=final_output,
                task_id=task.id,
            )
            task.add_message(agent_message)

            # Create artifact
            artifact = Artifact(
                name="response",
                description="Agent response",
                parts=[TextPart(text=final_output)],
            )
            task.add_artifact(artifact)

            # Update status to completed
            task.update_status(TaskState.COMPLETED, message=agent_message)

        except Exception as e:
            logger.exception(f"Agent execution error: {e}")
            error_message = A2AMessage.create_agent_message(
                text=f"Execution error: {str(e)}",
                task_id=task.id,
            )
            task.update_status(TaskState.FAILED, message=error_message)

        return task.model_dump()

    async def _handle_tasks_send_subscribe(
        self,
        request_id: str,
        params: Dict[str, Any],
        http_request: Request
    ) -> EventSourceResponse:
        """
        Handle tasks/sendSubscribe method (SSE streaming mode)

        Create a task and stream the execution process via Server-Sent Events.
        Suitable for long-running tasks or scenarios requiring real-time feedback.

        SSE Event Types:
        - task: Initial task information
        - taskStatusUpdate: Task status updates
        - taskArtifactUpdate: Artifact updates (containing text stream)
        - done: Task completed

        Flow:
        1. Create Task, send initial status
        2. Stream AgentRuntime execution
        3. Send real-time status and artifact updates
        4. Send completion event

        Args:
            request_id: JSON-RPC request ID
            params: Method parameters
            http_request: HTTP request object

        Returns:
            EventSourceResponse: SSE event stream
        """
        # Parse parameters
        send_params = TaskSendParams(**params)
        message = send_params.message

        # Create Task
        task = Task(
            contextId=message.contextId or f"ctx-{uuid.uuid4().hex[:12]}"
        )
        self.tasks[task.id] = task
        task.add_message(message)

        # Extract parameters
        user_input = self._extract_text_from_message(message)
        metadata = message.metadata or {}
        agent_id = metadata.get("agent_id", "default_agent")
        user_id = metadata.get("user_id", "default_user")

        async def event_generator():
            """SSE event generator"""
            nonlocal task

            try:
                # Send initial Task status
                task.update_status(TaskState.SUBMITTED)
                yield {
                    "event": "task",
                    "data": json.dumps(task.model_dump(), ensure_ascii=False, default=str)
                }

                # Update to working status
                task.update_status(TaskState.WORKING)
                status_event = TaskStatusUpdateEvent(
                    taskId=task.id,
                    contextId=task.contextId,
                    status=task.status,
                    final=False
                )
                yield {
                    "event": "taskStatusUpdate",
                    "data": json.dumps(status_event.model_dump(), ensure_ascii=False, default=str)
                }

                # Execute Agent
                agent_runtime = AgentRuntime()
                final_output = ""
                artifact_id = f"artifact-{uuid.uuid4().hex[:8]}"

                async for response in agent_runtime.run(
                    agent_id=agent_id,
                    user_id=user_id,
                    input_content=user_input,
                    trigger_extra_data={"trigger_id": f"a2a_sse_{task.id}"},
                ):
                    # Process text increments
                    if hasattr(response, 'delta'):
                        delta = response.delta
                        final_output += delta

                        # Send artifact update (incremental text)
                        artifact = Artifact(
                            artifactId=artifact_id,
                            name="response",
                            parts=[TextPart(text=delta)]
                        )
                        artifact_event = TaskArtifactUpdateEvent(
                            taskId=task.id,
                            artifact=artifact,
                            append=True  # Append mode
                        )
                        yield {
                            "event": "taskArtifactUpdate",
                            "data": json.dumps(
                                artifact_event.model_dump(),
                                ensure_ascii=False,
                                default=str
                            )
                        }

                    # Process progress messages
                    elif hasattr(response, 'step'):
                        # ProgressMessage - convert to status update
                        progress_message = A2AMessage(
                            role=MessageRole.AGENT,
                            parts=[TextPart(text=f"[{response.step}] {response.title}")]
                        )
                        status_event = TaskStatusUpdateEvent(
                            taskId=task.id,
                            contextId=task.contextId,
                            status=TaskStatus(
                                state=TaskState.WORKING,
                                message=progress_message
                            ),
                            final=False
                        )
                        yield {
                            "event": "taskStatusUpdate",
                            "data": json.dumps(
                                status_event.model_dump(),
                                ensure_ascii=False,
                                default=str
                            )
                        }

                # Create final response message
                agent_message = A2AMessage.create_agent_message(
                    text=final_output,
                    task_id=task.id
                )
                task.add_message(agent_message)

                # Create final artifact
                final_artifact = Artifact(
                    artifactId=artifact_id,
                    name="response",
                    description="Agent complete response",
                    parts=[TextPart(text=final_output)]
                )
                task.add_artifact(final_artifact)

                # Update to completed status
                task.update_status(
                    TaskState.COMPLETED,
                    message=agent_message
                )

                # Send final status update
                final_status_event = TaskStatusUpdateEvent(
                    taskId=task.id,
                    contextId=task.contextId,
                    status=task.status,
                    final=True
                )
                yield {
                    "event": "taskStatusUpdate",
                    "data": json.dumps(
                        final_status_event.model_dump(),
                        ensure_ascii=False,
                        default=str
                    )
                }

                # Send completion event
                yield {
                    "event": "done",
                    "data": json.dumps({
                        "taskId": task.id,
                        "status": "completed"
                    })
                }

            except Exception as e:
                logger.exception(f"Stream execution error: {e}")

                # Update to failed status
                error_message = A2AMessage.create_agent_message(
                    text=f"Execution error: {str(e)}",
                    task_id=task.id
                )
                task.update_status(TaskState.FAILED, message=error_message)

                # Send error event
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "taskId": task.id,
                        "error": str(e)
                    })
                }

        return EventSourceResponse(event_generator())

    async def _handle_tasks_get(
        self,
        request_id: str,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle tasks/get method

        Get the task's current status, history, and artifacts.

        Args:
            request_id: JSON-RPC request ID
            params: Method parameters, containing taskId

        Returns:
            Task object dictionary

        Raises:
            HTTPException: Raised when task does not exist
        """
        get_params = TaskGetParams(**params)
        task_id = get_params.taskId

        if task_id not in self.tasks:
            raise HTTPException(
                status_code=404,
                detail=f"Task not found: {task_id}"
            )

        task = self.tasks[task_id]

        # If historyLength is specified, truncate history
        if get_params.historyLength is not None:
            task_dict = task.model_dump()
            task_dict["history"] = task_dict["history"][-get_params.historyLength:]
            return task_dict

        return task.model_dump()

    async def _handle_tasks_cancel(
        self,
        request_id: str,
        params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle tasks/cancel method

        Cancel an executing task.
        Note: Completed or cancelled tasks cannot be cancelled again.

        Args:
            request_id: JSON-RPC request ID
            params: Method parameters, containing taskId

        Returns:
            Task object dictionary

        Raises:
            HTTPException: Task does not exist or status does not allow cancellation
        """
        cancel_params = TaskCancelParams(**params)
        task_id = cancel_params.taskId

        if task_id not in self.tasks:
            raise HTTPException(
                status_code=404,
                detail=f"Task not found: {task_id}"
            )

        task = self.tasks[task_id]

        # Check task status
        if task.status.state in [TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel task in state: {task.status.state}"
            )

        # Update to cancelled status
        cancel_message = A2AMessage.create_agent_message(
            text=cancel_params.message or "Task cancelled by user",
            task_id=task_id
        )
        task.update_status(TaskState.CANCELLED, message=cancel_message)

        return task.model_dump()

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _extract_text_from_message(self, message: A2AMessage) -> str:
        """
        Extract text content from A2A Message

        Iterates through all parts of the message, extracting TextPart content and concatenating.

        Args:
            message: A2A message object

        Returns:
            Extracted text content
        """
        texts = []
        for part in message.parts:
            if isinstance(part, TextPart):
                texts.append(part.text)
            elif isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
        return "\n".join(texts)

    def _success_response(
        self,
        request_id: str,
        result: Any
    ) -> JSONResponse:
        """
        Create a successful JSON-RPC response

        Args:
            request_id: Request ID
            result: Result data

        Returns:
            JSON response
        """
        response = JSONRPCResponse.success(request_id, result)
        return JSONResponse(
            content=response.model_dump(),
            media_type="application/json"
        )

    def _error_response(
        self,
        request_id: Optional[str],
        code: int,
        message: str,
        data: Optional[Any] = None
    ) -> JSONResponse:
        """
        Create an error JSON-RPC response

        Args:
            request_id: Request ID (may be None)
            code: Error code
            message: Error message
            data: Additional data

        Returns:
            JSON response
        """
        response = JSONRPCResponse.error(request_id, code, message, data)
        return JSONResponse(
            content=response.model_dump(),
            media_type="application/json"
        )

    # =========================================================================
    # Server Startup
    # =========================================================================

    def run(self) -> None:
        """
        Start the A2A server

        Uses uvicorn as the ASGI server to run the FastAPI application.
        """
        import uvicorn

        logger.info("Starting A2A Protocol Server")
        logger.info(f"   Agent: {self.agent_card.name}")
        logger.info(f"   Version: {self.agent_card.version}")
        logger.info(f"   Protocol: A2A/{self.agent_card.protocolVersion}")
        logger.info(f"   Host: {self.host}")
        logger.info(f"   Port: {self.port}")
        logger.info("")
        logger.info("📡 Endpoints:")
        logger.info("   GET  /.well-known/agent.json  Agent Card")
        logger.info("   POST /                        JSON-RPC 2.0")
        logger.info("   GET  /health                  Health Check")
        logger.info("   GET  /docs                    Swagger UI")
        logger.info("")
        logger.info("Supported Methods:")
        logger.info("   - agentCard/get")
        logger.info("   - tasks/send")
        logger.info("   - tasks/sendSubscribe")
        logger.info("   - tasks/get")
        logger.info("   - tasks/cancel")
        uvicorn.run(self.app, host=self.host, port=self.port)


# =============================================================================
# Standalone Entry Point
# =============================================================================

def run_a2a_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    agent_name: str = "XYZ Agent",
    agent_description: str = "XYZ Agent Context - Intelligent Conversational Agent"
):
    """
    Run the A2A protocol server

    This is a convenience function for starting the service.

    Args:
        host: Host address
        port: Port number
        agent_name: Agent name
        agent_description: Agent description

    Example:
        ```python
        from xyz_agent_context.module.chat_module.chat_trigger import run_a2a_server
        run_a2a_server(port=8080)
        ```
    """
    server = A2AServer(
        host=host,
        port=port,
        agent_name=agent_name,
        agent_description=agent_description
    )
    server.run()


if __name__ == "__main__":
    import sys

    # Parse command line arguments
    host = "0.0.0.0"
    port = 8000

    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    if len(sys.argv) > 2:
        host = sys.argv[2]

    run_a2a_server(host=host, port=port)
