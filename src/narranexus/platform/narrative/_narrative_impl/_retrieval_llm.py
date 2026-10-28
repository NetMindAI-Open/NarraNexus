"""
@file_name: _retrieval_llm.py
@author: Bin Liang
@date: 2026-03-06
@description: LLM-based Narrative match judgment logic

Extracted from retrieval.py. Contains:
- LLM output schema definitions
- Unified multi-candidate judgment (llm_judge_unified)

These are pure LLM judgment functions with no dependency on NarrativeRetrieval state.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel
from loguru import logger

from narranexus.platform.agent_framework.llm.helper_sdk import get_helper_sdk
from ..config import config
from . import routing_blocks
from .prompts import (
    NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS,
    NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS,
)


# ===== LLM output schema definitions =====

class UnifiedMatchOutput(BaseModel):
    """
    LLM unified match output structure

    Used for the output of the llm_judge_unified function.
    """
    reason: str  # Detailed reasoning process
    matched_category: str  # "search", "no_durable_topic", or "none"
    matched_index: int  # Matched index (0-based), -1 unless matched_category="search"


# ===== LLM judgment functions =====

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
    # NO early return on an empty candidate set — deliberately.
    #
    # It used to bail out here with `matched_type=None`, which the caller reads
    # as "nothing matched, so create". That was harmless only because eight
    # default buckets were always in the menu, so the branch was unreachable.
    # Bucket governance (C-1) emptied the menu and thereby ACTIVATED it, with
    # the worst possible aim: a contentless message has near-zero term overlap,
    # so an empty pool is precisely the case where the verdict matters most.
    # Live probe 2026-08-16: a bare "哈哈哈" opened a new thread while the
    # session held a good anchor, and an ephemeral voice turn created a
    # narrative against its own no-trace contract.
    #
    # "No candidates" is not an answer to "does this turn carry a durable
    # topic". Only the model can answer that, and with an empty menu its answer
    # is exactly the binary we need: `no_durable_topic` (land it anchor-first)
    # or `none` (a real new subject deserves a thread). The extra helper call
    # buys the decision that was previously being made wrongly for free.

    has_participant_context = participant_candidates and len(participant_candidates) > 0

    try:
        # Adjust instructions based on whether PARTICIPANT candidates exist
        if has_participant_context:
            instructions = NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS
        else:
            instructions = NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS

        # Build candidate list
        user_input = ""

        # 0. PARTICIPANT Narratives - placed first to emphasize importance.
        # Rendered by the shared block (routing_blocks) so the merged router
        # cannot become a second, drifting copy of this section — the exact
        # failure this file has already paid for twice.
        user_input += routing_blocks.render_participant_candidates(
            participant_candidates or []
        ).text

        # 1. Default Narratives — empty under bucket governance (C-1). The
        # eight category names still reach the model, as VOCABULARY inside the
        # instructions, so it can still recognise "no durable topic"; what it
        # can no longer do is file the turn INTO one of them.
        if default_candidates:
            user_input += "## Default Topic Types:\n\n"
            for i, candidate in enumerate(default_candidates):
                user_input += f"[Default-{i}] {candidate['name']}\n"
                user_input += f"Description: {candidate['description']}\n"
                if candidate.get('examples'):
                    user_input += f"Examples: {', '.join(candidate['examples'][:3])}\n"
                user_input += "\n"

        # 2. Search results (BM25 keyword candidates), through the shared
        # renderer. The header is emitted even when the list is empty: with
        # nothing to match against, the model must be TOLD, not left to infer it
        # from a section that simply is not there. WHY each row scored — the
        # matched terms and the snippet — is the load-bearing part; see
        # routing_blocks.render_search_candidates.
        user_input += routing_blocks.render_search_candidates(
            search_candidates or [],
            header=routing_blocks.JUDGE_MENU_HEADER,
            empty_note=routing_blocks.JUDGE_MENU_EMPTY_NOTE,
            include_score=True,
        ).text

        user_input += f"## User's New Query:\n{query}\n\n"
        user_input += "Please determine which candidate the user query should match, or create a new topic."

        sdk = get_helper_sdk()
        result = await sdk.llm_function(
            instructions=instructions,
            user_input=user_input,
            output_type=UnifiedMatchOutput,
            model=config.NARRATIVE_JUDGE_LLM_MODEL,
            reasoning_effort=config.NARRATIVE_JUDGE_LLM_REASONING_EFFORT or None,
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

        elif output.matched_category == "no_durable_topic":
            # A verdict about the TURN, carrying no destination. The caller
            # (retrieval -> NarrativeService.select) decides where it lands;
            # see the anchor-first rule in select().
            logger.info(f"LLM: no durable topic this turn — {output.reason[:120]}")
            return {
                "matched_id": None,
                "matched_type": "no_topic",
                "reason": output.reason,
            }

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
