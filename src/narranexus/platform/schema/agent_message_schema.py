"""
@file_name: agent_message_schema.py
@author: NetMind.AI
@date: 2025-12-10
@description: Agent Message Schema - Agent message list data model definition

=============================================================================
Belongs to Module: ChatModule / Agent Runtime
=============================================================================

Used to store each Agent's message list, recording all messages in chronological order.

Message source types (source_type):
- user: Messages sent by the user
- agent: Messages sent by the Agent
- system: System messages

Field descriptions:
- source_type: Message source type
- source_id: Source ID (agent_id, user_id, or "system")
- content: Message content
- if_response: Whether it has been replied to
- narrative_id: Associated narrative ID (populated after Agent reply)
- event_id: Associated event ID (populated after Agent reply)

Related files:
- schema: narranexus/platform/schema/agent_message_schema.py (this file)
- TableManager: narranexus/platform/utils/database_table_management/create_agent_message_table.py
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================

class MessageSourceType(str, Enum):
    """Message source type"""
    USER = "user"       # Message sent by the user
    AGENT = "agent"     # Message sent by the Agent
    SYSTEM = "system"   # System message


# =============================================================================
# Agent Message Model
# =============================================================================

class AgentMessage(BaseModel):
    """
    Agent message model

    Stores each Agent's message list, recorded in chronological order.
    On initialization, narrative_id and event_id are empty,
    and are populated after the Agent replies.
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
    agent_id: str = Field(
        ...,
        max_length=64,
        description="Agent ID (the Agent this message belongs to)"
    )

    # === Source Information ===
    source_type: MessageSourceType = Field(
        ...,
        description="Message source type: user/agent/system"
    )

    source_id: str = Field(
        ...,
        max_length=128,
        description="Source ID (agent_id, user_id, or 'system')"
    )

    # === Content ===
    content: str = Field(
        ...,
        description="Message content"
    )

    # === Status ===
    if_response: bool = Field(
        default=False,
        description="Whether it has been replied to"
    )

    # === Associated Information (populated after Agent reply) ===
    narrative_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Associated narrative ID (populated after Agent reply)"
    )

    event_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Associated event ID (populated after Agent reply)"
    )

    # === Time ===
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="Message creation time"
    )
