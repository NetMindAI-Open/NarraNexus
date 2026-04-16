"""
@file_name: inbox_schema.py
@author: NetMind.AI
@date: 2025-11-25
@description: Inbox Schema - Inbox data model definition

=============================================================================
Belongs to Module: ChatModule
=============================================================================

Inbox is the "send message" capability of ChatModule:
- ChatModule is not just about "receiving messages" (user sends messages to Agent)
- It also includes "sending messages" (Agent proactively sends messages to user)
- Inbox is the "mailbox" for messages sent by the Agent to the user

Message sources:
- Job execution result notifications (JobModule calls ChatModule's inbox capability)
- System notifications (reserved)
- Agent proactive messages (reserved)

Use cases:
1. Display inbox in the Streamlit frontend based on user_id
2. Write results to Inbox after Job execution completes
3. Users can view and mark as read

Related files:
- schema: xyz_agent_context/schema/inbox_schema.py (this file)
- TableManager: xyz_agent_context/utils/database_table_management/create_chat_table.py
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================

class InboxMessageType(str, Enum):
    """Inbox message type"""
    JOB_RESULT = "job_result"      # Job execution result
    SYSTEM_NOTICE = "system"       # System notification (reserved)
    AGENT_MESSAGE = "agent"        # Agent proactive message (reserved)
    CHANNEL_MESSAGE = "channel_message"  # IM channel message (Lark, Slack, etc.)


# =============================================================================
# Message Source
# =============================================================================

class MessageSource(BaseModel):
    """
    Message source

    Generic source identifier for tracing the origin of a message.

    Examples:
        # Job execution result
        MessageSource(type="job", id="job_abc123")

        # Narrative/Event message
        MessageSource(type="narrative", id="event_xyz789")

        # System notification
        MessageSource(type="system", id="sys_notice_001")
    """

    type: str = Field(
        ...,
        description="Source type, e.g., 'job', 'narrative', 'system'"
    )

    id: str = Field(
        ...,
        description="Source ID, e.g., job_id, event_id, etc."
    )


# =============================================================================
# Inbox Message Model
# =============================================================================

class InboxMessage(BaseModel):
    """
    Inbox message model

    Core functionality:
    1. Stores messages from various sources for users to view in Streamlit
    2. Associates sources (Job/Narrative/System) via the source field
    3. Supports read/unread status management
    """

    # === Database ID ===
    id: Optional[int] = Field(
        default=None,
        description="Database auto-increment ID"
    )

    # === Business Identifier ===
    message_id: str = Field(
        ...,
        max_length=64,
        description="Unique message identifier (UUID)"
    )

    # === Ownership ===
    user_id: str = Field(
        ...,
        max_length=64,
        description="Recipient user ID"
    )

    # === Source ===
    source: Optional[MessageSource] = Field(
        default=None,
        description="Message source, containing type and id fields"
    )

    event_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Associated execution event ID"
    )

    # === Content ===
    message_type: InboxMessageType = Field(
        ...,
        description="Message type"
    )

    title: str = Field(
        ...,
        max_length=255,
        description="Message title"
    )

    content: str = Field(
        ...,
        description="Message body (Agent's execution result)"
    )

    # === Status ===
    is_read: bool = Field(
        default=False,
        description="Whether read"
    )

    # === Time ===
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="Creation time"
    )
