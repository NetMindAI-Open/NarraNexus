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
from typing import List, Dict, Any, Optional, Tuple, TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from narranexus.platform.utils.url_safety import Resolver

from .base import BaseRepository
from narranexus.platform.utils import utc_now
from narranexus.platform.schema import MCPUrl


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
    resolver: Optional["Resolver"] = None,
    enforce_public_url: bool = True,
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
        resolver: DNS resolver override (tests inject a fake); None → real DNS.
        enforce_public_url: when True, reject URLs that resolve to a non-public
            address (SSRF gate). The DEPLOYMENT-MODE decision is the caller's:
            cloud passes True; local/desktop passes False, because a single-
            trusted-user install legitimately runs MCP servers on localhost and
            the OS user is already the trust boundary (a local agent has bash —
            an HTTP-layer block stops the real user, not a real attacker).

    Returns:
        (success: bool, error_message: Optional[str])
    """
    import httpx

    from narranexus.platform.utils.url_safety import (
        UnsafeUrlError,
        assert_public_http_url,
    )

    # SSRF gate (cloud only; see enforce_public_url): this fetches a user-/agent-
    # supplied URL from the server, so it can be pointed at internal targets —
    # cloud metadata (169.254.169.254), litellm/admin on the docker network,
    # loopback. Reject anything that resolves to a non-public address before
    # connecting. Post-resolution checking NARROWS the DNS-rebinding window at
    # validate time but does not close it — httpx re-resolves the hostname to
    # connect, so a TTL=0 record can still flip to an internal IP (TOCTOU).
    # Full closure needs connection pinning to the validated IP (tracked as a
    # follow-up). Keep the reject message generic — never echo the resolved IP.
    if enforce_public_url:
        try:
            await assert_public_http_url(url, resolver=resolver)
        except UnsafeUrlError:
            return False, "URL is not allowed: it must be a public http(s) endpoint."

    try:
        # Use streaming request to validate SSE endpoint
        # SSE is a continuous stream; just check if the connection was established successfully
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            follow_redirects=False,
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
                    # Status code only. The returned text is persisted to
                    # mcp_urls.last_error and shown to the user — echoing the
                    # upstream body would turn a blind server-side fetch into
                    # a readable one. The body is not read at all.
                    return False, f"HTTP {response.status_code} from the MCP server"

    except httpx.TimeoutException:
        return False, f"Connection timeout after {timeout}s"
    except httpx.ConnectError as e:
        # Exception text can name internal addresses; log it, return generic.
        logger.warning(f"MCP SSE validation connect error: {type(e).__name__}: {e}")
        return False, "Connection failed (could not reach the MCP server)"
    except Exception as e:
        logger.warning(f"MCP SSE validation failed: {type(e).__name__}: {e}")
        return False, "Connection failed (unexpected error while validating)"
