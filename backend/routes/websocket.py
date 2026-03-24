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
4. Connection closes when execution completes

Message Types:
- progress: Step-by-step execution progress
- agent_response: Text output from the agent
- agent_thinking: Agent's thinking process
- tool_call: Tool/function calls
- error: Error messages
"""

import asyncio
import traceback
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from loguru import logger

from backend.config import settings

from xyz_agent_context.agent_runtime import AgentRuntime
from xyz_agent_context.schema import WorkingSource
from xyz_agent_context.repository import MCPRepository
from xyz_agent_context.utils.db_factory import get_db_client


router = APIRouter()


class AgentRunRequest(BaseModel):
    """WebSocket request payload for running an agent"""
    agent_id: str
    user_id: str
    input_content: str
    working_source: Optional[str] = "chat"


@router.websocket("/ws/agent/run")
async def websocket_agent_run(websocket: WebSocket):
    """
    WebSocket endpoint for streaming agent execution

    Protocol:
    1. Accept WebSocket connection
    2. Receive JSON request with agent_id, user_id, input_content
    3. Stream RuntimeMessage objects as JSON
    4. Close connection on completion or error
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

        # Convert working_source string to enum
        working_source = WorkingSource(request.working_source)

        # Create runtime and stream messages
        logger.info(f"Starting agent runtime: agent_id={request.agent_id}, user_id={request.user_id}")

        # Load MCP URLs from database for this agent+user
        mcp_urls = {}
        try:
            db_client = await get_db_client()
            mcp_repo = MCPRepository(db_client)
            mcps = await mcp_repo.get_mcps_by_agent_user(
                agent_id=request.agent_id,
                user_id=request.user_id,
                is_enabled=True  # Only load enabled MCPs
            )
            for mcp in mcps:
                mcp_urls[mcp.name] = mcp.url
            if mcp_urls:
                logger.info(f"Loaded {len(mcp_urls)} MCP servers: {list(mcp_urls.keys())}")
        except Exception as e:
            logger.warning(f"Failed to load MCP URLs: {e}")

        # Heartbeat task: periodically send heartbeat to prevent idle timeout
        heartbeat_stop = asyncio.Event()

        async def heartbeat_loop():
            """Periodically send heartbeat messages to keep WebSocket connection alive"""
            while not heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(heartbeat_stop.wait(), timeout=settings.ws_heartbeat_interval)
                    break  # stop event was set, exit
                except asyncio.TimeoutError:
                    try:
                        await websocket.send_json({"type": "heartbeat"})
                    except Exception:
                        break  # connection already closed

        heartbeat_task = asyncio.create_task(heartbeat_loop())

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
                        # WebSocket already closed (client disconnected mid-stream)
                        logger.info("WebSocket closed during streaming, stopping send loop")
                        break
                    # Verbose logging: show type + content preview for monitoring
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
        finally:
            heartbeat_stop.set()
            await heartbeat_task

        _ws_end = _time.monotonic()
        _total = _ws_end - _ws_start
        _post_stream = (_ws_end - _step3_end) if _step3_end else 0
        logger.info(
            f"Agent execution completed — total={_total:.1f}s, "
            f"post-stream (step 4)={_post_stream:.1f}s"
        )

        # Send completion signal (skip if WebSocket already closed)
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
            pass  # Client may have disconnected

    finally:
        try:
            await websocket.close()
        except Exception:
            pass  # Already closed
        logger.info("WebSocket connection closed")


@router.websocket("/ws/ping")
async def websocket_ping(websocket: WebSocket):
    """
    Simple ping/pong WebSocket for connection testing
    """
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
