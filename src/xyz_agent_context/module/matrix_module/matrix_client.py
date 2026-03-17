"""
@file_name: matrix_client.py
@author: Bin Liang
@date: 2026-03-10
@description: NexusMatrix HTTP client wrapper

Encapsulates all HTTP calls to the NexusMatrix Server API.
This is a thin async client — business logic stays in the module layer.

The client is stateless (no persistent connection) and safe to share across
coroutines. Each call takes an api_key parameter for auth.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from loguru import logger


class NexusMatrixClient:
    """
    Async HTTP client for NexusMatrix Server.

    Wraps the NexusMatrix REST API with typed methods.
    Uses httpx.AsyncClient under the hood with connection pooling.

    Args:
        server_url: Base URL of the NexusMatrix server (e.g. "http://localhost:8953")
        timeout: Request timeout in seconds
    """

    def __init__(self, server_url: str, timeout: float = 30.0):
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared httpx client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.server_url,
                timeout=self.timeout,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _headers(self, api_key: str) -> Dict[str, str]:
        """Build auth headers."""
        return {"X-Api-Key": api_key}

    # =========================================================================
    # Auth / Registration
    # =========================================================================

    async def register_agent(
        self,
        agent_name: str,
        description: str,
        capabilities: List[str],
        owner: str = "",
        preferred_username: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Register a new agent on NexusMatrix Server.

        This is a public endpoint — no API key required.

        Args:
            agent_name: Display name for the agent
            description: Agent description
            capabilities: List of capability strings
            owner: Owner identifier (optional)
            preferred_username: Preferred Matrix username, e.g. agent_id (optional)
            metadata: Extra metadata dict (optional)

        Returns:
            Registration result dict with agent_id, matrix_user_id, api_key,
            or None on failure
        """
        client = await self._get_client()
        payload = {
            "agent_name": agent_name,
            "description": description,
            "capabilities": capabilities,
        }
        if owner:
            payload["owner"] = owner
        if preferred_username:
            payload["preferred_username"] = preferred_username
        if metadata:
            payload["metadata"] = metadata

        try:
            resp = await client.post("/api/v1/registry/register", json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                return data.get("data")
            logger.error(f"Register agent failed: {data}")
            return None
        except Exception as e:
            logger.error(f"Register agent request failed: {e}")
            return None

    # =========================================================================
    # Heartbeat / Sync
    # =========================================================================

    async def heartbeat(self, api_key: str) -> Optional[Dict[str, Any]]:
        """
        Check for updates via the heartbeat endpoint.

        Lightweight periodic check — returns unread counts, pending invites,
        and action suggestions.

        Args:
            api_key: Agent's API key

        Returns:
            Heartbeat response dict, or None on failure
        """
        client = await self._get_client()
        try:
            resp = await client.get(
                "/api/v1/heartbeat",
                headers=self._headers(api_key),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data") if data.get("success") else None
        except Exception as e:
            logger.warning(f"Heartbeat request failed: {e}")
            return None

    async def sync(
        self,
        api_key: str,
        since: Optional[str] = None,
        timeout: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """
        Full event synchronization with optional long-poll.

        Args:
            api_key: Agent's API key
            since: Pagination token from previous sync (None for initial sync)
            timeout: Long-poll timeout in milliseconds (0 = immediate return)

        Returns:
            Sync response dict with next_batch, rooms, etc., or None on failure
        """
        client = await self._get_client()
        params: Dict[str, Any] = {"timeout": timeout}
        if since:
            params["since"] = since

        try:
            resp = await client.get(
                "/api/v1/sync",
                headers=self._headers(api_key),
                params=params,
                timeout=max(self.timeout, (timeout / 1000) + 10),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data") if data.get("success") else None
        except Exception as e:
            logger.warning(f"Sync request failed: {e}")
            return None

    # =========================================================================
    # Messages
    # =========================================================================

    async def send_message(
        self,
        api_key: str,
        room_id: str,
        content: str,
        mention_user_ids: Optional[List[str]] = None,
        mention_everyone: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Send a text message to a room, optionally with @mentions.

        When mention_user_ids or mention_everyone is set, the message body is
        prefixed with @-tags and the Matrix m.mentions field is included.
        MatrixTrigger uses the body prefix for filtering (NexusMatrix doesn't
        return m.mentions in message history, so body text is the source of truth).

        Args:
            api_key: Agent's API key
            room_id: Target room ID
            content: Message text
            mention_user_ids: List of Matrix user IDs to mention (e.g. ["@alice:localhost"])
            mention_everyone: If True, mention @everyone (room-wide broadcast)

        Returns:
            Send result dict, or None on failure
        """
        # Build m.mentions for structured mention data
        body = content
        extra_content: Dict[str, Any] = {}

        if mention_everyone:
            extra_content["m.mentions"] = {"room": True}
        elif mention_user_ids:
            extra_content["m.mentions"] = {"user_ids": mention_user_ids}

        client = await self._get_client()
        try:
            payload: Dict[str, Any] = {"room_id": room_id, "body": body}
            if extra_content:
                payload["extra_content"] = extra_content
            resp = await client.post(
                "/api/v1/messages/send",
                headers=self._headers(api_key),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data") if data.get("success") else None
        except Exception as e:
            logger.error(f"Send message failed: {e}")
            return None

    async def get_messages(
        self,
        api_key: str,
        room_id: str,
        limit: int = 20,
        since: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get message history for a room.

        Args:
            api_key: Agent's API key
            room_id: Room ID
            limit: Max number of messages
            since: Pagination token (optional)

        Returns:
            List of message dicts, or None on failure
        """
        client = await self._get_client()
        params: Dict[str, Any] = {"limit": limit}
        if since:
            params["start"] = since

        try:
            resp = await client.get(
                f"/api/v1/messages/{room_id}/history",
                headers=self._headers(api_key),
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                return data.get("data", {}).get("messages", [])
            return None
        except Exception as e:
            logger.error(f"Get messages failed: {e}")
            return None

    async def mark_read(
        self,
        api_key: str,
        room_id: str,
        event_id: Optional[str] = None,
    ) -> bool:
        """
        Mark messages in a room as read.

        Args:
            api_key: Agent's API key
            room_id: Room ID
            event_id: Specific event to mark (optional, marks all if omitted)

        Returns:
            True if successful
        """
        client = await self._get_client()
        try:
            if event_id:
                url = f"/api/v1/messages/{room_id}/read/{event_id}"
            else:
                url = f"/api/v1/messages/{room_id}/read"
            resp = await client.post(url, headers=self._headers(api_key))
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Mark read failed: {e}")
            return False

    # =========================================================================
    # Rooms
    # =========================================================================

    async def get_room_info(
        self, api_key: str, room_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get room info (name, member_count, creator, etc.).

        Args:
            api_key: Agent's API key
            room_id: Room ID

        Returns:
            Room info dict, or None on failure
        """
        client = await self._get_client()
        try:
            resp = await client.get(
                f"/api/v1/rooms/{room_id}/info",
                headers=self._headers(api_key),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data") if data.get("success") else None
        except Exception as e:
            logger.warning(f"Get room info failed: {e}")
            return None

    async def create_room(
        self,
        api_key: str,
        name: str = "",
        invite_user_ids: Optional[List[str]] = None,
        is_direct: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Create a new room.

        Args:
            api_key: Agent's API key
            name: Room name (optional for DMs)
            invite_user_ids: Users to invite at creation
            is_direct: Whether this is a direct message room

        Returns:
            Room creation result dict, or None on failure
        """
        client = await self._get_client()
        payload: Dict[str, Any] = {
            "preset": "private_chat" if is_direct else "public_chat",
        }
        if name:
            payload["name"] = name
        if invite_user_ids:
            payload["invite"] = invite_user_ids

        try:
            resp = await client.post(
                "/api/v1/rooms/create",
                headers=self._headers(api_key),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data") if data.get("success") else None
        except Exception as e:
            logger.error(f"Create room failed: {e}")
            return None

    async def join_room(self, api_key: str, room_id: str) -> bool:
        """
        Join a room.

        Args:
            api_key: Agent's API key
            room_id: Room ID or alias

        Returns:
            True if successful
        """
        client = await self._get_client()
        try:
            resp = await client.post(
                "/api/v1/rooms/join",
                headers=self._headers(api_key),
                json={"room_id": room_id},
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Join room failed: {e}")
            return False

    async def list_rooms(self, api_key: str) -> Optional[List[Dict[str, Any]]]:
        """
        List all joined rooms.

        Args:
            api_key: Agent's API key

        Returns:
            List of room dicts, or None on failure
        """
        client = await self._get_client()
        try:
            resp = await client.get(
                "/api/v1/rooms/joined",
                headers=self._headers(api_key),
            )
            resp.raise_for_status()
            data = resp.json()
            # Server may return a raw list or a wrapped {"success": true, "data": [...]}
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                if data.get("success"):
                    result = data.get("data")
                    if isinstance(result, list):
                        return result
                    if isinstance(result, dict):
                        return result.get("rooms", [])
                    return []
            return None
        except Exception as e:
            logger.error(f"List rooms failed: {e}")
            return None

    async def get_room_members(
        self, api_key: str, room_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get room member list.

        Args:
            api_key: Agent's API key
            room_id: Room ID

        Returns:
            List of member dicts, or None on failure
        """
        client = await self._get_client()
        try:
            resp = await client.get(
                f"/api/v1/rooms/{room_id}/members",
                headers=self._headers(api_key),
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                # API returns ApiResponse[List[RoomMember]], so data["data"] is a list directly
                result = data.get("data", [])
                if isinstance(result, list):
                    return result
                # Fallback: if wrapped in dict with "members" key
                return result.get("members", []) if isinstance(result, dict) else []
            return None
        except Exception as e:
            logger.error(f"Get room members failed: {e}")
            return None

    async def invite_to_room(
        self, api_key: str, room_id: str, user_id: str
    ) -> bool:
        """
        Invite a user to a room.

        Args:
            api_key: Agent's API key
            room_id: Room ID
            user_id: Matrix user ID to invite

        Returns:
            True if successful
        """
        client = await self._get_client()
        try:
            resp = await client.post(
                f"/api/v1/rooms/{room_id}/invite",
                headers=self._headers(api_key),
                json={"user_id": user_id},
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Invite to room failed: {e}")
            return False

    # =========================================================================
    # Registry / Discovery
    # =========================================================================

    async def search_agents(
        self,
        api_key: str,
        query: str = "",
        capabilities: Optional[List[str]] = None,
        limit: int = 10,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Semantic search for agents in the registry.

        Args:
            api_key: Agent's API key
            query: Search query text
            capabilities: Filter by capabilities (optional)
            limit: Max results

        Returns:
            List of search result dicts, or None on failure
        """
        client = await self._get_client()
        payload: Dict[str, Any] = {"query": query, "limit": limit}
        if capabilities:
            payload["capabilities"] = capabilities

        try:
            resp = await client.post(
                "/api/v1/registry/search",
                headers=self._headers(api_key),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data") if data.get("success") else None
        except Exception as e:
            logger.error(f"Search agents failed: {e}")
            return None

    async def get_agent_profile(
        self, api_key: str, agent_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get a specific agent's profile from the registry.

        Args:
            api_key: Agent's API key
            agent_id: Target agent's ID

        Returns:
            Agent profile dict, or None on failure
        """
        client = await self._get_client()
        try:
            resp = await client.get(
                f"/api/v1/registry/agents/{agent_id}",
                headers=self._headers(api_key),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data") if data.get("success") else None
        except Exception as e:
            logger.error(f"Get agent profile failed: {e}")
            return None

    async def update_agent_profile(
        self, api_key: str, agent_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Update agent profile on NexusMatrix Server.

        Triggers re-embedding if description or capabilities change,
        keeping semantic search results accurate.

        Args:
            api_key: Agent's API key
            agent_id: Agent's NexusMatrix agent_id
            updates: Fields to update (agent_name, description, etc.)

        Returns:
            Updated profile dict, or None on failure
        """
        client = await self._get_client()
        try:
            resp = await client.put(
                f"/api/v1/registry/agents/{agent_id}",
                headers=self._headers(api_key),
                json=updates,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data") if data.get("success") else None
        except Exception as e:
            logger.error(f"Update agent profile failed: {e}")
            return None

    async def get_agent_by_matrix_user_id(
        self, api_key: str, matrix_user_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Look up an agent profile by their Matrix user ID.

        Uses the search endpoint since the registry only indexes by internal agent_id.

        Args:
            api_key: Agent's API key
            matrix_user_id: Matrix user ID (e.g. "@agent_xxx:localhost")

        Returns:
            Agent profile dict if found, None otherwise
        """
        results = await self.search_agents(api_key, query=matrix_user_id, limit=5)
        if results:
            for r in results:
                if r.get("matrix_user_id") == matrix_user_id:
                    return r
        return None

    async def get_my_profile(self, api_key: str) -> Optional[Dict[str, Any]]:
        """
        Get the current agent's own profile.

        Args:
            api_key: Agent's API key

        Returns:
            Profile dict, or None on failure
        """
        client = await self._get_client()
        try:
            resp = await client.get(
                "/api/v1/registry/me",
                headers=self._headers(api_key),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data") if data.get("success") else None
        except Exception as e:
            logger.error(f"Get my profile failed: {e}")
            return None
