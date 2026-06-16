"""
Prompt building implementation

@file_name: prompt_builder.py
@author: NetMind.AI
@date: 2025-12-22
@description: Narrative Prompt assembly
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Narrative, NarrativeType, NarrativeActorType
from .prompts import (
    NARRATIVE_TYPE_CHAT_PROMPT,
    NARRATIVE_TYPE_TASK_PROMPT,
    NARRATIVE_TYPE_GENERAL_PROMPT,
    ACTOR_TYPE_USER_DESCRIPTION,
    ACTOR_TYPE_AGENT_DESCRIPTION,
    ACTOR_TYPE_PARTICIPANT_DESCRIPTION,
    ACTOR_TYPE_SYSTEM_DESCRIPTION,
    NARRATIVE_MAIN_PROMPT_TEMPLATE,
)

if TYPE_CHECKING:
    pass


class PromptBuilder:
    """
    Prompt Builder

    Responsibilities:
    - Convert Narrative into a structured Prompt
    - Assemble context required for Agent reasoning
    """

    @staticmethod
    async def build_main_prompt(narrative: Narrative) -> str:
        """
        Generate the main Prompt for a Narrative

        Converts a Narrative object into structured Prompt text.

        Args:
            narrative: Narrative object

        Returns:
            Formatted Narrative Prompt
        """
        # Type description
        if narrative.type == NarrativeType.CHAT:
            type_prompt = NARRATIVE_TYPE_CHAT_PROMPT
        elif narrative.type == NarrativeType.TASK:
            type_prompt = NARRATIVE_TYPE_TASK_PROMPT
        else:
            type_prompt = NARRATIVE_TYPE_GENERAL_PROMPT

        # Actor description (2026-01-21 P2: Added PARTICIPANT type description)
        actor_type_map = {
            NarrativeActorType.USER: ACTOR_TYPE_USER_DESCRIPTION,
            NarrativeActorType.AGENT: ACTOR_TYPE_AGENT_DESCRIPTION,
            NarrativeActorType.PARTICIPANT: ACTOR_TYPE_PARTICIPANT_DESCRIPTION,
        }
        # Resolve human actors (USER / PARTICIPANT) to their display name —
        # actor.id for those is a user_id (an opaque NetMind userSystemCode in
        # cloud mode), which must not be shown to the LLM as a person. AGENT /
        # SYSTEM actor ids are agent_id / system keys and stay as-is.
        from xyz_agent_context.utils.db_factory import get_db_client
        from xyz_agent_context.repository import UserRepository
        _repo = UserRepository(await get_db_client())
        _human_actor_types = (NarrativeActorType.USER, NarrativeActorType.PARTICIPANT)
        actor_prompt = ""
        for actor in narrative.narrative_info.actors:
            actor_type_description = actor_type_map.get(actor.type, ACTOR_TYPE_SYSTEM_DESCRIPTION)
            label = (
                await _repo.get_display_name(actor.id)
                if actor.type in _human_actor_types
                else actor.id
            )
            actor_prompt += f"\n\t- {label} ({actor.type.value}): {actor_type_description}"

        # Assemble Prompt
        narrative_prompt = NARRATIVE_MAIN_PROMPT_TEMPLATE.format(
            narrative_id=narrative.id,
            type_prompt=type_prompt,
            created_at=narrative.created_at,
            updated_at=narrative.updated_at,
            name=narrative.narrative_info.name,
            description=narrative.narrative_info.description,
            current_summary=narrative.narrative_info.current_summary,
            actor_prompt=actor_prompt,
        )
        return narrative_prompt

    @staticmethod
    async def build_summary_prompt(narrative: Narrative) -> str:
        """
        Generate a Narrative summary Prompt

        Args:
            narrative: Narrative object

        Returns:
            Summary Prompt
        """
        summary_parts = []

        # Basic information
        summary_parts.append(f"Narrative: {narrative.narrative_info.name}")

        # Topic hint
        if narrative.topic_hint:
            summary_parts.append(f"Topic: {narrative.topic_hint}")

        # Keywords
        if narrative.topic_keywords:
            summary_parts.append(f"Keywords: {', '.join(narrative.topic_keywords)}")

        # Dynamic summary (last 3 entries)
        if narrative.dynamic_summary:
            recent_summaries = narrative.dynamic_summary[-3:]
            for entry in recent_summaries:
                summary_parts.append(f"- {entry.summary[:100]}")

        return "\n".join(summary_parts)
