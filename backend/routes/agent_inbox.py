"""
@file_name: agent_inbox.py
@author: Bin Liang
@date: 2026-03-11
@description: REST API routes for Agent Inbox (Matrix channel messages)

Agent Inbox displays Matrix channel messages grouped by room.
Data source: NexusMatrix API (complete room history with all participants).

Each room shows:
- room_id, room_name
- members (agent_id, agent_name, matrix_user_id)
- chronological messages from ALL participants

Provides endpoints for:
- GET /api/agent-inbox - Room-grouped Matrix messages (from NexusMatrix API)
- PUT /api/agent-inbox/{message_id}/read - Mark message as read (inbox_table)
"""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Query
from pydantic import BaseModel
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client


router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================

class RoomMember(BaseModel):
    """A member in a Matrix room (mapped to local agent info)."""
    agent_id: str
    agent_name: str
    matrix_user_id: str


class RoomMessage(BaseModel):
    """A single message within a room."""
    message_id: str
    sender_id: str          # Matrix user ID
    sender_name: str
    content: str
    is_read: bool = True    # Messages from Matrix API are treated as read
    created_at: Optional[str] = None


class MatrixRoom(BaseModel):
    """A Matrix room with its members and messages."""
    room_id: str
    room_name: str
    members: list[RoomMember] = []
    unread_count: int = 0
    messages: list[RoomMessage] = []
    latest_at: Optional[str] = None


class AgentInboxListResponse(BaseModel):
    """Agent inbox response — rooms with messages."""
    success: bool
    rooms: list[MatrixRoom] = []
    total_unread: int = 0
    error: Optional[str] = None


class MarkReadResponse(BaseModel):
    """Mark as read response model."""
    success: bool
    marked_count: int = 0
    error: Optional[str] = None


# =============================================================================
# Helper: Build agent name mapping
# =============================================================================

