"""
@file_name: _entity_updater.py
@author: NetMind.AI
@date: 2026-03-06
@description: Entity description, embedding, and persona update logic

Extracted from SocialNetworkModule to separate LLM-powered entity update
operations from the module's hook orchestration and MCP interface.

Contains:
- summarize_new_entity_info: LLM conversation summarization
- append_to_entity_description: Cumulative description update with compression
- update_entity_embedding: Embedding vector regeneration
- compress_description: LLM description compression
- update_interaction_stats: Interaction counter increment
- should_update_persona: Persona refresh condition check
- infer_persona: LLM persona inference
- update_entity_persona: Persona DB write
"""

from typing import List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.agent_framework.openai_agents_sdk import OpenAIAgentsSDK
from xyz_agent_context.repository import SocialNetworkRepository, SocialNetworkEntity
from xyz_agent_context.agent_framework.llm_api.embedding import get_embedding
from xyz_agent_context.module.social_network_module.prompts import (
    ENTITY_SUMMARY_INSTRUCTIONS,
    DESCRIPTION_COMPRESSION_INSTRUCTIONS,
    PERSONA_INFERENCE_INSTRUCTIONS,
    BATCH_ENTITY_EXTRACTION_INSTRUCTIONS,
    DEDUP_MERGE_DECISION_INSTRUCTIONS,
)


# ── LLM Output Schemas ──────────────────────────────────────────────────────

class SummaryOutput(BaseModel):
    """Conversation summary output structure"""
    summary: str = Field(default="", description="Short summary of conversation key points (one line)")


class CompressedDescriptionOutput(BaseModel):
    """Compressed description output structure"""
    compressed_summary: str = Field(default="", description="Compressed description (no more than 500 characters)")


class PersonaOutput(BaseModel):
    """Persona inference output structure"""
    persona: str = Field(
        default="",
        description="Communication persona/style guide for interacting with this entity (1-3 sentences in natural language)"
    )


class ExtractedEntity(BaseModel):
    """A single social entity mentioned in the conversation (human, agent, or group only)"""
    name: str = Field(..., description="Entity name as mentioned in the conversation")
    entity_type: str = Field(default="user", description="Entity type: user | agent | group")
    summary: str = Field(default="", description="Brief summary of what was said about this entity")
    keywords: List[str] = Field(default_factory=list, description="0-3 contextual keywords (topics, domains, platforms associated with this person)")
    aliases: List[str] = Field(default_factory=list, description="System IDs and alternate names (e.g. Lark open_ids, platform agent IDs)")
    familiarity: str = Field(default="known_of", description="direct (participating in conversation) | known_of (only referenced)")


class BatchExtractionOutput(BaseModel):
    """Output of batch entity extraction from conversation"""
    entities: List[ExtractedEntity] = Field(
        default_factory=list,
        description="All entities mentioned in the conversation (excluding the primary speaker)"
    )


class DedupDecision(BaseModel):
    """Dedup merge decision output"""
    decision: str = Field(description="MERGE or CREATE_NEW")
    merge_target_index: Optional[int] = Field(default=None, description="Index of the existing entity to merge with (0-based). Required if decision is MERGE.")
    reason: str = Field(default="", description="One-line explanation for the decision")


# ── Dedup Pipeline ──────────────────────────────────────────────────────────

# Similarity threshold for vector-based dedup candidate retrieval
DEDUP_SIMILARITY_THRESHOLD = 0.6
DEDUP_TOP_K = 3


