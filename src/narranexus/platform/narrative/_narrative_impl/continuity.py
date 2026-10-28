"""
Query Continuity Detection & Narrative Attribution

@file_name: continuity.py
@author: NetMind.AI
@date: 2025-12-22
@description: Uses LLM to detect whether a Query belongs to the current Narrative.
Note: Conversation continuity ≠ Same Narrative. Must consider the Narrative's theme information.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from pydantic import BaseModel, Field
from loguru import logger
from .anchor_rules import minutes_since

from ..models import ConversationSession, ContinuityResult
from narranexus.platform.agent_framework.llm.helper_sdk import get_helper_sdk
from ..config import config as narrative_config
from . import routing_blocks
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
        self.sdk = get_helper_sdk()
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
        # No prior *visible* exchange at all — neither a previous user query
        # nor a previous agent reply the user could be responding to. Only then
        # is this genuinely a new session. (A proactive agent message anchors
        # last_response with last_query empty; that must still run continuity.)
        has_query = bool(session.last_query and session.last_query.strip())
        has_response = bool(session.last_response and session.last_response.strip())
        if not has_query and not has_response:
            return ContinuityResult(
                is_continuous=False,
                confidence=1.0,
                reason="new_session"
            )

        # THE one elapsed-time definition, shared with the merged path
        # (anchor_rules.minutes_since; naive timestamps read as UTC). The 0.0
        # default is DEFENSIVE, not a live bug fix: last_query_time is a
        # required pydantic field, so the None branch is unreachable through
        # any current constructor (review round 3, M6) — it exists so the
        # shared helper's None contract has a stated answer here. The
        # rendered prompt text is byte-identical whenever the value exists.
        time_minutes = minutes_since(session)
        if time_minutes is None:
            time_minutes = 0.0

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
            logger.exception(f"LLM call failed: {e}")
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

        # Build user input. The anchored-thread block is rendered by the
        # shared block (routing_blocks) — byte for byte what this method used to
        # build inline, and pinned as such by test. It is shared because the
        # merged router describes the same thread to the same model, and every
        # previous copy of one of these descriptions drifted (see the file
        # header there).
        narrative_context = routing_blocks.render_anchor_context(
            current_narrative
        ).text

        # Build Agent Awareness context
        awareness_context = ""
        if awareness:
            awareness_context = f"""
Agent Awareness:
{awareness}

Note: The Agent's role and characteristics may influence how Narratives are categorized. Please consider the Agent's positioning when judging topic attribution.
"""

        # current_query / last_query / last_response already arrive as clean
        # retrieval anchors ("[From <name>] <body>") from NarrativeService.select,
        # so no template-stripping is needed here. The old _extract_core_content
        # regex had drifted from the live channel template (stripped nothing in
        # prod) and was removed. See the 2026-06-01 design doc.
        clean_previous = previous_query
        clean_current = current_query
        clean_response = previous_response

        # The "previous turn" is whatever the user last SAW in their chat box,
        # in one of two shapes (normal exchange / the agent messaging the user
        # unprompted from a scheduled job). Both live in the shared block, for
        # the same reason as the anchor context above — the merged router needs
        # exactly this text, and the proactive variant is the kind of detail a
        # copy loses.
        previous_turn = routing_blocks.render_previous_turn(
            clean_previous, clean_response
        ).text

        user_input = f"""{previous_turn}
{narrative_context}{awareness_context}
Current user query: {clean_current}

Time elapsed: {time_elapsed_minutes:.1f} minutes

Please determine whether the current query belongs to the current Narrative (not just whether the conversation is continuous)."""
        logger.debug(f"LLM input: {user_input}")

        try:
            result = await self.sdk.llm_function(
                instructions=instructions,
                user_input=user_input,
                output_type=ContinuityOutput,
                model=narrative_config.CONTINUITY_LLM_MODEL,
                reasoning_effort=narrative_config.CONTINUITY_LLM_REASONING_EFFORT or None,
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
