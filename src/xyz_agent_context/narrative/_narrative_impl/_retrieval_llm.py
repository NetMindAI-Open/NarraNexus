"""
@file_name: _retrieval_llm.py
@author: Bin Liang
@date: 2026-03-06
@description: LLM-based Narrative match judgment logic

Extracted from retrieval.py. Contains:
- LLM output schema definitions
- Single-match confirmation (llm_confirm)
- Unified multi-candidate judgment (llm_judge_unified)

These are pure LLM judgment functions with no dependency on NarrativeRetrieval state.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel
from loguru import logger

from xyz_agent_context.agent_framework.openai_agents_sdk import OpenAIAgentsSDK
from ..config import config
from .prompts import (
    NARRATIVE_SINGLE_MATCH_INSTRUCTIONS,
    NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS,
    NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS,
)


# ===== LLM output schema definitions =====

class RelationType(Enum):
    """Narrative relation type"""
    CONTINUATION = "continuation"
    REFERENCE = "reference"
    OTHER = "other"


class NarrativeMatchOutput(BaseModel):
    """LLM Narrative match output structure"""
    reason: str
    matched_index: int
    relation_type: RelationType


class UnifiedMatchOutput(BaseModel):
    """
    LLM unified match output structure

    Used for the output of the llm_judge_unified function.
    """
    reason: str  # Detailed reasoning process
    matched_category: str  # "default", "search", or "none"
    matched_index: int  # Matched index (0-based), -1 if matched_category="none"


# ===== LLM judgment functions =====

async def llm_confirm(query: str, candidates: List[dict]) -> dict:
    """
    LLM single-match confirmation

    Used by retrieve_or_create for simple binary confirmation.

    Args:
        query: User query
        candidates: Candidate list [{"id", "name", "query"}]

    Returns:
        {"matched_id": str/None, "reason": str}
    """
    if not candidates:
        return {"matched_id": None, "reason": "No candidates"}

    try:
        instructions = NARRATIVE_SINGLE_MATCH_INSTRUCTIONS

        # Build candidate topic list
        user_input = ""
        for index, candidate in enumerate(candidates):
            user_input += f"Topic {index}: {candidate.get('name', 'Untitled')}\nDescription: {candidate.get('query', '')}\n\n"
        user_input += f"User's new query: {query}"

        sdk = OpenAIAgentsSDK()
        result = await sdk.llm_function(
            instructions=instructions,
            user_input=user_input,
            output_type=NarrativeMatchOutput,
            model=config.NARRATIVE_JUDGE_LLM_MODEL,
        )
        output: NarrativeMatchOutput = result.final_output

        # Both continuation and reference are considered a match; also check index bounds
        if output.relation_type in (RelationType.CONTINUATION, RelationType.REFERENCE):
            if 0 <= output.matched_index < len(candidates):
                return {"matched_id": candidates[output.matched_index]["id"], "reason": output.reason}
            logger.warning(f"LLM returned matched_index={output.matched_index} out of range [0, {len(candidates)})")
        return {"matched_id": None, "reason": output.reason or "New topic"}

    except Exception as e:
        logger.warning(f"LLM confirmation failed: {e}")
        return {"matched_id": None, "reason": f"LLM call failed: {str(e)}"}


async def llm_judge_unified(
    query: str,
    search_candidates: List[dict],
    default_candidates: List[dict],
    participant_candidates: Optional[List[dict]] = None,
) -> dict:
    """
    LLM unified judgment: Considers search results, default Narratives, and PARTICIPANT Narratives

    Args:
        query: User query
        search_candidates: Search result candidates [{"id", "type": "search", "name", "description", "score"}]
        default_candidates: Default Narrative candidates [{"id", "type": "default", "name", "description", "examples"}]
        participant_candidates: PARTICIPANT Narratives [{"id", "type": "participant", "name", "description"}]

    Returns:
        {
            "matched_id": str/None,
            "matched_type": "default"/"search"/"participant"/None,
            "reason": str
        }
    """
    if not search_candidates and not default_candidates and not participant_candidates:
        return {"matched_id": None, "matched_type": None, "reason": "No candidates"}

    has_participant_context = participant_candidates and len(participant_candidates) > 0

    try:
        # Adjust instructions based on whether PARTICIPANT candidates exist
        if has_participant_context:
            instructions = NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS
        else:
            instructions = NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS

        # Build candidate list
        user_input = ""

        # 0. PARTICIPANT Narratives - placed first to emphasize importance
        if participant_candidates:
            user_input += "## Participant-Associated Topics (user is a PARTICIPANT):\n\n"
            for i, candidate in enumerate(participant_candidates):
                user_input += f"[Participant-{i}] {candidate['name']}\n"
                user_input += f"Description: {candidate['description']}\n"
                user_input += "\n"

        # 1. Default Narratives
        if default_candidates:
            user_input += "## Default Topic Types:\n\n"
            for i, candidate in enumerate(default_candidates):
                user_input += f"[Default-{i}] {candidate['name']}\n"
                user_input += f"Description: {candidate['description']}\n"
                if candidate.get('examples'):
                    user_input += f"Examples: {', '.join(candidate['examples'][:3])}\n"
                user_input += "\n"

        # 2. Search results (with Phase 1 matched_content from EverMemOS)
        if search_candidates:
            user_input += "## Existing Topics:\n\n"
            for i, candidate in enumerate(search_candidates):
                user_input += f"[Topic-{i}] {candidate['name']}\n"
                user_input += f"Description: {candidate['description']}\n"
                user_input += f"Similarity score: {candidate['score']:.2f}\n"
                if candidate.get('matched_content'):
                    user_input += f"Matched content:\n{candidate['matched_content']}\n"
                    logger.info(f"[Phase 1] Candidate {i} added matched_content ({len(candidate['matched_content'])} chars)")
                else:
                    logger.debug(f"[Phase 1] Candidate {i} has no matched_content")
                user_input += "\n"

        user_input += f"## User's New Query:\n{query}\n\n"
        user_input += "Please determine which candidate the user query should match, or create a new topic."

        sdk = OpenAIAgentsSDK()
        result = await sdk.llm_function(
            instructions=instructions,
            user_input=user_input,
            output_type=UnifiedMatchOutput,
            model=config.NARRATIVE_JUDGE_LLM_MODEL,
        )
        output: UnifiedMatchOutput = result.final_output

        # Parse result — prioritize PARTICIPANT match
        if output.matched_category == "participant":
            if participant_candidates and 0 <= output.matched_index < len(participant_candidates):
                matched_id = participant_candidates[output.matched_index]["id"]
                logger.info(f"LLM matched PARTICIPANT Narrative (index={output.matched_index}): {matched_id}")
                return {
                    "matched_id": matched_id,
                    "matched_type": "participant",
                    "reason": output.reason
                }
            else:
                logger.warning(f"LLM returned participant index={output.matched_index} out of range")

        elif output.matched_category == "default":
            if 0 <= output.matched_index < len(default_candidates):
                matched_id = default_candidates[output.matched_index]["id"]
                logger.info(f"LLM matched default Narrative (index={output.matched_index}): {matched_id}")
                return {
                    "matched_id": matched_id,
                    "matched_type": "default",
                    "reason": output.reason
                }
            else:
                logger.warning(f"LLM returned default index={output.matched_index} out of range")

        elif output.matched_category == "search":
            if 0 <= output.matched_index < len(search_candidates):
                matched_id = search_candidates[output.matched_index]["id"]
                logger.info(f"LLM matched search result (index={output.matched_index}): {matched_id}")
                return {
                    "matched_id": matched_id,
                    "matched_type": "search",
                    "reason": output.reason
                }
            else:
                logger.warning(f"LLM returned search index={output.matched_index} out of range")

        # matched_category == "none" or error
        logger.info(f"LLM determined no match with any Narrative: {output.reason}")
        return {
            "matched_id": None,
            "matched_type": None,
            "reason": output.reason
        }

    except Exception as e:
        logger.warning(f"LLM unified judgment failed: {e}")
        return {
            "matched_id": None,
            "matched_type": None,
            "reason": f"LLM call failed: {str(e)}"
        }
