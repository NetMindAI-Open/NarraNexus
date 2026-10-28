"""
@file_name: _entity_updater.py
@author: NetMind.AI
@date: 2026-03-06
@description: Entity description and persona update logic.

Extracted from SocialNetworkModule to separate LLM-powered entity update
operations from the module's hook orchestration and MCP interface.

Contains:
- summarize_new_entity_info: LLM conversation summarization
- append_to_entity_description: Cumulative description update with compression
- compress_description: LLM description compression
- update_interaction_stats: Interaction counter increment
- should_update_persona: Persona refresh condition check
- infer_persona: LLM persona inference
- update_entity_persona: Persona DB write
- extract_mentioned_entities: LLM batch extraction
- decide_merge_or_create: LLM dedup decision

2026-05-27: removed `update_entity_embedding` and the dedup
`DEDUP_SIMILARITY_THRESHOLD` / `DEDUP_TOP_K` constants together with
the semantic-search chain (Owner spec). Mentioned-entity dedup now
relies on Stage 1 (name/alias exact match) + LLM disambiguation only.

2026-08-24 — failures here stop being silent. Every function below used
to catch, log, and return an empty-ish value, which the caller could not
tell apart from a legitimately empty RESULT: `summarize_new_entity_info`
returning "" meant either "the LLM found nothing worth remembering" or
"the LLM is dead", and the caller skipped the write either way. So a
broken helper-LLM key degraded long memory with no owner-facing signal
at all — and "this sender's profile is permanently blank" is exactly why
the agent in the 8/14 ping-pong incident could not notice it had met
this peer 60,000 times before.

Two fixes, applied to all eight handlers:
  - Failure is now DISTINGUISHABLE from emptiness. The two functions
    whose empty value was ambiguous return `None` on failure (binding
    rule #2 — the signature just changes, no compatibility shim).
  - Failure is now REPORTED. LLM call sites route to
    `alert_background_llm_failure` (whose docstring already listed
    `entity_summary` as an intended source — the hole was dug, never
    wired); DB write sites leave a `service_audit` error row, since a
    failed UPDATE is not something an owner can fix by rotating a key.
"""

import re
from typing import List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from narranexus.platform.agent_framework.llm.failure import redact_secrets
from narranexus.platform.agent_framework.llm.helper_sdk import get_helper_sdk
from narranexus.platform.repository import (
    AgentRepository,
    SocialNetworkRepository,
    SocialNetworkEntity,
)
from narranexus.platform.services.background_llm_alerts import (
    alert_background_llm_failure,
)
from narranexus.platform.services.service_audit import ServiceAuditor
from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus_plugins.social_network_module.prompts import (
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
    confidence: float = Field(
        default=1.0,
        description=(
            "0-1: how confident you are that this is a REAL, individually "
            "identifiable social entity (not a concept, role, or artifact of "
            "the conversation). Below 0.5 the entity is discarded."
        ),
    )


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


# ── Meaningfulness Guard ────────────────────────────────────────────────────
#
# The extraction prompt already forbids concepts / roles / bare IDs, but weak
# helper models leak them anyway and every leaked row becomes a permanent
# junk node in the social graph (bug: "entity 图无意义条目"). This guard is
# the deterministic backstop: cheap, model-independent, applied to every
# extracted entity before it can be created or merged.

# Names that are roles/categories, not individuals (lowercased for compare).
_GENERIC_ENTITY_NAMES = {
    # English
    "user", "users", "the user", "assistant", "the assistant", "agent",
    "the agent", "agents", "admin", "administrator", "bot", "the bot",
    "system", "someone", "somebody", "anyone", "everyone", "everybody",
    "people", "team", "the team", "group", "members", "colleagues",
    "participants", "customer", "client", "owner", "creator", "my creator",
    "human", "unknown", "n/a", "none", "null",
    # Chinese
    "用户", "助手", "客服", "管理员", "机器人", "某人", "有人", "大家",
    "所有人", "团队", "群组", "成员", "同事", "客户", "创建者", "主人",
}

# Bare system/platform identifiers masquerading as names.
_ID_LIKE_PATTERNS = (
    re.compile(r"^(ou|oc|om|on|cli|usr|user|agent|bot|app|team|grp|art|evt|nar|sess)_[0-9a-z_-]+$", re.IGNORECASE),
    re.compile(r"^[0-9]+$"),                      # pure digits
    re.compile(r"^[0-9a-f]{12,}$", re.IGNORECASE),  # long hex blob
    re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE),  # uuid
)

