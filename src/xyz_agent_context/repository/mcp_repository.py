"""
@file_name: mcp_repository.py
@author: NetMind.AI
@date: 2025-12-02
@description: MCP Repository - Data access layer for MCP URLs

Responsibilities:
- CRUD operations for MCP URLs
- Connection status management
- Query by agent+user
- SSE connection validation
"""

import json
from typing import List, Dict, Any, Optional, Tuple
from loguru import logger

from .base import BaseRepository
from xyz_agent_context.utils import utc_now
from xyz_agent_context.schema import MCPUrl


class MCPRepository(BaseRepository[MCPUrl]):
    """
    MCP Repository implementation

    Usage example:
        repo = MCPRepository(db_client)

        # Add MCP
        await repo.add_mcp(agent_id, user_id, name, url)

        # Get all MCPs for agent+user
        mcps = await repo.get_mcps_by_agent_user(agent_id, user_id)

        # Update connection status
        await repo.update_connection_status(mcp_id, "connected")
    """

    table_name = "mcp_urls"
    id_field = "id"

    _json_fields = {"metadata", "headers"}

    async def get_mcp(self, mcp_id: str) -> Optional[MCPUrl]:
        """Get a single MCP"""
        logger.debug(f"    → MCPRepository.get_mcp({mcp_id})")
        return await self.find_one({"mcp_id": mcp_id})

    async def get_mcps_by_agent_user(
        self,
        agent_id: str,
        user_id: str,
        is_enabled: Optional[bool] = None,
        limit: int = 100
    ) -> List[MCPUrl]:
        """Get all MCP URLs for a specific agent+user"""
        logger.debug(f"    → MCPRepository.get_mcps_by_agent_user({agent_id}, {user_id})")

        filters = {"agent_id": agent_id, "user_id": user_id}
        if is_enabled is not None:
            filters["is_enabled"] = is_enabled

        return await self.find(
            filters=filters,
            limit=limit,
            order_by="created_at DESC"
        )

    async def add_mcp(
        self,
        agent_id: str,
        user_id: str,
        mcp_id: str,
        name: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        description: Optional[str] = None,
        is_enabled: bool = True,
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """Add a new MCP URL"""
        logger.debug(f"    → MCPRepository.add_mcp({mcp_id})")

        mcp = MCPUrl(
            mcp_id=mcp_id,
            agent_id=agent_id,
            user_id=user_id,
            name=name,
            url=url,
            headers=headers,
            description=description,
            is_enabled=is_enabled,
            connection_status="unknown",
            metadata=metadata,
        )

        return await self.insert(mcp)

    async def update_mcp(self, mcp_id: str, updates: Dict[str, Any]) -> int:
        """Update MCP information"""
        logger.debug(f"    → MCPRepository.update_mcp({mcp_id})")

        # Serialize JSON fields on a copy — never mutate the caller's dict.
        updates = dict(updates)
        for json_field in ("metadata", "headers"):
            if json_field in updates and updates[json_field] is not None and not isinstance(updates[json_field], str):
                updates[json_field] = json.dumps(updates[json_field], ensure_ascii=False)

        query = f"""
            UPDATE {self.table_name}
            SET {', '.join(f'`{k}` = %s' for k in updates.keys())}
            WHERE mcp_id = %s
        """

        params = list(updates.values()) + [mcp_id]
        result = await self._db.execute(query, params=tuple(params), fetch=False)
        return result if isinstance(result, int) else 0

    async def update_connection_status(
        self,
        mcp_id: str,
        status: str,
        error: Optional[str] = None
    ) -> int:
        """Update MCP connection status"""
        logger.debug(f"    → MCPRepository.update_connection_status({mcp_id}, {status})")

        updates = {
            "connection_status": status,
            "last_check_time": utc_now(),
        }
        if error:
            updates["last_error"] = error

        return await self.update_mcp(mcp_id, updates)

    async def delete_mcp(self, mcp_id: str) -> int:
        """Delete an MCP"""
        logger.debug(f"    → MCPRepository.delete_mcp({mcp_id})")

        query = f"DELETE FROM {self.table_name} WHERE mcp_id = %s"
        result = await self._db.execute(query, params=(mcp_id,), fetch=False)
        return result if isinstance(result, int) else 0

    def _row_to_entity(self, row: Dict[str, Any]) -> MCPUrl:
        """Convert a database row to an MCPUrl object"""
        metadata = self._parse_json_field(row.get("metadata"), None)
        headers = self._parse_json_field(row.get("headers"), None)

        return MCPUrl(
            id=row.get("id"),
            mcp_id=row["mcp_id"],
            agent_id=row["agent_id"],
            user_id=row["user_id"],
            name=row["name"],
            url=row["url"],
            headers=headers,
            description=row.get("description"),
            is_enabled=row.get("is_enabled", True),
            connection_status=row.get("connection_status"),
            last_check_time=row.get("last_check_time"),
            last_error=row.get("last_error"),
            metadata=metadata,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def _entity_to_row(self, entity: MCPUrl) -> Dict[str, Any]:
        """Convert an MCPUrl object to a database row"""
        return {
            "mcp_id": entity.mcp_id,
            "agent_id": entity.agent_id,
            "user_id": entity.user_id,
            "name": entity.name,
            "url": entity.url,
            "headers": json.dumps(entity.headers, ensure_ascii=False) if entity.headers else None,
            "description": entity.description,
            "is_enabled": entity.is_enabled,
            "connection_status": entity.connection_status,
            "last_check_time": entity.last_check_time,
            "last_error": entity.last_error,
            "metadata": json.dumps(entity.metadata, ensure_ascii=False) if entity.metadata else None,
        }

    @staticmethod
    def _parse_json_field(value: Any, default: Any) -> Any:
        """Parse a JSON field"""
        if value is None:
            return default
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return default
        return value


# =============================================================================
# MCP SSE Connection Validation
# =============================================================================

async def validate_mcp_sse_connection(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
) -> Tuple[bool, Optional[str]]:
    """
    Validate whether an MCP SSE URL can connect normally

    Uses httpx streaming request for validation, since SSE is a continuous stream
    and a regular GET request would keep waiting and timeout.

    Args:
        url: MCP SSE URL
        headers: Custom HTTP headers to send (e.g. Authorization); values are
            secrets — never log them
        timeout: Timeout duration (seconds)

    Returns:
        (success: bool, error_message: Optional[str])
    """
    import httpx

    try:
        # Use streaming request to validate SSE endpoint
        # SSE is a continuous stream; just check if the connection was established successfully
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0)
        ) as client:
            # Custom headers first, SSE baseline last: the code below asserts
            # the response is text/event-stream, so a user-supplied Accept
            # override would make a perfectly healthy endpoint fail validation.
            request_headers = dict(headers) if headers else {}
            request_headers.update({
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
            })
            async with client.stream(
                "GET",
                url,
                headers=request_headers,
            ) as response:
                # Check response status
                if response.status_code == 200:
                    # Check if Content-Type is SSE
                    content_type = response.headers.get("content-type", "")
                    if "text/event-stream" in content_type:
                        # Try to read the first chunk to confirm the connection is working
                        try:
                            async for chunk in response.aiter_bytes():
                                # Receiving data means the connection is successful
                                if chunk:
                                    return True, None
                                break
                        except Exception:
                            pass
                        # Even without data received, consider it successful if status code is correct
                        return True, None
                    else:
                        return True, f"Warning: Content-Type is {content_type}, expected text/event-stream"
                else:
                    # Read error response content
                    error_body = ""
                    try:
                        async for chunk in response.aiter_bytes():
                            error_body += chunk.decode("utf-8", errors="ignore")
                            if len(error_body) > 200:
                                break
                    except Exception:
                        pass
                    return False, f"HTTP {response.status_code}: {error_body[:200]}"

    except httpx.TimeoutException:
        return False, f"Connection timeout after {timeout}s"
    except httpx.ConnectError as e:
        return False, f"Connection failed: {str(e)}"
    except Exception as e:
        return False, f"Error: {str(e)}"
