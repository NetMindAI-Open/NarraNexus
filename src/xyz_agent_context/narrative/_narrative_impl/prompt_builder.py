"""
Prompt building implementation

@file_name: prompt_builder.py
@author: NetMind.AI
@date: 2025-12-22
@description: Narrative Prompt assembly
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

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
    NARRATIVE_STABLE_PROMPT_TEMPLATE,
    NARRATIVE_TURN_PROMPT_TEMPLATE,
)

if TYPE_CHECKING:
    pass


def _canonical_timestamp(value: Any) -> str:
    """Render a narrative timestamp in ONE canonical byte form (R4c).

    The same wall-clock instant used to reach the prompt through two paths
    that serialized differently: a freshly created in-memory narrative
    carries a tz-aware datetime WITH microseconds, while a DB-round-tripped
    narrative comes back at second precision (naive or tz-aware, depending
    on backend/driver). ``str()`` rendered those as different bytes
    ("...:39.367468+00:00" vs "...:39+00:00"), breaking the cacheable
    system-prompt prefix ~1.2K chars in (experiment E2, 2026-07-25).

    Canonical form: UTC, second precision, explicit " UTC" suffix —
    e.g. "2026-07-25 20:08:39 UTC". Naive datetimes are treated as UTC
    (every writer in this codebase stores UTC). Non-datetime input falls
    back to ``str()`` (defensive only; the model types these as datetime).
    """
    if isinstance(value, datetime):
        dt = value if value.tzinfo is None else value.astimezone(timezone.utc)
        return f"{dt.replace(microsecond=0):%Y-%m-%d %H:%M:%S} UTC"
    return str(value)


class PromptBuilder:
    """
    Prompt Builder

    Responsibilities:
    - Convert Narrative into a structured Prompt
    - Assemble context required for Agent reasoning
    """

    @staticmethod
    async def build_main_prompt(
        narrative: Narrative,
        include_volatile: bool = True,
    ) -> str:
        """
        Generate the main Prompt for a Narrative

        Converts a Narrative object into structured Prompt text.

        Args:
            narrative: Narrative object
            include_volatile: True renders the full template including the
                per-turn volatile fields (name / created_at / updated_at /
                current_summary) — the pre-R4 layout, used when
                turn-context relocation is disabled. False renders the
                byte-stable half only; the volatile fields then travel via
                build_turn_prompt() in the current message's [Turn context]
                block.

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
        from xyz_agent_context.utils.db.db_factory import get_db_client
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

        # Assemble Prompt. Timestamps go through ONE canonical formatter so
        # in-memory and DB-round-tripped narratives render byte-identically
        # (R4c; the stable half is a cacheable prefix).
        if include_volatile:
            narrative_prompt = NARRATIVE_MAIN_PROMPT_TEMPLATE.format(
                narrative_id=narrative.id,
                type_prompt=type_prompt,
                created_at=_canonical_timestamp(narrative.created_at),
                updated_at=_canonical_timestamp(narrative.updated_at),
                name=narrative.narrative_info.name,
                description=narrative.narrative_info.description,
                current_summary=narrative.narrative_info.current_summary,
                actor_prompt=actor_prompt,
            )
        else:
            # No timestamp is rendered here (R4d): the stable half is the
            # cacheable prefix and created_at has two independent clock
            # sources (see prompts.NARRATIVE_STABLE_PROMPT_TEMPLATE), so it
            # travels in the turn block together with updated_at.
            narrative_prompt = NARRATIVE_STABLE_PROMPT_TEMPLATE.format(
                narrative_id=narrative.id,
                type_prompt=type_prompt,
                description=narrative.narrative_info.description,
                actor_prompt=actor_prompt,
            )
        return narrative_prompt

    @staticmethod
    async def build_turn_prompt(narrative: Narrative) -> str:
        """
        Generate the per-turn volatile Narrative block (R4 relocation).

        Carries every field whose rendered bytes are not guaranteed stable
        across turns (name, created_at, updated_at, current_summary) —
        rendered into the [Turn context] block of the current user message
        instead of the system prompt, so the stable half built by
        build_main_prompt(include_volatile=False) stays byte-identical
        across turns. Name rides here (R4c) because the narrative updater
        rewrites it on every LLM update; created_at rides here (R4d)
        because its VALUE has two clock sources (DB default vs the Python
        timestamp captured in crud.create) — see the template comments in
        prompts.py for the full rationale.

        Args:
            narrative: Narrative object

        Returns:
            Formatted per-turn Narrative state block
        """
        return NARRATIVE_TURN_PROMPT_TEMPLATE.format(
            name=narrative.narrative_info.name,
            created_at=_canonical_timestamp(narrative.created_at),
            updated_at=_canonical_timestamp(narrative.updated_at),
            current_summary=narrative.narrative_info.current_summary,
        )

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