async def decide_merge_or_create(
    candidate_name: str,
    candidate_summary: str,
    candidate_aliases: List[str],
    existing_entities: List[SocialNetworkEntity],
) -> tuple[str, Optional[SocialNetworkEntity]]:
    """
    Use LLM to decide if a candidate entity matches any of the existing entities.
    All candidates are presented in one call so the LLM can compare across them.

    Args:
        candidate_name: Name of the newly extracted entity
        candidate_summary: Summary of what was said about this entity
        candidate_aliases: System IDs and alternate names
        existing_entities: List of potential matches from Stage 1 or Stage 2

    Returns:
        Tuple of (decision, matched_entity):
        - ("MERGE", entity) if LLM decides it matches one of the existing entities
        - ("CREATE_NEW", None) if LLM decides it's a new entity
    """
    if not existing_entities:
        return "CREATE_NEW", None

    try:
        candidate_aliases_str = ", ".join(candidate_aliases) if candidate_aliases else "None"

        # Build description of all existing candidates
        existing_lines = []
        for i, e in enumerate(existing_entities):
            desc = (e.entity_description or "No description")[:200]
            aliases = ", ".join(e.aliases) if e.aliases else "None"
            keywords = ", ".join(e.keywords) if e.keywords else "None"
            existing_lines.append(
                f"[{i}] Name: {e.entity_name or 'Unknown'} | ID: {e.entity_id} | "
                f"Aliases: {aliases} | Keywords: {keywords} | "
                f"Interactions: {e.interaction_count} | Desc: {desc}"
            )

        user_input = f"""**Candidate (newly extracted):**
- Name: {candidate_name}
- Summary: {candidate_summary or 'No summary'}
- Aliases: {candidate_aliases_str}

**Existing entities in database ({len(existing_entities)} candidates):**
{chr(10).join(existing_lines)}

Does the candidate match any existing entity? If yes, return MERGE with the index. If no match, return CREATE_NEW:"""

        sdk = OpenAIAgentsSDK()
        result = await sdk.llm_function(
            instructions=DEDUP_MERGE_DECISION_INSTRUCTIONS,
            user_input=user_input,
            output_type=DedupDecision,
        )
        output: DedupDecision = result.final_output
        decision = output.decision.strip().upper()

        if decision == "MERGE":
            idx = output.merge_target_index
            if idx is not None and 0 <= idx < len(existing_entities):
                matched = existing_entities[idx]
                logger.info(
                    f"            Dedup decision for '{candidate_name}': MERGE → "
                    f"{matched.entity_name} ({matched.entity_id}) — {output.reason}"
                )
                return "MERGE", matched
            else:
                logger.warning(
                    f"            Dedup MERGE but invalid index {idx} "
                    f"(max {len(existing_entities)-1}), defaulting to CREATE_NEW"
                )
                return "CREATE_NEW", None

        logger.info(f"            Dedup decision for '{candidate_name}': CREATE_NEW — {output.reason}")
        return "CREATE_NEW", None

    except Exception as e:
        logger.warning(f"            Dedup LLM call failed, defaulting to CREATE_NEW: {e}")
        return "CREATE_NEW", None


# ── Batch Entity Extraction Pipeline ────────────────────────────────────────


async def extract_mentioned_entities(
    input_content: str,
    final_output: str,
    primary_entity_name: str = "",
    agent_name: str = "",
    agent_id: str = "",
) -> List[ExtractedEntity]:
    """
    Extract all entities mentioned in a conversation (besides the primary speaker and the agent itself).

    Uses LLM to detect mentions of other people, agents, or organizations
    in the conversation, so SocialNetworkModule can auto-create or update them.

    Args:
        input_content: User input
        final_output: Agent output
        primary_entity_name: Name of the primary interaction entity (excluded from results)
        agent_name: The agent's own name (excluded from results to prevent self-extraction)
        agent_id: The agent's own ID (excluded from results)

    Returns:
        List of extracted entities (may be empty if no others are mentioned)
    """
    try:
        # Build exclusion list for the LLM prompt
        exclusions = [primary_entity_name or 'unknown']
        if agent_name:
            exclusions.append(agent_name)
        if agent_id:
            exclusions.append(agent_id)
        exclusion_str = ", ".join(exclusions)

        user_input = f"""Conversation:
User: {input_content}
Agent: {final_output}

Names to EXCLUDE from results (these are the conversation participants): {exclusion_str}

Extract all OTHER social entities mentioned:"""

        logger.debug(
            f"[SocialExtraction] LLM input:\n"
            f"  Excluded names: {exclusion_str}\n"
            f"  User msg preview: {input_content[:200]}...\n"
            f"  Agent msg preview: {final_output[:200]}..."
        )

        sdk = OpenAIAgentsSDK()
        result = await sdk.llm_function(
            instructions=BATCH_ENTITY_EXTRACTION_INSTRUCTIONS,
            user_input=user_input,
            output_type=BatchExtractionOutput,
        )
        output: BatchExtractionOutput = result.final_output

        logger.info(
            f"[SocialExtraction] LLM returned {len(output.entities)} raw entities: "
            f"{[e.name for e in output.entities]}"
        )

        # Build exclusion set for post-filter (case-insensitive)
        exclude_lower = {n.lower() for n in exclusions if n}
        if agent_id:
            exclude_lower.add(agent_id.lower())

        # Filter out empty, primary entity, and self-references
        filtered = [
            e for e in output.entities
            if e.name.strip()
            and e.name.lower() not in exclude_lower
        ]

        if filtered:
            logger.info(f"[SocialExtraction] After filtering: {len(filtered)} entities")
            for e in filtered:
                logger.info(
                    f"[SocialExtraction]   → {e.name} (type={e.entity_type}, "
                    f"familiarity={e.familiarity}, keywords={e.keywords}, "
                    f"aliases={e.aliases}, summary={e.summary[:80]}...)"
                )
        else:
            logger.debug("[SocialExtraction] No entities after filtering")

        return filtered

    except Exception as e:
        logger.warning(f"Batch entity extraction failed (non-critical): {e}")
        return []