async def _build_agent_name_map(db_client) -> dict:
    """
    Build matrix_user_id → {agent_id, agent_name, matrix_user_id} mapping.

    Joins matrix_credentials and agents tables to resolve identities.
    """
    query = """
        SELECT mc.agent_id, mc.matrix_user_id, a.agent_name
        FROM matrix_credentials mc
        LEFT JOIN agents a ON mc.agent_id = a.agent_id
        WHERE mc.is_active = TRUE
    """
    rows = await db_client.execute(query, fetch=True)

    name_map = {}
    for row in rows:
        mid = row.get("matrix_user_id", "")
        if mid:
            name_map[mid] = {
                "agent_id": row.get("agent_id", ""),
                "agent_name": row.get("agent_name", "Unknown Agent"),
                "matrix_user_id": mid,
            }
    return name_map


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("", response_model=AgentInboxListResponse)
async def list_agent_inbox_rooms(
    agent_id: str = Query(..., description="Agent ID"),
    limit: int = Query(50, description="Max messages per room"),
):
    """
    List Matrix channel messages grouped by room.

    Fetches complete room history from NexusMatrix API (all participants),
    resolves agent identities via local DB, returns rooms sorted by latest
    message time.
    """
    logger.info(f"Listing agent inbox rooms for agent: {agent_id}")

    try:
        db_client = await get_db_client()

        # 1. Get agent's Matrix credential
        cred_rows = await db_client.execute(
            "SELECT api_key, matrix_user_id, server_url FROM matrix_credentials "
            "WHERE agent_id = %s AND is_active = TRUE",
            params=(agent_id,), fetch=True,
        )
        if not cred_rows:
            return AgentInboxListResponse(success=True, rooms=[], total_unread=0)

        cred = cred_rows[0]
        api_key = cred["api_key"]
        server_url = cred["server_url"]

        # 2. Build agent name mapping
        name_map = await _build_agent_name_map(db_client)

        # 3. Fetch rooms from NexusMatrix API
        from xyz_agent_context.module.matrix_module.matrix_client import NexusMatrixClient

        client = NexusMatrixClient(server_url=server_url)
        try:
            raw_rooms = await client.list_rooms(api_key=api_key) or []
        except Exception as e:
            logger.error(f"Failed to list rooms from NexusMatrix: {e}")
            return AgentInboxListResponse(success=False, error=str(e))

        # 4. Fetch message history + members for each room
        rooms = []
        total_unread = 0

        for r in raw_rooms:
            room_id = r.get("room_id", "")
            raw_room_name = r.get("name", "")
            if not room_id:
                continue

            # Fetch message history
            try:
                raw_msgs = await client.get_messages(
                    api_key=api_key, room_id=room_id, limit=limit,
                ) or []
            except Exception:
                raw_msgs = []

            # Fetch members
            try:
                raw_members = await client.get_room_members(
                    api_key=api_key, room_id=room_id,
                ) or []
            except Exception:
                raw_members = []

            # Resolve members
            members = []
            for m in raw_members:
                mid = m.get("user_id", "") if isinstance(m, dict) else str(m)
                info = name_map.get(mid, {})
                members.append(RoomMember(
                    agent_id=info.get("agent_id", ""),
                    agent_name=info.get("agent_name", mid.split(":")[0].lstrip("@") if ":" in mid else mid),
                    matrix_user_id=mid,
                ))

            # Build room display name: prefer explicit name, fallback to member names
            if raw_room_name:
                room_name = raw_room_name
            elif members:
                room_name = ", ".join(m.agent_name for m in members)
            else:
                room_name = room_id

            # Build messages (all participants, chronological order)
            messages = []
            for msg in raw_msgs:
                sender_mid = msg.get("sender", "")
                sender_info = name_map.get(sender_mid, {})
                sender_name = sender_info.get("agent_name",
                    sender_mid.split(":")[0].lstrip("@") if ":" in sender_mid else sender_mid)

                # Format timestamp
                ts = msg.get("timestamp") or msg.get("origin_server_ts")
                created_at = None
                if ts:
                    try:
                        if isinstance(ts, (int, float)):
                            created_at = datetime.fromtimestamp(
                                ts / 1000, tz=timezone.utc
                            ).strftime("%Y-%m-%dT%H:%M:%SZ")
                        else:
                            created_at = str(ts)
                    except Exception:
                        pass

                event_id = msg.get("event_id", "")
                messages.append(RoomMessage(
                    message_id=event_id or f"msg_{hash(f'{room_id}_{sender_mid}_{ts}') & 0xFFFFFFFF:08x}",
                    sender_id=sender_mid,
                    sender_name=sender_name,
                    content=msg.get("body", ""),
                    is_read=True,
                    created_at=created_at,
                ))

            latest_at = messages[-1].created_at if messages else None
            rooms.append(MatrixRoom(
                room_id=room_id,
                room_name=room_name,
                members=members,
                unread_count=0,
                messages=messages,
                latest_at=latest_at,
            ))

        await client.close()

        # Sort rooms by latest message (newest first)
        rooms.sort(key=lambda r: r.latest_at or "", reverse=True)

        logger.info(f"Found {len(rooms)} rooms with messages from NexusMatrix")

        return AgentInboxListResponse(
            success=True,
            rooms=rooms,
            total_unread=total_unread,
        )

    except Exception as e:
        logger.error(f"Error listing agent inbox rooms: {e}")
        return AgentInboxListResponse(success=False, error=str(e))


@router.put("/{message_id}/read", response_model=MarkReadResponse)
async def mark_message_read(message_id: str):
    """Mark a single message as read in inbox_table."""
    logger.info(f"Marking message as read: {message_id}")

    try:
        db_client = await get_db_client()
        query = "UPDATE inbox_table SET is_read = TRUE WHERE message_id = %s"
        result = await db_client.execute(query, params=(message_id,), fetch=False)
        count = result if isinstance(result, int) else 0

        return MarkReadResponse(success=True, marked_count=count)

    except Exception as e:
        logger.error(f"Error marking message read: {e}")
        return MarkReadResponse(success=False, error=str(e))
