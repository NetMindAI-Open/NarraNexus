"""
@file_name: agents_mcps.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent MCP management routes

Provides endpoints for:
- GET /{agent_id}/mcps - List all MCP URLs
- POST /{agent_id}/mcps - Add new MCP URL
- PUT /{agent_id}/mcps/{mcp_id} - Update MCP URL
- DELETE /{agent_id}/mcps/{mcp_id} - Delete MCP URL
- POST /{agent_id}/mcps/{mcp_id}/validate - Validate single MCP connection
- POST /{agent_id}/mcps/validate-all - Batch validate all MCPs
"""

import uuid
import asyncio

from fastapi import APIRouter, Query
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils import format_for_api
from xyz_agent_context.repository import MCPRepository
from xyz_agent_context.repository.mcp_repository import validate_mcp_sse_connection
from xyz_agent_context.schema import (
    MCPUrl,
    MCPInfo,
    MCPListResponse,
    MCPCreateRequest,
    MCPUpdateRequest,
    MCPResponse,
    MCPValidateResponse,
    MCPValidateAllResponse,
)


router = APIRouter()


def _mcp_to_info(mcp: MCPUrl) -> MCPInfo:
    """Convert MCPUrl data model to MCPInfo response model"""
    return MCPInfo(
        mcp_id=mcp.mcp_id,
        agent_id=mcp.agent_id,
        user_id=mcp.user_id,
        name=mcp.name,
        url=mcp.url,
        description=mcp.description,
        is_enabled=mcp.is_enabled,
        connection_status=mcp.connection_status,
        last_check_time=format_for_api(mcp.last_check_time),
        last_error=mcp.last_error,
        created_at=format_for_api(mcp.created_at),
        updated_at=format_for_api(mcp.updated_at),
    )


@router.get("/{agent_id}/mcps", response_model=MCPListResponse)
async def list_mcps(
    agent_id: str,
    user_id: str = Query(..., description="User ID"),
):
    """List all MCP URLs for Agent+User"""
    logger.info(f"Listing MCPs for agent: {agent_id}, user: {user_id}")

    try:
        db_client = await get_db_client()
        repo = MCPRepository(db_client)
        mcps = await repo.get_mcps_by_agent_user(agent_id=agent_id, user_id=user_id)
        mcp_list = [_mcp_to_info(mcp) for mcp in mcps]

        return MCPListResponse(success=True, mcps=mcp_list, count=len(mcp_list))

    except Exception as e:
        logger.error(f"Error listing MCPs: {e}")
        return MCPListResponse(success=False, error=str(e))


@router.post("/{agent_id}/mcps", response_model=MCPResponse)
async def create_mcp(
    agent_id: str,
    request: MCPCreateRequest,
    user_id: str = Query(..., description="User ID"),
):
    """Create a new MCP URL"""
    logger.info(f"Creating MCP for agent: {agent_id}, user: {user_id}, name: {request.name}")

    try:
        if not request.url.startswith(("http://", "https://")):
            return MCPResponse(
                success=False,
                error="URL must start with http:// or https://"
            )

        db_client = await get_db_client()
        repo = MCPRepository(db_client)

        mcp_id = f"mcp_{uuid.uuid4().hex[:8]}"

        record_id = await repo.add_mcp(
            agent_id=agent_id,
            user_id=user_id,
            mcp_id=mcp_id,
            name=request.name,
            url=request.url,
            description=request.description,
            is_enabled=request.is_enabled
        )

        mcps = await repo.get_mcps_by_agent_user(agent_id, user_id)
        created_mcp = next((m for m in mcps if m.id == record_id), None)

        return MCPResponse(
            success=True,
            mcp=_mcp_to_info(created_mcp) if created_mcp else None,
        )

    except Exception as e:
        logger.error(f"Error creating MCP: {e}")
        return MCPResponse(success=False, error=str(e))


@router.put("/{agent_id}/mcps/{mcp_id}", response_model=MCPResponse)
async def update_mcp_endpoint(
    agent_id: str,
    mcp_id: str,
    request: MCPUpdateRequest,
    user_id: str = Query(..., description="User ID"),
):
    """Update an existing MCP URL"""
    logger.info(f"Updating MCP {mcp_id} for agent: {agent_id}, user: {user_id}")

    try:
        db_client = await get_db_client()
        repo = MCPRepository(db_client)

        existing_mcp = await repo.get_mcp(mcp_id)
        if not existing_mcp:
            return MCPResponse(success=False, error=f"MCP not found: {mcp_id}")

        if existing_mcp.agent_id != agent_id or existing_mcp.user_id != user_id:
            return MCPResponse(success=False, error="MCP does not belong to this agent+user")

        if request.url and not request.url.startswith(("http://", "https://")):
            return MCPResponse(success=False, error="URL must start with http:// or https://")

        await repo.update_mcp(
            mcp_id=mcp_id,
            name=request.name,
            url=request.url,
            description=request.description,
            is_enabled=request.is_enabled
        )

        updated_mcp = await repo.get_mcp(mcp_id)

        return MCPResponse(
            success=True,
            mcp=_mcp_to_info(updated_mcp) if updated_mcp else None,
        )

    except Exception as e:
        logger.error(f"Error updating MCP: {e}")
        return MCPResponse(success=False, error=str(e))


