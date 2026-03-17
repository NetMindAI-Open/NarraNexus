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
from xyz_agent_context.utils.embedding import get_embedding
from xyz_agent_context.module.social_network_module.prompts import (
    ENTITY_SUMMARY_INSTRUCTIONS,
    DESCRIPTION_COMPRESSION_INSTRUCTIONS,
    PERSONA_INFERENCE_INSTRUCTIONS,
    BATCH_ENTITY_EXTRACTION_INSTRUCTIONS,
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
    """A single entity mentioned in the conversation"""
    name: str = Field(..., description="Entity name as mentioned in the conversation")
    entity_type: str = Field(default="user", description="Entity type: user | agent | organization")
    summary: str = Field(default="", description="Brief summary of what was said about this entity")
    tags: List[str] = Field(default_factory=list, description="0-2 tags only when clearly evidenced (e.g. expert:ML, engineer). Prefer empty list.")


class BatchExtractionOutput(BaseModel):
    """Output of batch entity extraction from conversation"""
    entities: List[ExtractedEntity] = Field(
        default_factory=list,
        description="All entities mentioned in the conversation (excluding the primary speaker)"
    )


# ── Batch Entity Extraction Pipeline ────────────────────────────────────────


async def extract_mentioned_entities(
    input_content: str,
    final_output: str,
    primary_entity_name: str = "",
) -> List[ExtractedEntity]:
    """
    Extract all entities mentioned in a conversation (besides the primary speaker).

    Uses LLM to detect mentions of other people, agents, or organizations
    in the conversation, so SocialNetworkModule can auto-create or update them.

    Args:
        input_content: User input
        final_output: Agent output
        primary_entity_name: Name of the primary interaction entity (excluded from results)

    Returns:
        List of extracted entities (may be empty if no others are mentioned)
    """
    try:
        user_input = f"""Conversation:
User: {input_content}
Agent: {final_output}

Primary speaker name (EXCLUDE from results): {primary_entity_name or 'unknown'}

Extract all OTHER entities mentioned:"""

        sdk = OpenAIAgentsSDK()
        result = await sdk.llm_function(
            instructions=BATCH_ENTITY_EXTRACTION_INSTRUCTIONS,
            user_input=user_input,
            output_type=BatchExtractionOutput,
        )
        output: BatchExtractionOutput = result.final_output

        # Filter out empty or primary entity matches
        filtered = [
            e for e in output.entities
            if e.name.strip()
            and e.name.lower() != primary_entity_name.lower()
        ]

        if filtered:
            logger.info(f"Batch extraction found {len(filtered)} mentioned entities")
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
        return output.summary.strip()

    except Exception as e:
        logger.error(f"Error summarizing entity info: {e}")
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
        logger.error(f"Error appending to entity_description: {e}")


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
        if entity.tags:
            text_parts.append(f"Tags: {', '.join(entity.tags)}")

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
        logger.info(f"Updated embedding for entity {entity_id} (dim={len(embedding)})")

    except Exception as e:
        logger.error(f"Error updating entity embedding: {e}")


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
        logger.error(f"Error compressing description: {e}")
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
        logger.error(f"Error updating interaction stats: {e}")


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
- Tags: {', '.join(entity.tags) if entity.tags else 'None'}
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
        logger.error(f"            Error inferring persona: {e}")
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
        logger.error(f"            Error updating persona: {e}")
