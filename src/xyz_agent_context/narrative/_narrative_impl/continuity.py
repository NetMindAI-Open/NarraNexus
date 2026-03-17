"""
Query Continuity Detection & Narrative Attribution

@file_name: continuity.py
@author: NetMind.AI
@date: 2025-12-22
@description: Uses LLM to detect whether a Query belongs to the current Narrative.
Note: Conversation continuity ≠ Same Narrative. Must consider the Narrative's theme information.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from pydantic import BaseModel, Field
from loguru import logger

from ..models import ConversationSession, ContinuityResult
from xyz_agent_context.agent_framework.openai_agents_sdk import OpenAIAgentsSDK
from ..config import config as narrative_config
from .prompts import CONTINUITY_DETECTION_INSTRUCTIONS

if TYPE_CHECKING:
    from ..models import Narrative


# ===== LLM Output Schema Definition =====

class ContinuityOutput(BaseModel):
    """
    LLM output schema for Narrative attribution detection.
    """
    is_continuous: bool = Field(..., description="Whether the query belongs to the current Narrative")
    confidence: float = Field(default=0.5, description="Confidence score between 0.0 and 1.0")
    reason: str = Field(default="", description="Brief reasoning for the decision")


class ContinuityDetector:
    """
    Narrative Attribution Detector

    Uses LLM to determine whether the current Query belongs to the current Narrative.

    Notes:
    - Conversation continuity ≠ Same Narrative
    - Users may switch topics during continuous conversation, requiring a new Narrative
    - Judgment should consider the Narrative's name, description, summary, and keywords

    Special Handling:
    - The system has 8 special default Narratives (is_special="default")
    - These Narratives have very strict boundaries with simplified information
    - Once the user mentions specific objects, tasks, or ongoing topics, should switch to a new Narrative

    Example:
        >>> detector = ContinuityDetector()
        >>>
        >>> # Continuation of a regular Narrative
        >>> result = await detector.detect("tell me more about this product", session, current_narrative)
        >>> print(result.is_continuous)  # True - belongs to current Narrative
        >>>
        >>> # Switching from special Narrative to specific topic
        >>> result = await detector.detect("help me write code", session, greeting_narrative)
        >>> print(result.is_continuous)  # False - switching from greeting to specific task
    """

    def __init__(self):
        """
        Initialize the detector.
        """
        self.sdk = OpenAIAgentsSDK()
        logger.debug("ContinuityDetector initialized")

    async def detect(
        self,
        current_query: str,
        session: ConversationSession,
        current_narrative: Optional["Narrative"] = None,
        awareness: Optional[str] = None
    ) -> ContinuityResult:
        """
        Detect whether the Query belongs to the current Narrative.

        Note: This is not just about conversation continuity, but whether the current Query
        belongs to the same Narrative. The conversation may be continuous, but the topic
        may have switched to another Narrative.

        Args:
            current_query: The current Query
            session: Session object
            current_narrative: Current Narrative object (optional)
            awareness: Agent awareness content (optional)

        Returns:
            ContinuityResult: Detection result
        """
        # No historical Query
        if not session.last_query or session.last_query.strip() == "":
            return ContinuityResult(
                is_continuous=False,
                confidence=1.0,
                reason="new_session"
            )

        # Calculate time elapsed
        # Ensure last_query_time is offset-aware (if naive, assume UTC)
        last_query_time = session.last_query_time
        if last_query_time.tzinfo is None:
            last_query_time = last_query_time.replace(tzinfo=timezone.utc)

        time_elapsed = (datetime.now(timezone.utc) - last_query_time).total_seconds()
        time_minutes = time_elapsed / 60.0

        try:
            return await self._call_llm(
                previous_query=session.last_query,
                previous_response=session.last_response,
                current_query=current_query,
                time_elapsed_minutes=time_minutes,
                current_narrative=current_narrative,
                awareness=awareness
            )
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return ContinuityResult(
                is_continuous=False,
                confidence=0.5,
                reason=f"llm_error: {str(e)}"
            )

    async def _call_llm(
        self,
        previous_query: str,
        previous_response: str,
        current_query: str,
        time_elapsed_minutes: float,
        current_narrative: Optional["Narrative"] = None,
        awareness: Optional[str] = None
    ) -> ContinuityResult:
        """Call LLM to determine if the query belongs to the same Narrative."""
        instructions = CONTINUITY_DETECTION_INSTRUCTIONS

        # Build user input
        narrative_context = ""
        if current_narrative:
            # Check if this is a special default Narrative
            is_default_narrative = current_narrative.is_special == "default"
            narrative_type_label = "[Special Default Narrative]" if is_default_narrative else "[Regular Narrative]"

            narrative_context = f"""
Current Narrative Information:
{narrative_type_label}
- Name: {current_narrative.narrative_info.name}
- Description: {current_narrative.narrative_info.description}
- Current Summary: {current_narrative.narrative_info.current_summary}
- Topic Keywords: {', '.join(current_narrative.topic_keywords) if current_narrative.topic_keywords else 'None'}

Note: If this is a [Special Default Narrative], its boundaries are very strict. Once the user mentions specific objects, tasks, or ongoing topics, it should be judged as not belonging to the current Narrative.
"""
        else:
            narrative_context = "\nNo current Narrative information (this is a new session or no history)\n"

        # Build Agent Awareness context
        awareness_context = ""
        if awareness:
            awareness_context = f"""
Agent Awareness:
{awareness}

Note: The Agent's role and characteristics may influence how Narratives are categorized. Please consider the Agent's positioning when judging topic attribution.
"""

        user_input = f"""Previous conversation turn:
User asked: {previous_query}
Agent's reasoning: {previous_response}
{narrative_context}{awareness_context}
Current user query: {current_query}

Time elapsed: {time_elapsed_minutes:.1f} minutes

Please determine whether the current query belongs to the current Narrative (not just whether the conversation is continuous)."""
        logger.debug(f"LLM input: {user_input}")

        try:
            result = await self.sdk.llm_function(
                instructions=instructions,
                user_input=user_input,
                output_type=ContinuityOutput,
                model=narrative_config.CONTINUITY_LLM_MODEL,
            )

            # result is RunResult, get the parsed Pydantic object via .final_output
            output: ContinuityOutput = result.final_output

            # Ensure confidence is within valid range
            confidence = max(0.0, min(1.0, output.confidence))

            return ContinuityResult(
                is_continuous=output.is_continuous,
                confidence=confidence,
                reason=f"LLM decision: {output.reason}"
            )

        except Exception as e:
            raise RuntimeError(f"LLM call failed: {e}")