@router.delete("/{agent_id}/mcps/{mcp_id}", response_model=MCPResponse)
async def delete_mcp_endpoint(
    agent_id: str,
    mcp_id: str,
    user_id: str = Query(..., description="User ID"),
):
    """Delete MCP URL"""
    logger.info(f"Deleting MCP {mcp_id} for agent: {agent_id}, user: {user_id}")

    try:
        db_client = await get_db_client()
        repo = MCPRepository(db_client)

        existing_mcp = await repo.get_mcp(mcp_id)
        if not existing_mcp:
            return MCPResponse(success=False, error=f"MCP not found: {mcp_id}")

        if existing_mcp.agent_id != agent_id or existing_mcp.user_id != user_id:
            return MCPResponse(success=False, error="MCP does not belong to this agent+user")

        await repo.delete_mcp(mcp_id)

        return MCPResponse(success=True, mcp=_mcp_to_info(existing_mcp))

    except Exception as e:
        logger.error(f"Error deleting MCP: {e}")
        return MCPResponse(success=False, error=str(e))


@router.post("/{agent_id}/mcps/{mcp_id}/validate", response_model=MCPValidateResponse)
async def validate_mcp_endpoint(
    agent_id: str,
    mcp_id: str,
    user_id: str = Query(..., description="User ID"),
):
    """Validate a single MCP SSE connection"""
    logger.info(f"Validating MCP {mcp_id} for agent: {agent_id}, user: {user_id}")

    try:
        db_client = await get_db_client()
        repo = MCPRepository(db_client)

        existing_mcp = await repo.get_mcp(mcp_id)
        if not existing_mcp:
            return MCPValidateResponse(
                success=False, mcp_id=mcp_id, connected=False,
                error=f"MCP not found: {mcp_id}"
            )

        if existing_mcp.agent_id != agent_id or existing_mcp.user_id != user_id:
            return MCPValidateResponse(
                success=False, mcp_id=mcp_id, connected=False,
                error="MCP does not belong to this agent+user"
            )

        connected, error = await validate_mcp_sse_connection(existing_mcp.url)

        status = "connected" if connected else "failed"
        await repo.update_connection_status(mcp_id=mcp_id, status=status, error=error)

        return MCPValidateResponse(
            success=True, mcp_id=mcp_id, connected=connected, error=error
        )

    except Exception as e:
        logger.error(f"Error validating MCP: {e}")
        return MCPValidateResponse(
            success=False, mcp_id=mcp_id, connected=False, error=str(e)
        )


@router.post("/{agent_id}/mcps/validate-all", response_model=MCPValidateAllResponse)
async def validate_all_mcps_endpoint(
    agent_id: str,
    user_id: str = Query(..., description="User ID"),
):
    """Batch validate all MCP SSE connections for Agent+User (parallel execution)"""
    logger.info(f"Validating all MCPs for agent: {agent_id}, user: {user_id}")

    try:
        db_client = await get_db_client()
        repo = MCPRepository(db_client)

        mcps = await repo.get_mcps_by_agent_user(agent_id=agent_id, user_id=user_id)

        if not mcps:
            return MCPValidateAllResponse(
                success=True, results=[], total=0, connected=0, failed=0
            )

        async def validate_single(mcp: MCPUrl) -> MCPValidateResponse:
            connected, error = await validate_mcp_sse_connection(mcp.url)
            status = "connected" if connected else "failed"
            await repo.update_connection_status(
                mcp_id=mcp.mcp_id, status=status, error=error
            )
            return MCPValidateResponse(
                success=True, mcp_id=mcp.mcp_id, connected=connected, error=error
            )

        results = await asyncio.gather(*[validate_single(mcp) for mcp in mcps])

        connected_count = sum(1 for r in results if r.connected)
        failed_count = sum(1 for r in results if not r.connected)

        return MCPValidateAllResponse(
            success=True,
            results=results,
            total=len(results),
            connected=connected_count,
            failed=failed_count,
        )

    except Exception as e:
        logger.error(f"Error validating all MCPs: {e}")
        return MCPValidateAllResponse(success=False, error=str(e))