_MIN_CONFIDENCE = 0.5
_MAX_NAME_LENGTH = 80

# Name the social-network memory plane records DB failures under.
_MEMORY_AUDIT_SERVICE = "social_network_memory"


async def _report_llm_failure(
    *, source: str, error: Exception, agent_id: str, source_id: str = ""
) -> None:
    """Route an LLM failure in this file to the background-failure surface.

    ``alert_background_llm_failure`` needs the OWNER to send its inbox
    notice, and nothing on this path carries one — the hook runs detached,
    several layers below where the owner was resolved.

    Ownership is resolved through ``AgentRepository.resolve_owner`` — the
    ONE answer to "who owns this agent". A hand-rolled
    ``get_one("agents", ...)`` here (the first version of this function)
    would be the fourth private copy of that lookup, which is exactly the
    drift PR #258 collapsed; ``backend/routes/channels/wechat.py`` carries
    an explicit prohibition against it.

    ``resolve_owner`` distinguishes ``""`` (unknown agent) from ``None``
    (the lookup itself failed) and both are falsy, so the alert's
    "notify only when the owner is known" behaviour is unchanged — do not
    collapse them with ``or ""``.

    Best-effort throughout: an observer must never break the observed.
    ``alert_background_llm_failure`` never raises on its own, but the DB
    handle for the owner lookup can, so that call stays wrapped. The audit
    tier inside the alert fires even when the owner is unknown, so a failed
    lookup still leaves a SQL-able trace.
    """
    owner_user_id = None
    if agent_id:
        try:
            owner_user_id = await AgentRepository(
                await get_db_client()
            ).resolve_owner(agent_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[social-memory] owner lookup failed: {e}")
    await alert_background_llm_failure(
        agent_id=agent_id,
        owner_user_id=owner_user_id,
        source=source,
        error=error,
        source_id=source_id,
    )


async def _report_write_failure(
    *,
    operation: str,
    error: Exception,
    entity_id: str,
    instance_id: str,
    agent_id: str = "",
) -> None:
    """A failed DB write leaves an audit row, not an owner notice.

    Deliberately a different surface from the LLM failures above: a broken
    UPDATE is our bug or an infrastructure problem, not something the
    owner fixes by rotating a key, so paging them would be alarm fatigue.
    But it must still be answerable weeks later — "the profile stopped
    updating on the 14th" needs a row, not a rotated log file (lesson #5).

    ``agent_id`` is optional here, unlike the required ``agent_id`` on the
    functions above, and the asymmetry is deliberate: there it prevents a
    silent degradation (a missing owner means the alert never reaches a
    human), here it only makes the audit row easier to query.

    Three of the five call sites pass it today
    (``append_to_entity_description`` plus the two in
    ``social_network_module``). The other two —
    ``update_interaction_stats`` and ``update_entity_persona`` — simply
    have not threaded it down yet; their caller does have one. That is a
    to-do, not a property of those call sites.

    ``redact_secrets`` matches what ``background_llm_alerts`` does before
    every audit write — one redaction policy per audit table, not two.

    ``ServiceAuditor.error`` never raises, so this needs no wrapper.
    """
    await ServiceAuditor(_MEMORY_AUDIT_SERVICE).error(
        {
            "operation": operation,
            "agent_id": agent_id,
            "entity_id": entity_id,
            "instance_id": instance_id,
            # Class name kept INSIDE the redaction, not dropped: for a DB
            # failure ``OperationalError`` (lock/timeout) and
            # ``IntegrityError`` (unique clash) call for different
            # responses, and some exceptions — ``asyncio.TimeoutError()``
            # — stringify to "", which would file an audit row saying only
            # that something failed. ``redact_secrets`` still covers the
            # whole string.
            "error": redact_secrets(f"{type(error).__name__}: {error}"),
        }
    )


def is_meaningful_entity(entity: ExtractedEntity) -> bool:
    """
    Deterministic backstop that keeps junk out of the social graph.

    Rejects:
    - generic role/category names ("user", "团队", ...) — not individuals
    - bare system IDs, pure digits, uuid/hex blobs — IDs belong in aliases
    - absurdly long names (garbage sentences from a confused model)
    - entities the model itself marked low-confidence (< 0.5)

    Args:
        entity: One extracted entity from the LLM.

    Returns:
        True if the entity may enter the create/merge pipeline.
    """
    name = entity.name.strip()
    if not name or len(name) > _MAX_NAME_LENGTH:
        return False
    if name.lower() in _GENERIC_ENTITY_NAMES:
        return False
    if any(p.match(name) for p in _ID_LIKE_PATTERNS):
        return False
    if entity.confidence < _MIN_CONFIDENCE:
        return False
    return True


# ── Dedup Pipeline ──────────────────────────────────────────────────────────
#
# 2026-05-27: the vector-similarity dedup stage was removed. Pipeline is
# now Stage 1 (exact name/alias match) → LLM disambiguation only.


async def decide_merge_or_create(
    candidate_name: str,
    candidate_summary: str,
    candidate_aliases: List[str],
    existing_entities: List[SocialNetworkEntity],
    *,
    agent_id: str,
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

        sdk = get_helper_sdk()
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
        # Defaulting to CREATE_NEW is the right SHAPE of failure (a
        # duplicate node beats losing the entity), but it is not free —
        # every failure forks the graph, so a dead key quietly shreds the
        # social network into near-duplicates. Report it.
        logger.warning(f"            Dedup LLM call failed, defaulting to CREATE_NEW: {e}")
        await _report_llm_failure(
            source="entity_dedup", error=e, agent_id=agent_id,
            source_id=candidate_name,
        )
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

        sdk = get_helper_sdk()
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

        # Filter out primary entity / self-references, then run every
        # survivor through the deterministic meaningfulness guard so junk
        # (generic roles, bare IDs, low-confidence guesses) never becomes
        # a permanent node in the social graph.
        filtered = []
        for e in output.entities:
            if not e.name.strip() or e.name.lower() in exclude_lower:
                continue
            if not is_meaningful_entity(e):
                logger.info(
                    f"[SocialExtraction] Dropped meaningless entity "
                    f"'{e.name}' (confidence={e.confidence})"
                )
                continue
            filtered.append(e)

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
        # "Non-critical" is true per-turn and false in aggregate: every
        # failure silently drops every entity this conversation mentioned.
        logger.warning(f"Batch entity extraction failed (non-critical): {e}")
        await _report_llm_failure(
            source="entity_extraction", error=e, agent_id=agent_id
        )
        return []


# ── Entity Description Pipeline ─────────────────────────────────────────────


async def summarize_new_entity_info(
    input_content: str, final_output: str, *, agent_id: str
) -> Optional[str]:
    """
    Call LLM to summarize key points of a conversation round.

    Returns:
        Short summary of the round's key points;
        ``""`` when the LLM ran fine and found nothing worth remembering;
        ``None`` when the call FAILED.

    The ""-vs-None split is the whole point of this signature. Both used
    to be "", so the caller's `if new_summary:` skipped the description
    write identically for "nothing new happened" and "our LLM is dead" —
    and long memory degraded with no signal for two weeks the last time
    that happened. Callers that treat None like "" still behave as
    before; the difference is that the failure is now REPORTED.
    """
    try:
        user_input = f"""User: {input_content}
Agent: {final_output}

Summary (one line only):"""

        sdk = get_helper_sdk()
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
        await _report_llm_failure(
            source="entity_summary", error=e, agent_id=agent_id
        )
        return None


async def append_to_entity_description(
    repo: SocialNetworkRepository,
    entity_id: str,
    instance_id: str,
    new_info: str,
    *,
    agent_id: str,
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
            new_description = await compress_description(
                new_description, agent_id=agent_id
            )

        await repo.update_entity_info(
            entity_id=entity_id,
            instance_id=instance_id,
            updates={"entity_description": new_description}
        )
        logger.info(f"Appended to entity_description: {new_info[:50]}...")

    except Exception as e:
        logger.exception(f"Error appending to entity_description: {e}")
        await _report_write_failure(
            operation="append_to_entity_description", error=e,
            entity_id=entity_id, instance_id=instance_id, agent_id=agent_id,
        )


# 2026-05-27: `update_entity_embedding` was removed together with the
# semantic-search chain. Mentioned entities and the self-user no longer
# carry a per-entity embedding; the only search path is keyword LIKE
# over name / description / tags / aliases.


async def compress_description(long_description: str, *, agent_id: str) -> str:
    """Compress overly long description via LLM re-summarization.

    Falls back to a hard truncation when the LLM is unavailable. That
    keeps the write moving (better a clipped description than none), but
    it silently loses whatever was past the cut — so the failure is
    reported even though the return value looks successful.
    """
    try:
        user_input = f"""{long_description}

Compressed summary:"""

        sdk = get_helper_sdk()
        result = await sdk.llm_function(
            instructions=DESCRIPTION_COMPRESSION_INSTRUCTIONS,
            user_input=user_input,
            output_type=CompressedDescriptionOutput,
        )
        output: CompressedDescriptionOutput = result.final_output
        return output.compressed_summary.strip()

    except Exception as e:
        logger.exception(f"Error compressing description: {e}")
        await _report_llm_failure(
            source="description_compression", error=e, agent_id=agent_id
        )
        return long_description[:1000] + "..."


async def update_interaction_stats(
    repo: SocialNetworkRepository,
    entity_id: str,
    instance_id: str,
) -> None:
    """Increment interaction counter and update last_interaction_time.

    Not cosmetic: ``should_update_persona`` fires every N interactions, so
    a counter that silently stops incrementing also silently stops persona
    refreshes — one swallowed exception disabling a second feature is
    exactly the compounding this file's 2026-08-24 pass is about.
    """
    try:
        await repo.increment_interaction(entity_id=entity_id, instance_id=instance_id)
    except Exception as e:
        logger.exception(f"Error updating interaction stats: {e}")
        await _report_write_failure(
            operation="update_interaction_stats", error=e,
            entity_id=entity_id, instance_id=instance_id,
        )


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
    *,
    agent_id: str,
) -> Optional[str]:
    """
    Infer Persona using LLM.

    Returns:
        The inferred persona description, or ``None`` if the call FAILED.

    Failure used to return ``entity.persona`` — the current value — which
    the caller then wrote straight back. A no-op write is indistinguishable
    from a successful refresh, so a dead LLM looked exactly like a persona
    that simply was not changing. Returning None lets the caller skip the
    write and lets the failure be reported.
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

        sdk = get_helper_sdk()
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
            # "" = the call worked and produced nothing to change. NOT
            # ``entity.persona``: writing the current value back looks
            # exactly like a successful refresh to the caller and to the
            # log, which is the same false-success this pass exists to
            # end — it was only half-fixed while this branch survived.
            logger.warning("            LLM returned empty persona")
            return ""

    except Exception as e:
        logger.exception(f"            Error inferring persona: {e}")
        await _report_llm_failure(
            source="persona_inference", error=e, agent_id=agent_id,
            source_id=entity.entity_id or "",
        )
        return None


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
        await _report_write_failure(
            operation="update_entity_persona", error=e,
            entity_id=entity_id, instance_id=instance_id,
        )
