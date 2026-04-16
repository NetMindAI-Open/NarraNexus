"""
@file_name: channel_context_builder_base.py
@author: Bin Liang
@date: 2026-03-10
@description: Abstract base class for IM channel message prompt construction

Defines a standard sectioned assembly flow (Template Method pattern).
Subclasses only need to implement data-fetching methods.
Chat and Job do NOT use this base class — they have their own prompt logic.

Standard sections:
1. Message metadata (always included)
2. Sender profile (via Repository layer entity lookup)
3. Conversation history (configurable)
4. Current message body
5. Room members (for group conversations)
6. Action instructions
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger

from .channel_prompts import (
    CHANNEL_MESSAGE_EXECUTION_TEMPLATE,
    SENDER_PROFILE_FROM_ENTITY_TEMPLATE,
    SENDER_PROFILE_UNKNOWN_TEMPLATE,
    CONVERSATION_HISTORY_TEMPLATE,
    ROOM_MEMBERS_TEMPLATE,
)


@dataclass
class ChannelHistoryConfig:
    """
    Channel conversation history configuration.

    Shared config for all channel modules controlling whether and how
    conversation history is loaded.

    Attributes:
        load_conversation_history: Whether to load conversation history
        history_limit: Max number of recent messages to load
        history_max_chars: Max total chars for history text (older messages truncated)
    """
    load_conversation_history: bool = True
    history_limit: int = 20
    history_max_chars: int = 20000


class ChannelContextBuilderBase(ABC):
    """
    Abstract base for IM channel prompt construction.

    Uses Template Method pattern:
    - build_prompt() defines the standard sectioned assembly flow
    - Subclasses implement get_message_info(), get_conversation_history(), get_room_members()
    - Subclasses may override get_sender_extra_profile() for channel-specific info
    """

    @abstractmethod
    async def get_message_info(self) -> Dict[str, Any]:
        """
        Return message metadata.

        Must include these keys:
            channel_display_name: str  — e.g. "Lark"
            channel_key: str           — e.g. "lark"
            room_name: str
            room_id: str
            room_type: str             — "Direct Message" or "Group Room"
            sender_display_name: str
            sender_id: str
            timestamp: str
            my_channel_id: str         — this agent's ID on the channel
            message_body: str          — current message content
            send_tool_name: str        — MCP tool name for replying
        """
        ...

    @abstractmethod
    async def get_conversation_history(self, limit: int) -> List[Dict[str, Any]]:
        """
        Return recent conversation history for this room.

        Each message should contain: sender, timestamp, body

        Args:
            limit: Max number of messages to return

        Returns:
            List of messages in chronological order
        """
        ...

    @abstractmethod
    async def get_room_members(self) -> List[Dict[str, Any]]:
        """
        Return room member list.

        Each member should contain: user_id, display_name

        Returns:
            List of members
        """
        ...

    async def get_sender_extra_profile(self) -> str:
        """
        Channel-specific extra sender profile info.

        Returns empty string by default. Subclasses may override.
        E.g. LarkModule may fetch Bot capabilities.

        Returns:
            Additional sender profile text
        """
        return ""

    async def get_sender_entity(
        self, agent_id: str, channel_key: str, sender_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Look up sender's entity info from Social Network Repository.

        Queries via the Repository layer — does NOT import SocialNetworkModule.

        Args:
            agent_id: Current Agent ID
            channel_key: Channel identifier
            sender_id: Sender's ID

        Returns:
            Entity info dict, or None if not found
        """
        # Default: no lookup. Subclasses override as needed.
        return None

    async def build_prompt(self, config: ChannelHistoryConfig) -> str:
        """
        Standard sectioned assembly flow (Template Method).

        1. Message metadata (always included)
        2. Sender profile (entity lookup + channel-specific extras)
        3. Conversation history (configurable)
        4. Current message body
        5. Room members (shown for group conversations)
        6. Action instructions

        Args:
            config: History loading configuration

        Returns:
            Fully assembled prompt text
        """
        # Step 1: Message metadata
        info = await self.get_message_info()

        # Step 2: Sender profile
        sender_profile_section = await self._build_sender_profile(info)

        # Step 3: Conversation history (configurable)
        conversation_history_section = ""
        if config.load_conversation_history:
            conversation_history_section = await self._build_history_section(info, config)

        # Step 4-5: Room members
        room_members_section = await self._build_members_section(info)

        # Step 6: Assemble full template
        # TODO [Narrative Continuity Coupling]:
        # The output of this method becomes the AgentRuntime input_content and is
        # stored as session.last_query. A counterpart function `_extract_core_content()`
        # in `narrative/_narrative_impl/continuity.py` strips this template to extract
        # the core message body for topic continuity detection.
        # If you change CHANNEL_MESSAGE_EXECUTION_TEMPLATE or the section assembly
        # (especially Conversation History format with `[timestamp] @sender:` lines),
        # you MUST update `_extract_core_content()` accordingly.
        return CHANNEL_MESSAGE_EXECUTION_TEMPLATE.format(
            **info,
            sender_profile_section=sender_profile_section,
            conversation_history_section=conversation_history_section,
            room_members_section=room_members_section,
        )

    # === Internal methods ===

    async def _build_sender_profile(self, info: Dict[str, Any]) -> str:
        """Build sender profile section."""
        profile = ""

        # Shared part: look up Social Network entity
        entity = await self.get_sender_entity(
            agent_id=info.get("agent_id", ""),
            channel_key=info.get("channel_key", ""),
            sender_id=info.get("sender_id", ""),
        )

        if entity:
            profile = SENDER_PROFILE_FROM_ENTITY_TEMPLATE.format(
                name=entity.get("entity_name", "Unknown"),
                description=entity.get("entity_description", "N/A"),
                tags=", ".join(entity.get("tags", [])) or "None",
                entity_summary=entity.get("entity_description", "")[:200],
            )
        else:
            profile = SENDER_PROFILE_UNKNOWN_TEMPLATE.format(
                sender_display_name=info.get("sender_display_name", "Unknown"),
            )

        # Channel-specific extras
        extra = await self.get_sender_extra_profile()
        if extra:
            profile += "\n" + extra

        return profile

    async def _build_history_section(
        self, info: Dict[str, Any], config: ChannelHistoryConfig
    ) -> str:
        """Build conversation history section."""
        try:
            messages = await self.get_conversation_history(limit=config.history_limit)
            if not messages:
                return ""

            formatted = self._format_messages(
                messages,
                my_id=info.get("my_channel_id", ""),
                max_chars=config.history_max_chars,
            )

            return CONVERSATION_HISTORY_TEMPLATE.format(
                room_name=info.get("room_name", "Unknown"),
                n=len(messages),
                formatted_messages=formatted,
            )
        except Exception as e:
            logger.warning(f"Failed to load conversation history: {e}")
            return ""

    async def _build_members_section(self, info: Dict[str, Any]) -> str:
        """Build room members section (shown for group conversations)."""
        try:
            members = await self.get_room_members()
            if not members or len(members) <= 2:
                # 1:1 DM — no need to show members
                return ""

            lines = []
            for m in members:
                uid = m.get("user_id", "")
                name = m.get("display_name", uid)
                suffix = ""
                if uid == info.get("my_channel_id"):
                    suffix = " — You"
                elif uid == info.get("sender_id"):
                    suffix = " — Sender"
                lines.append(f"- {uid} ({name}){suffix}")

            return ROOM_MEMBERS_TEMPLATE.format(member_list="\n".join(lines))
        except Exception as e:
            logger.warning(f"Failed to load room members: {e}")
            return ""

    @staticmethod
    def _format_messages(
        messages: List[Dict[str, Any]],
        my_id: str,
        max_chars: int,
    ) -> str:
        """
        Format a list of chat history messages.

        The last message is marked with ▶ (the one to respond to).

        Args:
            messages: Message list
            my_id: This agent's channel ID
            max_chars: Max total characters

        Returns:
            Formatted text
        """
        lines = []
        total_chars = 0

        for i, msg in enumerate(messages):
            ts = msg.get("timestamp", "")
            sender = msg.get("sender", "unknown")
            body = msg.get("body", "")

            # Mark own messages
            sender_label = f"{sender} (You)" if sender == my_id else sender

            # Mark the last message with ▶
            prefix = "▶ " if i == len(messages) - 1 else "  "

            line = f"{prefix}[{ts}] {sender_label}:\n    {body}"

            # Check character limit
            total_chars += len(line)
            if total_chars > max_chars and i < len(messages) - 1:
                # Never truncate the last message
                lines.insert(0, "  ... (earlier messages truncated)")
                break

            lines.append(line)

        return "\n\n".join(lines)
