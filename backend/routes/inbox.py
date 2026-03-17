"""
@file_name: inbox.py
@author: NetMind.AI
@date: 2025-11-28
@description: REST API routes for inbox messages

Provides endpoints for:
- GET /api/inbox - List inbox messages for a user
- PUT /api/inbox/{message_id}/read - Mark message as read
- PUT /api/inbox/read-all - Mark all messages as read
"""

from typing import Optional
from fastapi import APIRouter, Query
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils import format_for_api
from xyz_agent_context.repository import InboxRepository
from xyz_agent_context.schema import (
    MessageSourceResponse,
    InboxMessageResponse,
    InboxListResponse,
    MarkReadResponse,
)


router = APIRouter()


def inbox_model_to_response(msg) -> InboxMessageResponse:
    """Convert InboxMessage model to response"""
    # Handle source conversion
    source = None
    if msg.source:
        source = MessageSourceResponse(
            type=msg.source.type if hasattr(msg.source, 'type') else None,
            id=msg.source.id if hasattr(msg.source, 'id') else None,
        )

    return InboxMessageResponse(
        message_id=msg.message_id,
        user_id=msg.user_id,
        message_type=msg.message_type.value if hasattr(msg.message_type, 'value') else str(msg.message_type),
        title=msg.title,
        content=msg.content,
        source=source,
        event_id=msg.event_id,
        is_read=msg.is_read,
        created_at=format_for_api(msg.created_at),
    )


@router.get("", response_model=InboxListResponse)
async def list_inbox_messages(
    user_id: str = Query(..., description="User ID"),
    is_read: Optional[bool] = Query(None, description="Filter by read status"),
    limit: int = Query(50, description="Max number of messages to return"),
    offset: int = Query(0, description="Number of messages to skip"),
):
    """
    List inbox messages for a user (supports pagination via limit/offset)
    """
    logger.info(f"Listing inbox for user: {user_id}, is_read: {is_read}, limit: {limit}, offset: {offset}")

    try:
        db_client = await get_db_client()
        repo = InboxRepository(db_client)

        # Get messages with pagination
        messages = await repo.get_messages(
            user_id=user_id,
            is_read=is_read,
            limit=limit,
            offset=offset,
        )

        # Get total count and unread count
        total = await repo.get_total_count(user_id, is_read=is_read)
        unread = await repo.get_unread_count(user_id)

        # Convert to response format
        message_responses = [inbox_model_to_response(msg) for msg in messages]

        logger.info(f"Found {len(message_responses)} messages (total: {total}), {unread} unread")

        return InboxListResponse(
            success=True,
            messages=message_responses,
            count=len(message_responses),
            total_count=total,
            unread_count=unread,
        )

    except Exception as e:
        logger.error(f"Error listing inbox: {e}")
        return InboxListResponse(
            success=False,
            error=str(e)
        )


@router.put("/{message_id}/read", response_model=MarkReadResponse)
async def mark_message_read(message_id: str):
    """
    Mark a single message as read
    """
    logger.info(f"Marking message as read: {message_id}")

    try:
        db_client = await get_db_client()
        repo = InboxRepository(db_client)

        await repo.mark_as_read(message_id)

        return MarkReadResponse(
            success=True,
            marked_count=1,
        )

    except Exception as e:
        logger.error(f"Error marking message read: {e}")
        return MarkReadResponse(
            success=False,
            error=str(e)
        )


@router.put("/read-all", response_model=MarkReadResponse)
async def mark_all_messages_read(
    user_id: str = Query(..., description="User ID"),
):
    """
    Mark all messages as read for a user
    """
    logger.info(f"Marking all messages as read for user: {user_id}")

    try:
        db_client = await get_db_client()
        repo = InboxRepository(db_client)

        count = await repo.mark_all_as_read(user_id)

        logger.info(f"Marked {count} messages as read")

        return MarkReadResponse(
            success=True,
            marked_count=count,
        )

    except Exception as e:
        logger.error(f"Error marking all messages read: {e}")
        return MarkReadResponse(
            success=False,
            error=str(e)
        )
