"""
@file_name: websocket.py
@author: NetMind.AI
@date: 2025-11-28
@description: WebSocket endpoint for agent runtime streaming

Provides real-time streaming of agent execution via WebSocket.
Messages are streamed as JSON objects following the RuntimeMessage schema.

Protocol:
1. Client connects to /ws/agent/run
2. Client sends JSON: {"agent_id": "...", "user_id": "...", "input_content": "..."}
3. Server streams RuntimeMessage objects as JSON
4. Client may send {"action": "stop"} at any time to cancel the run
5. Connection closes when execution completes or is cancelled

Message Types:
- progress: Step-by-step execution progress
- agent_response: Text output from the agent
- agent_thinking: Agent's thinking process
- tool_call: Tool/function calls
- cancelled: Sent when user cancels the run
- error: Error messages
"""

import asyncio
import traceback
from typing import Optional
import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from loguru import logger

from backend.config import settings
from backend.auth import _is_cloud_mode, decode_token

from xyz_agent_context.agent_runtime import AgentRuntime
from xyz_agent_context.agent_runtime.cancellation import CancellationToken, CancelledByUser
from xyz_agent_context.schema import WorkingSource
from xyz_agent_context.repository import MCPRepository
from xyz_agent_context.utils.db_factory import get_db_client


router = APIRouter()

# WebSocket close codes (RFC 6455 + application-specific)
WS_CLOSE_POLICY_VIOLATION = 1008  # auth failure / policy violation


class AgentRunRequest(BaseModel):
    """WebSocket request payload for running an agent"""
    agent_id: str
    user_id: str
    input_content: str
    working_source: Optional[str] = "chat"
    # JWT token — required in cloud mode, ignored in local mode.
    # Sent in the first WS message because browser WebSocket API cannot
    # set arbitrary Authorization headers.
    token: Optional[str] = None


async def _listen_for_stop(websocket: WebSocket, cancellation: CancellationToken) -> None:
    """
    Background listener: watches for a stop signal from the client.

    Runs concurrently with the agent loop. When the client sends
    {"action": "stop"}, triggers the cancellation token which
    propagates through the entire execution pipeline.
    """
    try:
        while not cancellation.is_cancelled:
            data = await websocket.receive_json()
            if isinstance(data, dict) and data.get("action") == "stop":
                cancellation.cancel("User clicked stop")
                return
    except WebSocketDisconnect:
        # Client disconnected — treat as implicit cancellation
        cancellation.cancel("Client disconnected")
    except Exception:
        # Any receive error (connection reset, etc.)
        pass