# ── Entity Description Pipeline ─────────────────────────────────────────────


async def summarize_new_entity_info(input_content: str, final_output: str) -> str:
    """
    Call LLM to summarize key points of a conversation round.

    Returns:
        Short summary of conversation key points, or empty string if no significant info.
    """
    try:
        user_input = f"""User: {input_content}
Agent: {final_output}

Summary (one line only):"""

        sdk = OpenAIAgentsSDK()
        result = await sdk.llm_function(
            instructions=ENTITY_SUMMARY_INSTRUCTIONS,
            user_input=user_input,
            output_type=SummaryOutput,
        )
        output: SummaryOutput = result.final_output
        summary = output.summary.strip()
        logger.info(f"[SocialSummary] Result: '{summary[:120]}'" if summary else "[SocialSummary] No significant info")
        return summary

    except Exception as e:
        logger.exception(f"Error summarizing entity info: {e}")
        return ""


async def append_to_entity_description(
    repo: SocialNetworkRepository,
    entity_id: str,
    instance_id: str,
    new_info: str,
) -> None:
    """
    Append information to entity_description (cumulative, not overwriting).
    Compresses if description exceeds 2000 chars.
    """
    try:
        entity = await repo.get_entity(entity_id=entity_id, instance_id=instance_id)
        if not entity:
            logger.warning(f"Entity {entity_id} not found, cannot append description")
            return

        existing_desc = entity.entity_description or ""
        new_description = f"{existing_desc}\n- {new_info}" if existing_desc else new_info

        if len(new_description) > 2000:
            logger.info(f"Description too long ({len(new_description)} chars), compressing...")
            new_description = await compress_description(new_description)

        await repo.update_entity_info(
            entity_id=entity_id,
            instance_id=instance_id,
            updates={"entity_description": new_description}
        )
        logger.info(f"Appended to entity_description: {new_info[:50]}...")

    except Exception as e:
        logger.exception(f"Error appending to entity_description: {e}")


async def update_entity_embedding(
    repo: SocialNetworkRepository,
    entity_id: str,
    instance_id: str,
) -> None:
    """
    Update entity's embedding vector based on entity_name + entity_description + tags.
    """
    try:
        entity = await repo.get_entity(entity_id=entity_id, instance_id=instance_id)
        if not entity:
            logger.warning(f"Entity {entity_id} not found, cannot update embedding")
            return

        text_parts = []
        if entity.entity_name:
            text_parts.append(f"Name: {entity.entity_name}")
        if entity.entity_description:
            text_parts.append(f"Description: {entity.entity_description}")
        if entity.keywords:
            text_parts.append(f"Keywords: {', '.join(entity.keywords)}")

        embedding_text = "\n".join(text_parts)
        if not embedding_text.strip():
            logger.debug("No content for embedding generation, skipping")
            return

        embedding = await get_embedding(embedding_text)
        await repo.update_entity_info(
            entity_id=entity_id,
            instance_id=instance_id,
            updates={"embedding": embedding}
        )
        # Dual-write to embeddings_store
        from xyz_agent_context.agent_framework.llm_api.embedding_store_bridge import store_embedding
        await store_embedding("entity", entity_id, embedding, source_text=embedding_text)

        logger.info(f"Updated embedding for entity {entity_id} (dim={len(embedding)})")

    except Exception as e:
        logger.exception(f"Error updating entity embedding: {e}")


