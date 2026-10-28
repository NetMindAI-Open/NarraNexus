"""
@file_name: channel_tag.py
@author: Bin Liang
@date: 2026-03-10
@description: ChannelTag — unified trigger source identifier protocol

ChannelTag is the universal identifier for ALL trigger sources (Chat, Job, Lark,
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


# Trailing segment appended by ``format()`` when the sender is another
# agent. A literal rather than a flag word so ``parse()`` can strip it
# unambiguously, and so it reads as English to the model.
AGENT_PEER_MARKER = "agent sender"


@dataclass
class ChannelTag:
    """
    Unified trigger source identifier.

    Producers: each Trigger creates a ChannelTag when feeding AgentRuntime.
    Consumers: Modules (Social Network, Narrative, etc.) read ChannelTag during processing.

    Attributes:
        channel: Trigger source type — "direct" / "job" / "lark" / "slack" / "email"
        sender_name: Display name (username / agent name / job name)
        sender_id: Unique identifier within the trigger source
        room_id: Conversation identifier (optional, used by IM channels)
        room_name: Conversation display name (optional)
        is_agent_peer: the sender is another agent, not a human
    """
    channel: str          # Trigger source type
    sender_name: str      # Display name
    sender_id: str        # Unique ID within the source
    room_id: str = ""     # Conversation ID (optional, for IM channels)
    room_name: str = ""   # Conversation name (optional)
    # Is the far side a machine? Filled from
    # ``ChannelTriggerBase.is_agent_peer``. Carried HERE rather than
    # re-derived downstream because only the trigger layer knows the
    # platform's identity convention, while the places that need the answer
    # (the prompt, and later the reply-decision layer) sit well above the
    # ParsedMessage and cannot reach it.
    #
    # Defaults False: on most channels every sender is a person, and
    # ``to_dict`` drops the field when it is, so existing serialised tags
    # are unchanged.
    is_agent_peer: bool = False

    def format(self) -> str:
        """
        Format as a text tag for injection into Agent input.

        Examples:
            [Direct · Alice · user_alice]
            [Lark · Research Agent · ou_research_open_id · oc_room_id_123]
            [Job · Daily Report · job_daily_report_001]
            [Narramessenger · Liam · @agent-x:h · !room · agent sender]

        The trailing ``agent sender`` marker is how the MODEL learns the
        far side is a machine — the DM Communication Protocol's
        "Breaking a Loop" section leans on that, and until now it could
        only guess from how the messages read.

        Appended only when true, so the overwhelming majority of tags are
        byte-identical to before (they also land in chat history, and a
        format change there would make old and new turns disagree).
        """
        parts = [self.channel.capitalize(), self.sender_name, self.sender_id]
        if self.room_id:
            parts.append(self.room_id)
        if self.is_agent_peer:
            parts.append(AGENT_PEER_MARKER)
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
            is_agent_peer=bool(data.get("is_agent_peer", False)),
        )

    @staticmethod
    def parse(tag_str: str) -> Optional[ChannelTag]:
        """
        Parse a text tag back into a structured ChannelTag.

        Supported formats:
            [Channel · Name · ID]
            [Channel · Name · ID · RoomID]
            [Channel · Name · ID · agent sender]
            [Channel · Name · ID · RoomID · agent sender]

        The trailing marker is stripped before the positional read, so a
        room-less agent tag does not parse "agent sender" as the room id.

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

        # Strip the agent marker before positional reading — otherwise a
        # room-less agent tag would parse "agent sender" as the room_id.
        is_agent_peer = bool(parts and parts[-1] == AGENT_PEER_MARKER)
        if is_agent_peer:
            parts = parts[:-1]
        if len(parts) < 3:
            return None

        return ChannelTag(
            channel=parts[0].lower(),
            sender_name=parts[1],
            sender_id=parts[2],
            room_id=parts[3] if len(parts) > 3 else "",
            is_agent_peer=is_agent_peer,
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
    def lark(
        sender_name: str,
        sender_id: str,
        chat_id: str = "",
        chat_name: str = "",
        is_agent_peer: bool = False,
    ) -> ChannelTag:
        """Create a ChannelTag for Lark/Feishu message."""
        return ChannelTag(
            channel="lark",
            sender_name=sender_name,
            sender_id=sender_id,
            room_id=chat_id,
            room_name=chat_name,
            is_agent_peer=is_agent_peer,
        )
