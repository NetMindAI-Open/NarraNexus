"""
Narrative update implementation

@file_name: updater.py
@author: NetMind.AI
@date: 2025-12-22
@description: Narrative update + LLM dynamic summary generation

Features:
1. update_with_event: Update Narrative with an Event
2. LLM dynamic update: Asynchronously update name, current_summary, actors, topic_keywords
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field
from loguru import logger

from ..config import config
from xyz_agent_context.config import NARRATIVE_LLM_UPDATE_INTERVAL
from ..models import (
    DynamicSummaryEntry,
    Event,
    Narrative,
    NarrativeActor,
    NarrativeActorType,
)
from .crud import NarrativeCRUD
from .prompts import NARRATIVE_UPDATE_INSTRUCTIONS

# Use common utilities from utils

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


# ============================================================================
# LLM Output Schema
# ============================================================================

class ActorOutput(BaseModel):
    """Actor output"""
    name: str = Field(description="Actor name")
    actor_type: str = Field(description="Type: user, agent, system, tool")


class NarrativeUpdateOutput(BaseModel):
    """
    LLM 生成的 Narrative 更新内容

    用于随着对话演进动态更新 Narrative 元数据。
    """
    name: str = Field(
        description="Short name for the Narrative (3-8 words), the core topic"
    )
    current_summary: str = Field(
        description=(
            "Structured fact sheet in bullet format. "
            "Format: 'Topic: ...\\nKey facts:\\n- fact1\\n- fact2\\n...\\nStatus: ...' "
            "Max 8-12 bullets. No paragraphs, no filler. Just atomic facts."
        )
    )
    topic_keywords: List[str] = Field(
        default_factory=list,
        description="Concrete topic keywords (5-10 items) for retrieval matching"
    )
    actors: List[ActorOutput] = Field(
        default_factory=list,
        description="Participants: users, Agents, and important named entities mentioned"
    )
    dynamic_summary_entry: str = Field(
        default="",
        description="One short sentence summarizing this turn, e.g. 'User requested X; Agent did Y.'"
    )


class NarrativeUpdater:
    """
    Narrative Updater

    Responsibilities:
    - Update Narrative with Events
    - Regenerate topic hints
    """

    def __init__(self, agent_id: str):
        """
        Initialize updater

        Args:
            agent_id: Agent ID
        """
        self.agent_id = agent_id
        self._crud = NarrativeCRUD(agent_id)
        self._event_service = None  # Dependency injection

    def set_database_client(self, db_client: "AsyncDatabaseClient"):
        """Set the database client"""
        self._crud.set_database_client(db_client)

    def set_event_service(self, event_service):
        """Inject EventService"""
        self._event_service = event_service

    async def update_with_event(
        self,
        narrative: Narrative,
        event: Event,
        is_main_narrative: bool = True,
        is_default_narrative: bool = False
    ) -> Narrative:
        """
        Update Narrative with an Event

        Features:
        - Associate Event ID
        - Update dynamic summary (temporary)
        - Asynchronously trigger LLM update (main_narrative only)

        Args:
            narrative: Narrative object
            event: Event object
            is_main_narrative: Whether this is the main Narrative
                - True: Full update, including async LLM dynamic update
                - False: Basic update only (associate Event, update dynamic_summary)
                  Note: Auxiliary Narrative LLM updates require different prompts,
                  as they provide supplementary information with a different summarization perspective.
                  TODO: Implement dedicated update logic for auxiliary Narratives in the future
            is_default_narrative: Whether this is a default Narrative (is_special="default")
                - True: Only add event_id, no other updates
                - False: Normal update

        Returns:
            Updated Narrative
        """
        logger.debug(f"update_with_event: narrative={narrative.id}, event={event.id}, is_default={is_default_narrative}")

        # [Fix] Reload the latest Narrative from database to avoid overwriting concurrent modifications (e.g., PARTICIPANT)
        # This is because the passed-in narrative object may be a stale version loaded at the start of the flow
        latest_narrative = await self._crud.load_by_id(narrative.id)
        if not latest_narrative:
            logger.warning(f"Narrative {narrative.id} not found in database, skipping update_with_event")
            return narrative

        # Default Narrative: Only add event_id, no other updates
        if is_default_narrative:
            logger.info(f"Default Narrative only adding event_id: {latest_narrative.id}")

            # Add event_id
            if event.id not in latest_narrative.event_ids:
                latest_narrative.event_ids.append(event.id)

            # Update timestamp
            latest_narrative.updated_at = datetime.now(timezone.utc)

            # Save
            await self._crud.save(latest_narrative)

            logger.debug(f"Default Narrative update completed: {latest_narrative.id} (only added event_id)")
            return latest_narrative

        # Non-default Narrative: Normal update flow
        # Add event_id
        if event.id not in latest_narrative.event_ids:
            latest_narrative.event_ids.append(event.id)

        # Temporary dynamic_summary update (waiting for LLM to generate a better version)
        if event.final_output:
            summary_entry = DynamicSummaryEntry(
                event_id=event.id,
                summary=event.final_output[:200],
                timestamp=event.updated_at,
                references=[],
            )
            latest_narrative.dynamic_summary.append(summary_entry)

        # Update timestamp
        latest_narrative.updated_at = datetime.now(timezone.utc)

        # Save basic updates
        await self._crud.save(latest_narrative)

        # EverMemOS write has been migrated to MemoryModule.hook_after_event_execution()
        # See docs/MEMORY_MODULE_REFACTOR.md

        # Update the passed-in object reference so subsequent code uses the latest data
        narrative = latest_narrative

        # Determine whether to trigger LLM update (async execution, non-blocking)
        # Note: Only main_narrative triggers the async LLM update
        # Auxiliary Narratives only get basic updates for now; dedicated update logic can be implemented in the future
        if is_main_narrative:
            event_count = len(narrative.event_ids)
            update_interval = NARRATIVE_LLM_UPDATE_INTERVAL

            if update_interval > 0 and event_count % update_interval == 0:
                logger.info(f"Triggering Narrative LLM update: {narrative.id} (event_count={event_count})")
                # Async execution, non-blocking main flow.
                asyncio.create_task(
                    self._async_llm_update(narrative, event)
                )
        else:
            # Auxiliary Narrative: Only record basic info, skip LLM update
            # TODO: Implement dedicated update logic for auxiliary Narratives in the future
            # Auxiliary Narratives have a different summarization perspective than main_narrative, requiring different prompts
            logger.debug(f"Skipping LLM update for auxiliary Narrative: {narrative.id}")

        return narrative

    # _async_evermemos_write has been migrated to MemoryModule.hook_after_event_execution()
    # See docs/MEMORY_MODULE_REFACTOR.md

    async def _async_llm_update(
        self,
        narrative: Narrative,
        event: Event,
    ) -> None:
        """
        Asynchronously update Narrative metadata using LLM

        Updated content:
        - narrative_info.name
        - narrative_info.current_summary
        - narrative_info.actors
        - topic_keywords
        - dynamic_summary (last entry)

        Args:
            narrative: Narrative object
            event: Latest Event object
        """
        try:
            logger.info(f"Starting LLM update for Narrative: {narrative.id}")

            # Build context: recent conversation history
            context = await self._build_update_context(narrative, event)

            # Call LLM to generate update content
            update_output = await self._call_llm_for_update(narrative, context)

            if update_output:
                # Apply updates
                await self._apply_llm_update(narrative, update_output, event)
                logger.info(f"LLM Narrative update completed: {narrative.id}")
            else:
                logger.warning(f"LLM update failed, skipping: {narrative.id}")

        except Exception as e:
            logger.exception(f"LLM Narrative update exception: {narrative.id}, error={e}")

    async def _build_update_context(self, narrative: Narrative, event: Event) -> str:
        """Build context for LLM update"""
        context_parts = []

        # Current Narrative information
        context_parts.append("## Current Narrative Information")
        context_parts.append(f"- Name: {narrative.narrative_info.name}")
        context_parts.append(f"- Description: {narrative.narrative_info.description}")
        context_parts.append(f"- Current Summary: {narrative.narrative_info.current_summary}")
        context_parts.append(f"- Keywords: {', '.join(narrative.topic_keywords or [])}")
        context_parts.append("")

        # Recent conversation history
        context_parts.append("## Recent Conversation History")

        # Get recent summaries from dynamic_summary
        recent_count = config.NARRATIVE_LLM_UPDATE_EVENTS_COUNT
        recent_summaries = narrative.dynamic_summary[-recent_count:]
        for i, entry in enumerate(recent_summaries):
            context_parts.append(f"{i+1}. {entry.summary}")

        context_parts.append("")

        # Latest Event details
        context_parts.append("## Latest Conversation")
        if event.env_context:
            user_input = event.env_context.get("input", "")
            if user_input:
                context_parts.append(f"User Input: {user_input}")
        if event.final_output:
            context_parts.append(f"Agent Response: {event.final_output[:500]}")

        return "\n".join(context_parts)

    async def _call_llm_for_update(
        self,
        narrative: Narrative,
        context: str
    ) -> Optional[NarrativeUpdateOutput]:
        """Call LLM to generate Narrative update content"""
        try:
            from xyz_agent_context.agent_framework.helper_sdk import get_helper_sdk

            instructions = NARRATIVE_UPDATE_INSTRUCTIONS

            from xyz_agent_context.narrative.config import config as narrative_config
            sdk = get_helper_sdk()
            result = await sdk.llm_function(
                instructions=instructions,
                user_input=context,
                output_type=NarrativeUpdateOutput,
                model=narrative_config.NARRATIVE_LLM_UPDATE_MODEL,
                reasoning_effort=narrative_config.NARRATIVE_LLM_UPDATE_REASONING_EFFORT or None,
            )

            return result.final_output

        except Exception as e:
            logger.exception(f"LLM call failed: {e}")
            return None

    async def _apply_llm_update(
        self,
        narrative: Narrative,
        update_output: NarrativeUpdateOutput,
        event: Event
    ) -> None:
        """
        Apply LLM-generated updates

        [Important] To avoid lost update issues, reload the latest Narrative from database first,
        then only update LLM-generated fields, preserving the latest actors and active_instances from the database.
        This is because during async execution, other processes may have already modified actors (e.g., adding PARTICIPANT).
        """
        # [Fix] Reload the latest Narrative from database to avoid overwriting other concurrent modifications
        latest_narrative = await self._crud.load_by_id(narrative.id)
        if not latest_narrative:
            logger.warning(f"Narrative {narrative.id} not found in database, skipping LLM update")
            return

        # Update narrative_info (only update name and current_summary, preserve actors)
        latest_narrative.narrative_info.name = update_output.name
        latest_narrative.narrative_info.current_summary = update_output.current_summary
        # Note: Do not update actors, preserve the latest actors from database (including PARTICIPANT)

        # Update topic_keywords
        latest_narrative.topic_keywords = update_output.topic_keywords

        # Update the last dynamic_summary entry
        if latest_narrative.dynamic_summary and update_output.dynamic_summary_entry:
            latest_narrative.dynamic_summary[-1].summary = update_output.dynamic_summary_entry

        # Update timestamp
        latest_narrative.updated_at = datetime.now(timezone.utc)

        # Save to database
        await self._crud.save(latest_narrative)

        logger.debug(
            f"LLM update applied: name={update_output.name}, "
            f"keywords={update_output.topic_keywords}"
        )

    # Embedding-update machinery removed (unified-memory refactor, 2026-06-04):
    # narrative routing is BM25 over name/summary/keywords, so there is no
    # routing_embedding / topic_hint / VectorStore to maintain. The DB columns
    # (routing_embedding, embedding_updated_at, events_since_last_embedding_update,
    # topic_hint) are left as inert tombstones per binding rule #6 (no
    # destructive migrations); nothing reads or writes them anymore.
