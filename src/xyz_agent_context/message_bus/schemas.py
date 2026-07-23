"""
@file_name: schemas.py
@author: NarraNexus
@date: 2026-04-02
@description: Pydantic data models for the MessageBus service

Defines the core data structures used across all MessageBus implementations:
- BusMessage: A message sent within a channel
- BusChannel: A communication channel (group or direct)
- BusChannelMember: Channel membership with read/processed cursors
- BusAgentInfo: Agent registration and discovery metadata
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional, Union

from pydantic import BaseModel


# Timestamps can be str (raw from SQLite before auto-parse) or datetime (after auto-parse)
Timestamp = Union[str, datetime]


class BusMessage(BaseModel):
    """A message sent within a MessageBus channel."""

    model_config = {"arbitrary_types_allowed": True}

    message_id: str
    channel_id: str
    from_agent: str
    content: str
    msg_type: str = "text"
    mentions: Optional[List[str]] = None  # list of agent_ids or ["@everyone"]
    # Bus-attachment dicts (file_id/original_name/mime_type/size_bytes/category/
    # rel_path). Populated when the sender attaches files; None for text-only.
    # The trigger renders these into Read-tool markers at delivery time.
    attachments: Optional[List[dict]] = None
    created_at: Any = None


class BusChannel(BaseModel):
    """A communication channel in the MessageBus."""

    model_config = {"arbitrary_types_allowed": True}

    channel_id: str
    name: str
    channel_type: str = "group"
    created_by: str
    created_at: Any = None


class BusChannelMember(BaseModel):
    """Channel membership record with read and processed cursors."""

    model_config = {"arbitrary_types_allowed": True}

    channel_id: str
    agent_id: str
    joined_at: Any = None
    last_read_at: Any = None
    last_processed_at: Any = None


class BusAgentInfo(BaseModel):
    """Agent registration and discovery metadata."""

    model_config = {"arbitrary_types_allowed": True}

    agent_id: str
    owner_user_id: str
    capabilities: List[str] = []
    description: str = ""
    visibility: str = "private"
    registered_at: Any = None
    last_seen_at: Any = None
