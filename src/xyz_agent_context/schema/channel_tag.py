"""
@file_name: channel_tag.py
@author: Bin Liang
@date: 2026-03-10
@description: ChannelTag — unified trigger source identifier protocol

ChannelTag is the universal identifier for ALL trigger sources (Chat, Job, Matrix,
future Slack/Email, etc.). Every message entering AgentRuntime carries a ChannelTag,
and every Chat History / Narrative record stores one too.

ChannelTag is infrastructure — it does NOT belong to any Module. Lives in the shared
schema layer.

Where ChannelTag appears:
- Each Trigger constructs it when building AgentRuntime input ("where did this message come from")
- Chat History messages carry it for source distinction
- Narrative summaries retain source context via ChannelTag
- Social Network batch entity extraction reads ChannelTag to identify entities
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any


@dataclass
class ChannelTag:
    """
    Unified trigger source identifier.

    Producers: each Trigger creates a ChannelTag when feeding AgentRuntime.
    Consumers: Modules (Social Network, Narrative, etc.) read ChannelTag during processing.

    Attributes:
        channel: Trigger source type — "direct" / "job" / "matrix" / "slack" / "email"
        sender_name: Display name (username / agent name / job name)
        sender_id: Unique identifier within the trigger source
        room_id: Conversation identifier (optional, used by IM channels)
        room_name: Conversation display name (optional)
    """
    channel: str          # Trigger source type
    sender_name: str      # Display name
    sender_id: str        # Unique ID within the source
    room_id: str = ""     # Conversation ID (optional, for IM channels)
    room_name: str = ""   # Conversation name (optional)

    def format(self) -> str:
        """
        Format as a text tag for injection into Agent input.

        Examples:
            [Direct · Alice · user_alice]
            [Matrix · Research Agent · @research:matrix.example.com · !room123:matrix.example.com]
            [Job · Daily Report · job_daily_report_001]
        """
        parts = [self.channel.capitalize(), self.sender_name, self.sender_id]
        if self.room_id:
            parts.append(self.room_id)
        return f"[{' · '.join(parts)}]"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for persistence (Chat History, etc.)."""
        d = asdict(self)
        # Remove empty fields to keep storage compact
        return {k: v for k, v in d.items() if v}

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> ChannelTag:
        """Deserialize from dict."""
        return ChannelTag(
            channel=data.get("channel", "unknown"),
            sender_name=data.get("sender_name", "Unknown"),
            sender_id=data.get("sender_id", ""),
            room_id=data.get("room_id", ""),
            room_name=data.get("room_name", ""),
        )

    @staticmethod
    def parse(tag_str: str) -> Optional[ChannelTag]:
        """
        Parse a text tag back into a structured ChannelTag.

        Supported formats:
            [Channel · Name · ID]
            [Channel · Name · ID · RoomID]

        Args:
            tag_str: Text tag string

        Returns:
            ChannelTag instance, or None if parsing fails
        """
        match = re.match(r'^\[(.+)\]$', tag_str.strip())
        if not match:
            return None

        parts = [p.strip() for p in match.group(1).split('·')]
        if len(parts) < 3:
            return None

        return ChannelTag(
            channel=parts[0].lower(),
            sender_name=parts[1],
            sender_id=parts[2],
            room_id=parts[3] if len(parts) > 3 else "",
        )

    # === Factory methods for common trigger sources ===

    @staticmethod
    def direct(sender_name: str, sender_id: str) -> ChannelTag:
        """Create a ChannelTag for direct user conversation."""
        return ChannelTag(
            channel="direct",
            sender_name=sender_name,
            sender_id=sender_id,
        )

    @staticmethod
    def job(job_name: str, job_id: str) -> ChannelTag:
        """Create a ChannelTag for Job-triggered execution."""
        return ChannelTag(
            channel="job",
            sender_name=job_name,
            sender_id=job_id,
        )

    @staticmethod
    def matrix(
        sender_name: str,
        sender_id: str,
        room_id: str = "",
        room_name: str = "",
    ) -> ChannelTag:
        """Create a ChannelTag for Matrix message."""
        return ChannelTag(
            channel="matrix",
            sender_name=sender_name,
            sender_id=sender_id,
            room_id=room_id,
            room_name=room_name,
        )