async def compress_description(long_description: str) -> str:
    """Compress overly long description via LLM re-summarization."""
    try:
        user_input = f"""{long_description}

Compressed summary:"""

        sdk = OpenAIAgentsSDK()
        result = await sdk.llm_function(
            instructions=DESCRIPTION_COMPRESSION_INSTRUCTIONS,
            user_input=user_input,
            output_type=CompressedDescriptionOutput,
        )
        output: CompressedDescriptionOutput = result.final_output
        return output.compressed_summary.strip()

    except Exception as e:
        logger.exception(f"Error compressing description: {e}")
        return long_description[:1000] + "..."


async def update_interaction_stats(
    repo: SocialNetworkRepository,
    entity_id: str,
    instance_id: str,
) -> None:
    """Increment interaction counter and update last_interaction_time."""
    try:
        await repo.increment_interaction(entity_id=entity_id, instance_id=instance_id)
    except Exception as e:
        logger.exception(f"Error updating interaction stats: {e}")


# ── Persona Pipeline ─────────────────────────────────────────────────────────


def should_update_persona(entity: SocialNetworkEntity, response_content: str = "") -> bool:
    """
    Determine if Persona needs to be updated.

    Triggered if any condition is met:
    1. First interaction (persona is empty)
    2. Every 10 conversation rounds (periodic re-evaluation)
    3. Significant change signal detected in conversation
    """
    if entity.persona is None:
        logger.debug("            Persona update needed: first interaction (persona is None)")
        return True

    if entity.interaction_count > 0 and entity.interaction_count % 10 == 0:
        logger.debug(f"            Persona update needed: periodic re-evaluation (turn {entity.interaction_count})")
        return True

    change_signals = [
        "i changed my mind", "actually i care more about", "budget changed", "decision process changed",
        "change my mind", "our needs changed", "our requirements changed"
    ]
    if response_content and any(signal in response_content.lower() for signal in change_signals):
        logger.debug("            Persona update needed: change signal detected in conversation")
        return True

    return False


async def infer_persona(
    entity: SocialNetworkEntity,
    awareness: str = "",
    job_info: str = "",
    recent_conversation: str = "",
) -> str:
    """
    Infer Persona using LLM.

    Returns:
        Inferred persona description, or existing persona on failure.
    """
    try:
        entity_context = f"""Contact Information:
- Name: {entity.entity_name or 'Unknown'}
- Type: {entity.entity_type}
- Description: {entity.entity_description or 'No description yet'}
- Keywords: {', '.join(entity.keywords) if entity.keywords else 'None'}
- Interaction count: {entity.interaction_count}"""

        if entity.identity_info:
            entity_context += f"\n- Identity info: {entity.identity_info}"

        user_input_parts = [entity_context]
        if awareness:
            user_input_parts.append(f"\nAgent Awareness (Master's Instructions):\n{awareness}")
        if job_info:
            user_input_parts.append(f"\nRelated Job Information:\n{job_info}")
        if recent_conversation:
            user_input_parts.append(f"\nRecent Conversation:\n{recent_conversation}")
        if entity.persona:
            user_input_parts.append(f"\nCurrent Persona (for reference):\n{entity.persona}")
        user_input_parts.append("\nGenerate a concise communication persona for this contact:")

        sdk = OpenAIAgentsSDK()
        result = await sdk.llm_function(
            instructions=PERSONA_INFERENCE_INSTRUCTIONS,
            user_input="\n".join(user_input_parts),
            output_type=PersonaOutput,
        )

        output: PersonaOutput = result.final_output
        persona = output.persona.strip()

        if persona:
            logger.info(f"            Persona inferred: {persona[:50]}...")
            return persona
        else:
            logger.warning("            LLM returned empty persona")
            return entity.persona or ""

    except Exception as e:
        logger.exception(f"            Error inferring persona: {e}")
        return entity.persona or ""


async def update_entity_persona(
    repo: SocialNetworkRepository,
    entity_id: str,
    instance_id: str,
    new_persona: str,
) -> None:
    """Update entity's Persona in the database."""
    try:
        await repo.update_entity_info(
            entity_id=entity_id,
            instance_id=instance_id,
            updates={"persona": new_persona}
        )
        logger.info("            Entity persona updated")
    except Exception as e:
        logger.exception(f"            Error updating persona: {e}")