@router.websocket("/ws/agent/run")
async def websocket_agent_run(websocket: WebSocket):
    """
    WebSocket endpoint for streaming agent execution.

    Uses a dual-task pattern:
    - Task A: runs the agent pipeline and streams messages to the client
    - Task B: listens for stop signals from the client

    Both tasks share a CancellationToken. When the client sends stop,
    Task B triggers the token, which causes Task A to exit gracefully.
    """
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    try:
        # Receive and parse request
        request_data = await websocket.receive_json()
        logger.info(f"Received request: {request_data}")

        try:
            request = AgentRunRequest(**request_data)
        except ValidationError as e:
            logger.error(f"Invalid request: {e}")
            await websocket.send_json({
                "type": "error",
                "error_message": f"Invalid request format: {str(e)}",
                "error_type": "ValidationError",
            })
            await websocket.close()
            return

        # ---- Cloud-mode JWT authentication ----
        #
        # The FastAPI auth middleware skips /ws/* paths because browser
        # WebSocket API cannot set Authorization headers. Instead, the
        # client sends the JWT in the first message payload. We validate
        # it here and refuse the connection if it is missing/invalid/expired.
        #
        # The token's user_id is authoritative — we reject any request
        # whose payload user_id does not match the token's user_id, to
        # prevent one authenticated user from running agents as another.
        #
        # Local mode (SQLite) bypasses this check entirely.
        if _is_cloud_mode():
            if not request.token:
                logger.warning("WS auth failed: missing token in cloud mode")
                await websocket.send_json({
                    "type": "error",
                    "error_message": "Authentication required",
                    "error_type": "AuthError",
                })
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
                return
            try:
                payload = decode_token(request.token)
            except jwt.ExpiredSignatureError:
                logger.warning("WS auth failed: token expired")
                await websocket.send_json({
                    "type": "error",
                    "error_message": "Token expired",
                    "error_type": "AuthError",
                })
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
                return
            except jwt.InvalidTokenError as e:
                logger.warning(f"WS auth failed: invalid token ({e})")
                await websocket.send_json({
                    "type": "error",
                    "error_message": "Invalid token",
                    "error_type": "AuthError",
                })
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
                return

            token_user_id = payload.get("user_id")
            if not token_user_id:
                logger.warning("WS auth failed: token missing user_id claim")
                await websocket.send_json({
                    "type": "error",
                    "error_message": "Invalid token claims",
                    "error_type": "AuthError",
                })
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
                return

            if token_user_id != request.user_id:
                logger.warning(
                    f"WS auth failed: user_id mismatch — token={token_user_id}, "
                    f"payload={request.user_id}"
                )
                await websocket.send_json({
                    "type": "error",
                    "error_message": "User ID does not match token",
                    "error_type": "AuthError",
                })
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
                return

            logger.info(f"WS auth OK: user_id={token_user_id}, role={payload.get('role')}")

        # Convert working_source string to enum
        working_source = WorkingSource(request.working_source)

        logger.info(f"Starting agent runtime: agent_id={request.agent_id}, user_id={request.user_id}")

        # ---- Dashboard v2 (TDR-2): register active session AFTER auth passes, ----
        # ---- BEFORE any MCP/runtime setup that can throw. The enclosing try/finally
        # ---- below guarantees removal on every exit path.
        # ---- NOTE: logging discipline — never print SessionInfo fields user_id /
        # ---- user_display / channel (PII). Only session_id + agent_id are log-safe.
        import uuid as _uuid
        from datetime import datetime as _datetime, timezone as _timezone
        from backend.state.active_sessions import get_session_registry as _get_registry, SessionInfo as _SessionInfo

        _session_id = str(_uuid.uuid4())
        _channel = request.working_source or "web"
        _registry = _get_registry()
        await _registry.add(
            request.agent_id,
            _SessionInfo(
                session_id=_session_id,
                user_id=request.user_id,
                user_display=request.user_id,  # refine via channel_tag.sender_name when available
                channel=_channel,
                started_at=_datetime.now(_timezone.utc).isoformat(),
            ),
        )

        # Load MCP URLs from database for this agent+user
        mcp_urls = {}
        try:
            db_client = await get_db_client()
            mcp_repo = MCPRepository(db_client)
            mcps = await mcp_repo.get_mcps_by_agent_user(
                agent_id=request.agent_id,
                user_id=request.user_id,
                is_enabled=True
            )
            for mcp in mcps:
                mcp_urls[mcp.name] = mcp.url
            if mcp_urls:
                logger.info(f"Loaded {len(mcp_urls)} MCP servers: {list(mcp_urls.keys())}")
        except Exception as e:
            logger.warning(f"Failed to load MCP URLs: {e}")

        # ---- Shared cancellation token ----
        cancellation = CancellationToken()

        # ---- Heartbeat task ----
        heartbeat_stop = asyncio.Event()

        async def heartbeat_loop():
            while not heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(heartbeat_stop.wait(), timeout=settings.ws_heartbeat_interval)
                    break
                except asyncio.TimeoutError:
                    try:
                        await websocket.send_json({"type": "heartbeat"})
                    except Exception:
                        break

        heartbeat_task = asyncio.create_task(heartbeat_loop())

        # ---- Start stop listener (Task B) ----
        stop_listener = asyncio.create_task(_listen_for_stop(websocket, cancellation))

        import time as _time
        _ws_start = _time.monotonic()
        _step3_end: float = 0  # will be set when last agent_response arrives

        try:
            async with AgentRuntime() as runtime:
                async for message in runtime.run(
                    agent_id=request.agent_id,
                    user_id=request.user_id,
                    input_content=request.input_content,
                    working_source=working_source,
                    pass_mcp_urls=mcp_urls,
                    cancellation=cancellation,
                ):
                    # Convert message to dict and send
                    if hasattr(message, 'to_dict'):
                        message_dict = message.to_dict()
                    elif hasattr(message, 'model_dump'):
                        message_dict = message.model_dump(mode='json')
                    elif isinstance(message, dict):
                        message_dict = message
                    else:
                        message_dict = {"type": "unknown", "data": str(message)}
                    try:
                        await websocket.send_json(message_dict)
                    except RuntimeError:
                        logger.info("WebSocket closed during streaming, stopping send loop")
                        break

                    # Verbose logging
                    msg_type = message_dict.get('type', '?')
                    if msg_type == 'agent_response':
                        _step3_end = _time.monotonic()  # track last streaming token time
                        preview = message_dict.get('delta', '')[:80]
                        logger.info(f"  📤 WS [{msg_type}] delta='{preview}'")
                    elif msg_type == 'agent_thinking':
                        preview = message_dict.get('thinking_content', '')[:80]
                        logger.info(f"  📤 WS [{msg_type}] thinking='{preview}'")
                    elif msg_type == 'progress':
                        step = message_dict.get('step', '?')
                        desc = message_dict.get('description', '')[:80]
                        tool = message_dict.get('details', {}).get('tool_name', '') if isinstance(message_dict.get('details'), dict) else ''
                        logger.info(f"  📤 WS [{msg_type}] step={step} tool={tool} desc='{desc}'")
                    else:
                        logger.info(f"  📤 WS [{msg_type}] {str(message_dict)[:120]}")

        except CancelledByUser as e:
            logger.info(f"Agent run cancelled: {e.reason}")
            try:
                await websocket.send_json({
                    "type": "cancelled",
                    "message": f"Agent run stopped: {e.reason}",
                })
            except RuntimeError:
                pass

        finally:
            heartbeat_stop.set()
            stop_listener.cancel()
            # Suppress CancelledError from the stop listener
            try:
                await stop_listener
            except asyncio.CancelledError:
                pass
            await heartbeat_task

        _ws_end = _time.monotonic()
        _total = _ws_end - _ws_start
        _post_stream = (_ws_end - _step3_end) if _step3_end else 0
        logger.info(
            f"Agent execution completed — total={_total:.1f}s, "
            f"post-stream (step 4)={_post_stream:.1f}s"
        )

        # Send completion signal if not cancelled
        if not cancellation.is_cancelled:
            try:
                await websocket.send_json({
                    "type": "complete",
                    "message": "Agent execution completed successfully",
                })
            except RuntimeError:
                logger.info("Skipped completion signal — WebSocket already closed")

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")

    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        logger.error(traceback.format_exc())
        try:
            await websocket.send_json({
                "type": "error",
                "error_message": str(e),
                "error_type": type(e).__name__,
                "traceback": traceback.format_exc(),
            })
        except Exception:
            pass

    finally:
        # Dashboard v2 (TDR-2): remove session on every exit path. `_session_id`
        # may be unset if we exited before the registry add (auth failure, bad
        # payload) — guard against NameError.
        try:
            if "_session_id" in locals():
                await _registry.remove(request.agent_id, _session_id)
        except Exception as _cleanup_err:
            logger.warning(f"session registry cleanup failed: {_cleanup_err}")
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("WebSocket connection closed")


@router.websocket("/ws/ping")
async def websocket_ping(websocket: WebSocket):
    """Simple ping/pong WebSocket for connection testing"""
    await websocket.accept()
    logger.info("Ping WebSocket connected")

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
            else:
                await websocket.send_text(f"echo: {data}")
    except WebSocketDisconnect:
        logger.info("Ping WebSocket disconnected")
