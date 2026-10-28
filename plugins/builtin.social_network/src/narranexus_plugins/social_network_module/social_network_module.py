"""
@file_name: social_network_module.py
@author: NetMind.AI
@date: 2025-11-21
@description: Social Network Module - Provides social network recording and search capabilities

Per the design document:
- Social Network Module provides the ability to record and search interaction entities
- Contains: Instructions, Tools (MCP), Data (social_network_entities table), Hooks
- Core features:
  1. Record interaction entity information (identity, expertise, contact info)
  2. Intelligent social network search
  3. Automatically load relevant information at the right time
"""

import asyncio
from typing import Optional, Dict, Any, List
from loguru import logger
from datetime import datetime

# Module (same package)
from narranexus.platform.module_system import XYZBaseModule, mcp_server_url

# Schema
from narranexus.platform.schema.module_schema import ModuleAgentInstance, ModuleDisplay, ModuleDecisionMeta
from narranexus.platform.schema import (
    AGENT_TEXT_MAX_LENGTH,
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    ModuleInstructions,
    HookAfterExecutionParams,
)

# Utils
from narranexus.platform.utils import DatabaseClient

# Repository
from narranexus.platform.repository import SocialNetworkRepository, SocialNetworkEntity, InstanceRepository

# Prompts
from narranexus_plugins.social_network_module.prompts import (
    SOCIAL_NETWORK_MODULE_INSTRUCTIONS,
    SOCIAL_NETWORK_MODULE_INSTRUCTIONS_STABLE,
)
from narranexus_plugins.social_network_module._social_mcp_tools import create_social_network_mcp_server

# Settings (leaf module, safe to import at module level)
from narranexus.platform.settings import settings

# Entity update pipeline (LLM-powered)
from narranexus_plugins.social_network_module._entity_updater import (
    summarize_new_entity_info,
    append_to_entity_description,
    update_interaction_stats,
    should_update_persona,
    infer_persona,
    update_entity_persona,
    extract_mentioned_entities,
    _report_write_failure,
)

# ``operation`` values for the caller-side memory-write audit rows. Module
# constants rather than inline literals so the test can assert the VALUE
# instead of grepping this file's source text — a source-reading test goes
# green when the call is deleted but the string survives in a comment, and
# red when the call moves to a helper.
_OP_CREATE_PRIMARY_ENTITY = "create_primary_entity"
_OP_PROCESS_MENTIONED_ENTITY = "process_mentioned_entity"


def social_instance_not_found_msg(agent_id: str) -> str:
    """The one canonical "agent has no SocialNetworkModule instance" message.

    Shared so the AgentDataStore seam's DirectStore and the backend
    `/social-network/*` write routes (the HttpStore path) return byte-identical
    text — otherwise the two migration paths would diverge on this edge case.
    Wording matches this file's sibling GET-route errors ("... for agent: X")."""
    return f"No SocialNetworkModule instance found for agent: {agent_id}"


def format_contact_result(entity_id: str, recall: dict) -> dict:
    """Shape ``recall_entity_info``'s result into the ``get_contact_info`` MCP
    tool's return dict. Shared by the seam's DirectStore and the
    `/social-network/contact` route (HttpStore path) so both produce byte-
    identical output — the tool's presentation logic lives here once instead of
    being hand-copied into the route."""
    if recall.get("success"):
        entity = recall.get("entity", {}) or {}
        return {
            "success": True,
            "entity_id": entity_id,
            "entity_name": entity.get("entity_name"),
            "contact_info": entity.get("contact_info", {}),
        }
    return {"success": False, "message": recall.get("message")}


def format_stats_result(sort_by: str, stats: list) -> dict:
    """Shape ``get_agent_stats``' list into the ``get_agent_social_stats`` MCP
    tool's return dict. Shared by DirectStore and the `/social-network/stats`
    route so both agree (see ``format_contact_result``)."""
    return {"success": True, "sort_by": sort_by, "count": len(stats), "results": stats}


# create_agent shares its response wording between the seam's DirectStore and the
# /social-network/create-agent route so both return byte-identical output for the
# same (agent_name, new_agent_id) — the new agent id is MINTED BY THE TOOL and
# threaded through as an input (not generated independently on each path), which
# is what makes create-with-a-random-id parity-able at all.
CREATE_AGENT_NO_OWNER_MSG = "Cannot determine your owner (created_by). Aborting."

# A nameless agent renders its raw agent_id in every surface, and a
# whitespace-only one renders as blank — less identifiable still. The rename
# paths (update_agent_profile and PUT /api/auth/agents) both refuse this
# already; creation used to accept it, which meant the same value was legal to
# create and illegal to change to. Shared between the two create paths for the
# same byte-parity reason as the message above.
CREATE_AGENT_EMPTY_NAME_MSG = "agent_name cannot be empty. Aborting."

# Over-length is refused by BOTH legs with this one string, for the same reason
# the empty-name message is shared — and specifically NOT by a `max_length` on
# the route's body model. A model-level cap makes the HTTP leg answer 422 before
# the handler runs, so the caller gets a transport-level string ("invalid
# arguments…") while the DirectStore leg answers with the pydantic message
# `Agent(...)` raises inside add_agent. Same tool call, two answers, and the
# cloud one carries no number the model could correct itself with.
CREATE_AGENT_TEXT_TOO_LONG_MSG = (
    f"agent_name and agent_description must each be at most "
    f"{AGENT_TEXT_MAX_LENGTH} characters. Aborting."
)


def create_agent_text_reject(
    agent_name: str, agent_description: str
) -> Optional[str]:
    """The refusal rule for create_agent's text arguments, in one place.

    Returns the sentence to hand back, or None to proceed. Takes ALREADY
    NORMALIZED values — normalization stays with the caller, which needs the
    normalized strings afterwards for the write, the echo and the log.

    A string, not a response dict: the route answers under an ``error`` key and
    DirectStore under ``message`` (folded back by the seam's
    ``_write_message_key``), so the shape belongs to each leg and only the
    wording is shared.

    THE ORDER IS PART OF THE RULE and therefore lives here, not in the callers:
    empty before length, or `" " * 300` gets refused as over-long — consistent
    between the legs, and wrong. Sharing the two message constants but writing
    this sequence twice is how the legs drifted in the first place; every future
    create-agent check belongs in this function, not in a caller.
    """
    if not agent_name:
        return CREATE_AGENT_EMPTY_NAME_MSG
    if (
        len(agent_name) > AGENT_TEXT_MAX_LENGTH
        or len(agent_description) > AGENT_TEXT_MAX_LENGTH
    ):
        return CREATE_AGENT_TEXT_TOO_LONG_MSG
    return None


def default_created_by_description(author: str) -> str:
    """The description an agent-created agent gets when none was supplied.

    Clamped rather than refused: this string is ours, not the caller's, and it
    is the one value on the path assembled AFTER the checks above (17 chars plus
    a creator name that may itself sit near the ceiling). Refusing a value the
    caller never typed cannot be explained to them.
    """
    return f"Agent created by {author}"[:AGENT_TEXT_MAX_LENGTH]


def format_create_agent_success(agent_name: str, new_agent_id: str, warnings: list | None = None) -> dict:
    """Build the create_agent success dict. Any non-fatal provisioning warning
    (a half-provisioned agent, incident lesson #5) is surfaced when present —
    both the tool path and the route now do this, so they don't diverge."""
    out: dict = {
        "success": True,
        "message": (
            f"Agent '{agent_name}' created successfully (ID: {new_agent_id}). "
            f"The user can now switch to this agent in the frontend. "
            f"If further configuration is needed (skills, jobs, etc.), "
            f"tell the user to interact with the new agent directly."
        ),
        "new_agent_id": new_agent_id,
        "agent_name": agent_name,
    }
    if warnings:
        out["warnings"] = warnings
    return out


class SocialNetworkModule(XYZBaseModule):
    """
    Social Network Module

    Provides social network capabilities:
    1. **Instructions** - Tells the Agent how to use social network features
    2. **Tools (MCP)** - Provides extract_entity_info, recall_entity, search_social_network tools
    3. **Data** - Stored in social_network_entities table
    4. **Hooks**:
       - gather: Load known information about the current interaction entity
       - after_turn: Summarize and update entity information (Phase 2 implementation)
    """

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None
    ):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)

        # Initialize repository (lazy initialization, since db may only be available at call time)
        self._social_repo: Optional[SocialNetworkRepository] = None

        # Build instructions, dynamically insert agent_id (via .replace, not
        # .format — the template contains other literal braces).
        # Legacy vs stable: contribute_instructions() picks per the R4 relocation
        # flag; agent_id is baked into both once here.
        self._instructions_legacy = SOCIAL_NETWORK_MODULE_INSTRUCTIONS.replace("{agent_id}", agent_id)
        self._instructions_stable = SOCIAL_NETWORK_MODULE_INSTRUCTIONS_STABLE.replace("{agent_id}", agent_id)
        self.instructions = self._instructions_legacy

    def _get_repo(self) -> SocialNetworkRepository:
        """Get or create SocialNetworkRepository instance"""
        if self._social_repo is None:
            self._social_repo = SocialNetworkRepository(self.db, self.agent_id)
        return self._social_repo

    async def _get_instance_id(self) -> Optional[str]:
        """
        Get the current Module's instance_id

        Prefers self.instance_id; if None, looks up via agent_id + module_class.
        SocialNetworkModule is an Agent-level module (is_public=1), each Agent has only one instance.
        """
        if self.instance_id:
            return self.instance_id

        # Look up via agent_id + module_class
        try:
            instance_repo = InstanceRepository(self.db)
            instances = await instance_repo.get_by_agent(
                agent_id=self.agent_id,
                module_class="SocialNetworkModule"
            )
            if instances:
                self.instance_id = instances[0].instance_id
                return self.instance_id
        except Exception as e:
            logger.warning(f"Failed to get instance_id: {e}")

        return None

    @staticmethod
    def get_config() -> ModuleConfig:
        """
        Return SocialNetworkModule configuration
        """
        return ModuleConfig(
            name="SocialNetworkModule",
            agent_instance=ModuleAgentInstance(description="Agent social network and entity relationship management", keywords=["social", "network", "relationship", "entity"], topic_hint="Social relationship network, user and entity information"),
            role="social_network",
            default=True,
            instance_prefix="social",
            display=ModuleDisplay(icon="👥", name="SocialNetwork", desc="Entity CRUD, relationship graph"),
            decision=ModuleDecisionMeta(
                capabilities=["Manage user social relationships", "Handle social interactions (follow, friends, etc.)", "Social network data analysis"],
                use_cases=["Social feature related conversations", "User relationship management", "Social data queries"],
                instance_type="persistent",
                typical_instance_id="social_{uuid8}",
            ),
            priority=3,  # Medium priority
            enabled=True,
            description="Provides social network recording and search capabilities"
        )

    # ============================================================================= Instructions

    async def contribute_instructions(self, ctx_data: ContextData) -> str:
        """Render the module instruction, selecting the template by the R4
        relocation flag.

        Flag ON  → stable template (§5 entity card replaced by a static
                   pointer) so the output is byte-stable across turns; the
                   card travels via contribute_turn_context() instead.
        Flag OFF → untouched legacy template, byte-identical to pre-R4.
        """
        self.instructions = (
            self._instructions_stable
            if settings.prompt_turn_context_relocation_enabled
            else self._instructions_legacy
        )
        return await super().contribute_instructions(ctx_data)

    async def contribute_turn_context(self, ctx_data: ContextData) -> str:
        """Per-turn volatile span: the current-entity card (§5).

        Carries ctx_data.social_network_current_entity exactly as
        gather rendered it — known-entity card, first-meeting /
        no-user-context / load-error fallbacks alike (R4: relocated, never
        dropped; the error text with its exception string is precisely the
        kind of volatile content that must stay out of the system prompt).
        """
        entity = getattr(ctx_data, "social_network_current_entity", None)
        if not entity:
            return ""
        return f"##### Current User Information\n{entity}"

    # ============================================================================= Hooks

    async def gather(self, ctx_data: ContextData) -> ContextData:
        """
        Load current interaction entity info when building context

        Functionality:
        1. Load known information for the current user_id
        2. Add information to ctx_data so the Agent knows "what I already know about the other party"
        3. Intent recognition (Phase 2): detect if other entities are mentioned or search is needed

        Refactoring notes (2025-12-24):
        - Uses instance_id to query instance_social_entities table
        - instance_id is obtained via _get_instance_id()

        Args:
            ctx_data: Context data

        Returns:
            Enriched ContextData
        """
        logger.debug("          → SocialNetworkModule.gather() started")

        try:
            # Get instance_id
            instance_id = await self._get_instance_id()
            if not instance_id:
                logger.warning("            No instance_id found, skipping social network data loading")
                ctx_data.social_network_current_entity = """**First time meeting this user.**

                    Remember to call `extract_entity_info` immediately when they introduce themselves or share personal information."""
                return ctx_data

            # 1. Load current interaction entity info
            if ctx_data.user_id:
                logger.debug(f"            Loading entity info for user_id: {ctx_data.user_id}, instance_id: {instance_id}")

                # Primary: exact entity_id match
                entity = await self._get_repo().get_entity(
                    entity_id=ctx_data.user_id,
                    instance_id=instance_id
                )

                # Fallback: fuzzy match by sender_name from channel_tag or keyword search
                if not entity:
                    entity = await self._fuzzy_find_entity(ctx_data, instance_id)

                if entity:
                    logger.debug(f"            ✓ Found entity: {entity.entity_name}")

                    # Add entity info to context
                    # This way the Agent can see this info in instructions
                    entity_info = {
                        "entity_id": entity.entity_id,
                        "entity_name": entity.entity_name,
                        "entity_description": entity.entity_description,
                        "identity_info": entity.identity_info or {},
                        "keywords": entity.keywords or [],
                        "interaction_count": entity.interaction_count,
                        "relationship_strength": entity.relationship_strength,
                        "last_interaction_time": entity.last_interaction_time.isoformat() if entity.last_interaction_time else None
                    }

                    # Format as readable display text
                    display_text = f"""**You already know this user:**
                        - Name: {entity.entity_name}
                        - Description: {entity.entity_description or 'N/A'}
                        - Keywords: {', '.join(entity.keywords) if entity.keywords else 'None'}
                        - Previous interactions: {entity.interaction_count}
                        - Last contact: {entity.last_interaction_time.strftime('%Y-%m-%d') if entity.last_interaction_time else 'N/A'}

                        Use this information to provide personalized responses and build on previous conversations."""

                    # Inject Persona (if available)
                    if entity.persona:
                        display_text += f"""

**Communication Persona for {entity.entity_name}:**
{entity.persona}

Adapt your communication style according to this persona."""

                    ctx_data.social_network_current_entity = display_text
                    logger.info(f"            ✓ Loaded social network info for {entity.entity_name}")

                    # === Option C: Write related_job_ids to extra_data ===
                    # Allow JobModule to read and load Job context in subsequent gather
                    if entity.related_job_ids:
                        ctx_data.extra_data["related_job_ids"] = entity.related_job_ids
                        ctx_data.extra_data["current_entity_id"] = entity.entity_id
                        ctx_data.extra_data["current_entity_name"] = entity.entity_name
                        logger.info(f"            ✓ Wrote related_job_ids to extra_data: {entity.related_job_ids}")
                else:
                    logger.debug(f"            ℹ No existing entity info for {ctx_data.user_id}")
                    ctx_data.social_network_current_entity = """**First time meeting this user.**

                    Remember to call `extract_entity_info` immediately when they introduce themselves or share personal information."""
            else:
                # Case where user_id is empty (e.g., anonymous user or system call)
                logger.debug("            ℹ No user_id in ctx_data, skipping social network lookup")
                ctx_data.social_network_current_entity = """**No user context available.**

                    Social network features are available when interacting with identified users."""

            # 2. Load known agent entities for cross-module use (e.g. MessageBusModule)
            #    Subproject 1: also union team-derived agents (same-team members get auto-known)
            try:
                agent_entities = await self._get_repo().get_all_entities(
                    instance_id=instance_id,
                    entity_type="agent",
                    limit=50,
                )

                # Build merged dict keyed by entity_id; declared first.
                merged: dict[str, dict] = {}
                for e in agent_entities:
                    merged[e.entity_id] = {
                        "entity_id": e.entity_id,
                        "entity_name": e.entity_name,
                        "entity_description": e.entity_description or "",
                        "keywords": e.keywords or [],
                        "contact_info": e.contact_info or {},
                        "team_membership": [],
                        "source": "declared",
                    }

                # Derived: team-mates of the current agent.
                try:
                    from narranexus.platform.repository import TeamMemberRepository, AgentRepository
                    from narranexus.platform.utils.db.db_factory import get_db_client
                    db = await get_db_client()
                    member_repo = TeamMemberRepository(db)
                    agent_repo = AgentRepository(db)
                    self_agent_id = ctx_data.agent_id if hasattr(ctx_data, "agent_id") else None
                    if self_agent_id:
                        mates = await member_repo.list_team_mates(self_agent_id)
                        for m in mates:
                            mate_id = m["agent_id"]
                            mate_team = m["team_id"]
                            if mate_id in merged:
                                if mate_team not in merged[mate_id]["team_membership"]:
                                    merged[mate_id]["team_membership"].append(mate_team)
                                if merged[mate_id]["source"] == "declared":
                                    merged[mate_id]["source"] = "both"
                            else:
                                mate_agent = await agent_repo.get_agent(mate_id)
                                if not mate_agent:
                                    continue
                                merged[mate_id] = {
                                    "entity_id": mate_id,
                                    "entity_name": mate_agent.agent_name,
                                    "entity_description": mate_agent.agent_description or "",
                                    "keywords": [],
                                    "contact_info": {},
                                    "team_membership": [mate_team],
                                    "source": "derived",
                                }
                except Exception as derive_exc:
                    logger.warning(f"            Failed to derive team-mates: {derive_exc}")

                if merged:
                    # Cap to 50; declared entries (registered earlier) take priority.
                    ctx_data.extra_data["known_agent_entities"] = list(merged.values())[:50]
                    logger.info(
                        f"            ✓ Loaded {len(merged)} known agent entities "
                        f"(declared + derived merged) to extra_data"
                    )
            except Exception as exc:
                logger.warning(f"            Failed to load known agent entities: {exc}")

            logger.debug("          ← SocialNetworkModule.gather() completed")

        except Exception as e:
            logger.exception(f"Error in gather: {e}")
            logger.exception(e)
            # Set error state value to ensure contribute_instructions doesn't fail due to missing fields
            # Also clearly indicate an error occurred for debugging
            ctx_data.social_network_current_entity = f"""**⚠️ Social network data loading failed.**

Error: {type(e).__name__}: {str(e)[:100]}

Please check the database connection and ensure all required tables exist.
Tables are auto-created on startup via schema_registry.auto_migrate()."""

        return ctx_data

    async def after_turn(self, params: HookAfterExecutionParams) -> None:
        """
        Automatically update entity_description after Event execution

        Functionality:
        1. Detect if the conversation contains identity information keywords
        2. If so, call LLM to summarize new information
        3. Append to entity_description (cumulative, not overwriting)
        4. Update interaction_count and last_interaction_time

        Division of responsibilities with extract_entity_info:
        - extract_entity_info: Actively called by Agent, updates structured info (tags, contact_info, identity_info)
        - after_turn: Automatically executed, cumulatively updates natural language description (entity_description)

        Refactoring notes (2025-12-24):
        - Uses instance_id to query and update instance_social_entities table
        - instance_id is obtained via _get_instance_id()

        Args:
            params: HookAfterExecutionParams, containing:
                - execution_ctx: Execution context (event_id, agent_id, user_id, working_source)
                - io_data: Input/output (input_content, final_output)
                - trace: Execution trace (event_log, agent_loop_response)
                - ctx_data: Complete context data
        """
        logger.debug("          → SocialNetworkModule.after_turn() started")

        try:
            # Get instance_id
            instance_id = await self._get_instance_id()
            if not instance_id:
                logger.warning("            No instance_id found, skipping hook")
                return

            # Get parameters (via convenience property access)
            user_id = params.user_id
            input_content = params.input_content
            final_output = params.final_output

            if not user_id:
                user_id = self.user_id

            if not user_id:
                logger.warning("No user_id found, skipping hook")
                return

            logger.debug(f"            Processing hook for user_id: {user_id}, instance_id: {instance_id}")

            # 0. Check if entity exists — single fetch, reused throughout
            repo = self._get_repo()
            entity = await repo.get_entity(
                entity_id=user_id,
                instance_id=instance_id
            )

            if not entity:
                logger.info(f"            Entity {user_id} not found, creating minimal entity")
                try:
                    await repo.add_entity(
                        entity_id=user_id,
                        entity_type="user",
                        instance_id=instance_id,
                        entity_name=user_id,
                        entity_description="",
                        familiarity="direct",
                    )
                    logger.info(f"            ✓ Created minimal entity for {user_id}")
                    entity = await repo.get_entity(entity_id=user_id, instance_id=instance_id)
                except Exception as e:
                    # The heaviest failure on this path: no primary entity
                    # means the summary, the counter and the persona all
                    # skip this turn — "memory stopped updating" with, until
                    # now, zero durable trace.
                    logger.exception(f"Failed to create entity: {e}")
                    await _report_write_failure(
                        operation=_OP_CREATE_PRIMARY_ENTITY, error=e,
                        entity_id=user_id, instance_id=instance_id,
                        agent_id=self.agent_id,
                    )
                    return

            primary_name = entity.entity_name or user_id if entity else user_id

            # Get agent's own name for self-exclusion in extraction
            agent_name = ""
            if params.ctx_data:
                agent_name = getattr(params.ctx_data, 'agent_name', '') or ''

            # 1. Run independent LLM calls in parallel: summary + batch extraction
            new_summary, mentioned = await asyncio.gather(
                summarize_new_entity_info(
                    input_content, final_output, agent_id=self.agent_id
                ),
                extract_mentioned_entities(
                    input_content, final_output, primary_name,
                    agent_name=agent_name, agent_id=self.agent_id,
                ),
            )

            # 2. Process summary results.
            # `None` means the LLM call FAILED (already reported by
            # _entity_updater); `""` means it ran and found nothing worth
            # remembering. Both skip the write, but only one is a problem
            # — which is the whole reason the two are distinguishable now.
            if new_summary is None:
                logger.warning(
                    "            Entity summary unavailable this turn "
                    "(LLM call failed); description write skipped"
                )
            elif new_summary.strip():
                logger.info(f"            New summary generated: {new_summary[:100]}...")
                await append_to_entity_description(
                    repo, user_id, instance_id, new_summary,
                    agent_id=self.agent_id,
                )

            # 3. Update interaction stats (always)
            await update_interaction_stats(repo, user_id, instance_id)

            # 4. Persona update (conditional) — re-fetch entity to get updated description
            entity = await repo.get_entity(entity_id=user_id, instance_id=instance_id)
            if entity and should_update_persona(entity, input_content):
                logger.info(f"            Updating persona for {user_id}...")

                awareness = ""
                job_info_str = ""
                if params.ctx_data:
                    awareness = getattr(params.ctx_data, 'awareness', '') or ''
                    if hasattr(params.ctx_data, 'extra_data') and params.ctx_data.extra_data:
                        related_jobs = params.ctx_data.extra_data.get('related_jobs_context', [])
                        if related_jobs:
                            job_info_str = str(related_jobs)

                recent_conversation = f"User: {input_content}\nAgent: {final_output}"
                new_persona = await infer_persona(
                    entity=entity, awareness=awareness,
                    job_info=job_info_str, recent_conversation=recent_conversation,
                    agent_id=self.agent_id,
                )
                # None = the call failed (reported downstream). Writing the
                # old value back would look exactly like a successful
                # refresh, so skip instead.
                if new_persona:
                    await update_entity_persona(repo, user_id, instance_id, new_persona)

            logger.info(f"Entity updated for {user_id}")

            # 5. Process mentioned entities (dedup pipeline)
            try:
                await self._process_mentioned_entities(repo, instance_id, mentioned)
            except Exception as e:
                # NOT "batch entity extraction" — that label was copied from
                # the extract_mentioned_entities handler and sent whoever
                # read it to the wrong LLM call. This is the dedup/write
                # pipeline. Per-entity failures are already reported inside
                # _process_mentioned_entities; this outer catch only sees
                # what that loop could not (and must keep not raising —
                # a dying hook would take Step-6 callbacks with it).
                logger.warning(
                    f"            Mentioned-entity dedup pipeline failed "
                    f"(non-critical): {e}"
                )

            # NOTE: entity writes above (add/update via SocialNetworkRepository)
            # now land directly in the unified memory_entity store — the repo IS
            # the engine. The old `_feed_entity_to_engine` mirror is gone: there
            # is no separate source table to sync from anymore (unified-memory
            # overhaul task 1). This also fixes the third-party-entity gap, since
            # _process_mentioned_entities writes go straight to the engine too.

        except Exception as e:
            logger.exception(f"Error in after_turn: {e}")

        logger.debug("          ← SocialNetworkModule.after_turn() completed")


    async def _process_mentioned_entities(self, repo, instance_id: str, mentioned: list) -> None:
        """
        2-stage dedup pipeline for entities extracted from conversation.

        For each mentioned entity:
          Stage 1: Exact name+alias match → if 1 match, UPDATE; if >1, ask
                   LLM to MERGE / CREATE_NEW
          Stage 2: No match found → CREATE_NEW

        Note (2026-05-27): the previous Stage 2 "vector similarity search"
        was removed together with the rest of the semantic / embedding
        chain (Owner spec). Entities with different spellings of the same
        person (e.g. "Bob" vs "Robert") now produce separate rows; merge
        them manually if it matters. The pre-existing user-facing
        `search_social_network` is keyword-only (LIKE on name / description
        / tags / aliases), which makes the embedding write path dead
        weight that we are no longer paying for.
        """
        from narranexus_plugins.social_network_module._entity_updater import (
            append_to_entity_description,
            decide_merge_or_create,
        )

        for mentioned_entity in mentioned:
            # Outside the try on purpose: the except below reports with this
            # id, so leaving the assignment as the try's first statement
            # made the error path depend on it having succeeded — first
            # iteration would raise NameError from inside the except (taking
            # the rest of the batch with it), later ones would file the
            # failure under the PREVIOUS entity's id. ``name`` is a required
            # pydantic ``str``, so this cannot raise today; the point is that
            # the error path should not rest on that.
            entity_id_candidate = f"entity_{mentioned_entity.name.lower().replace(' ', '_')}"
            try:
                candidate_aliases = getattr(mentioned_entity, 'aliases', [])
                candidate_familiarity = getattr(mentioned_entity, 'familiarity', 'known_of')

                # ── STAGE 1: Exact name+alias match ──
                matches = await repo.search_by_name_or_alias(
                    instance_id=instance_id,
                    name=mentioned_entity.name,
                )
                for alias in candidate_aliases:
                    alias_matches = await repo.search_by_name_or_alias(
                        instance_id=instance_id,
                        name=alias,
                    )
                    seen_ids = {m.entity_id for m in matches}
                    for m in alias_matches:
                        if m.entity_id not in seen_ids:
                            matches.append(m)
                            seen_ids.add(m.entity_id)

                existing = None
                if len(matches) == 1:
                    # Single exact match — high confidence, skip LLM
                    existing = matches[0]
                    logger.info(
                        f"            Stage 1 exact match: '{mentioned_entity.name}' "
                        f"→ {existing.entity_name} ({existing.entity_id})"
                    )
                elif len(matches) > 1:
                    # Multiple matches — single LLM call with all candidates
                    logger.info(
                        f"            Stage 1 found {len(matches)} name/alias matches, "
                        f"asking LLM to pick"
                    )
                    decision, matched = await decide_merge_or_create(
                        mentioned_entity.name, mentioned_entity.summary,
                        candidate_aliases, matches,
                        agent_id=self.agent_id,
                    )
                    if decision == "MERGE" and matched:
                        existing = matched

                # ── UPDATE existing or CREATE NEW ──
                if existing:
                    matched_id = existing.entity_id
                    if mentioned_entity.summary:
                        await append_to_entity_description(
                            repo, matched_id, instance_id, mentioned_entity.summary,
                            agent_id=self.agent_id,
                        )
                    # Merge keywords
                    if mentioned_entity.keywords:
                        existing_kws = list(existing.keywords or [])
                        existing_lower = {k.lower() for k in existing_kws}
                        for kw in mentioned_entity.keywords:
                            if kw.lower() not in existing_lower:
                                existing_kws.append(kw)
                                existing_lower.add(kw.lower())
                        if len(existing_kws) > 10:
                            existing_kws = existing_kws[:10]
                        await repo.update_entity_info(
                            entity_id=matched_id, instance_id=instance_id,
                            updates={"tags": existing_kws},
                        )
                    # Merge aliases
                    if candidate_aliases:
                        existing_aliases = list(existing.aliases or [])
                        existing_alias_lower = {a.lower() for a in existing_aliases}
                        for a in candidate_aliases:
                            if a.lower() not in existing_alias_lower:
                                existing_aliases.append(a)
                                existing_alias_lower.add(a.lower())
                        await repo.update_entity_info(
                            entity_id=matched_id, instance_id=instance_id,
                            updates={"aliases": existing_aliases},
                        )
                else:
                    await repo.add_entity(
                        entity_id=entity_id_candidate,
                        entity_type=mentioned_entity.entity_type,
                        instance_id=instance_id,
                        entity_name=mentioned_entity.name,
                        entity_description=mentioned_entity.summary,
                        keywords=mentioned_entity.keywords,
                        aliases=candidate_aliases,
                        familiarity=candidate_familiarity,
                    )
                    logger.info(
                        f"            Created new entity: "
                        f"{mentioned_entity.name} ({entity_id_candidate})"
                    )
            except Exception as e:
                # Covers every write in this loop: creating the mentioned
                # entity itself, the tags/aliases merges, and the Stage-1
                # name/alias LOOKUP. A failed lookup is the quiet one —
                # no match found means "brand new", so every turn forks
                # another duplicate node, the same shredding the dedup
                # handler reports for its own failures.
                logger.warning(f"            Failed to process entity '{mentioned_entity.name}': {e}")
                await _report_write_failure(
                    operation=_OP_PROCESS_MENTIONED_ENTITY, error=e,
                    entity_id=entity_id_candidate, instance_id=instance_id,
                    agent_id=self.agent_id,
                )

    # ============================================================================= MCP Server

    async def mcp_server(self) -> Optional[MCPServerConfig]:
        """
        Return MCP Server configuration

        SocialNetworkModule needs to provide an MCP Server for:
        - extract_entity_info tool
        - recall_entity tool
        - search_social_network tool
        - get_contact_info tool

        Returns:
            MCPServerConfig
        """
        return MCPServerConfig(
            server_name="social_network_module",
            server_url=mcp_server_url("social_network_module"),
            type="sse"
        )

    def create_mcp_server(self) -> Optional[Any]:
        """
        Create MCP Server instance

        Tool definitions have been extracted to _social_mcp_tools.py.
        """
        return create_social_network_mcp_server()

    # ============================================================================= Helper Methods

    async def _fuzzy_find_entity(
        self,
        ctx_data: ContextData,
        instance_id: str,
    ) -> Optional[SocialNetworkEntity]:
        """
        Fuzzy match entity when exact entity_id lookup fails.

        Strategy:
        1. Extract sender_name from channel_tag (if available)
        2. Keyword search across entity_name and entity_description
        3. Return the best match (highest interaction_count)

        Args:
            ctx_data: Context data (may contain channel_tag in extra_data)
            instance_id: SocialNetworkModule instance ID

        Returns:
            Best matching SocialNetworkEntity, or None
        """
        # Collect candidate search keywords
        keywords = []

        # From channel_tag sender_name
        if ctx_data.extra_data:
            channel_tag = ctx_data.extra_data.get("channel_tag")
            if channel_tag:
                # Handle both dict and ChannelTag object
                if isinstance(channel_tag, dict):
                    sender_name = channel_tag.get("sender_name", "")
                elif hasattr(channel_tag, "sender_name"):
                    sender_name = channel_tag.sender_name
                else:
                    sender_name = ""
                if sender_name and sender_name != ctx_data.user_id:
                    keywords.append(sender_name)

        if not keywords:
            return None

        repo = self._get_repo()
        for keyword in keywords:
            results = await repo.keyword_search(
                instance_id=instance_id,
                keyword=keyword,
                limit=3,
            )
            if results:
                # Return the entity with the highest interaction count
                best = max(results, key=lambda e: e.interaction_count or 0)
                logger.info(
                    f"            Fuzzy match found: '{keyword}' -> {best.entity_name} "
                    f"(entity_id={best.entity_id})"
                )
                return best

        return None

    async def _load_entity_info(self, entity_id: str, instance_id: str) -> Optional[Dict[str, Any]]:
        """
        Load entity information (internal use)

        Args:
            entity_id: Entity ID
            instance_id: Instance ID

        Returns:
            Entity info dict or None
        """
        entity = await self._get_repo().get_entity(
            entity_id=entity_id,
            instance_id=instance_id
        )

        if not entity:
            return None

        return {
            "entity_id": entity.entity_id,
            "entity_name": entity.entity_name,
            "entity_description": entity.entity_description,
            "identity_info": entity.identity_info or {},
            "contact_info": entity.contact_info or {},
            "keywords": entity.keywords or [],
            "interaction_count": entity.interaction_count,
            "relationship_strength": entity.relationship_strength,
        }

    async def _search_entities(
        self,
        search_keyword: str,
        instance_id: str,
        search_type: str = "auto"
    ) -> List[Dict[str, Any]]:
        """
        Search entities (internal use)

        Args:
            search_keyword: Search keyword
            instance_id: Instance ID
            search_type: Search type (auto | exact_id | tags | semantic)

        Returns:
            List of matching entity information
        """
        # Auto: auto-detect type
        if search_type == "auto":
            # If starts with "user_", "entity_", or "agent_", treat as entity_id
            if search_keyword.startswith(("user_", "entity_", "agent_")):
                search_type = "exact_id"
            else:
                search_type = "keyword"  # Search by name + description + keywords + aliases

        # Exact ID: exact lookup
        repo = self._get_repo()
        if search_type == "exact_id":
            entity = await repo.get_entity(
                entity_id=search_keyword,
                instance_id=instance_id
            )

            if entity:
                return [{
                    "entity_id": entity.entity_id,
                    "entity_name": entity.entity_name,
                    "entity_description": entity.entity_description,
                    "identity_info": entity.identity_info or {},
                    "keywords": entity.keywords or [],
                    "contact_info": entity.contact_info or {},
                    "relationship_strength": entity.relationship_strength,
                    "interaction_count": entity.interaction_count,
                }]
            else:
                return []

        # Tags: tag search
        if search_type == "tags":
            entities = await repo.search_by_tags(
                instance_id=instance_id,
                search_keyword=search_keyword
            )

            return [
                {
                    "entity_id": e.entity_id,
                    "entity_name": e.entity_name,
                    "entity_description": e.entity_description,
                    "keywords": e.keywords or [],
                    "contact_info": e.contact_info or {},
                    "relationship_strength": e.relationship_strength,
                }
                for e in entities
            ]

        # Keyword: search by name, description, keywords, aliases
        # 2026-05-27: also the destination of `auto` since the only other
        # historical option (`semantic`) was removed together with the
        # entity-embedding chain. Anything that does not look like an
        # ID (handled in the exact_id branch above) falls here.
        if search_type == "keyword":
            entities = await repo.keyword_search(
                instance_id=instance_id,
                keyword=search_keyword,
                limit=10,
            )
            return [
                {
                    "entity_id": e.entity_id,
                    "entity_name": e.entity_name,
                    "entity_description": e.entity_description,
                    "keywords": e.keywords or [],
                    "contact_info": e.contact_info or {},
                    "relationship_strength": e.relationship_strength,
                    "interaction_count": e.interaction_count,
                }
                for e in entities
            ]

        return []

    async def get_agent_stats(
        self,
        instance_id: str,
        sort_by: str = "recent",
        top_k: int = 10,
        filter_tags: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get Agent's social network statistics (internal use)

        Args:
            instance_id: Instance ID
            sort_by: Sort method (recent | frequent | strong)
            top_k: Number of results to return
            filter_tags: Tag filter

        Returns:
            Sorted entity list (with full info and description)
        """
        try:
            # Get all entities belonging to this Instance
            entities = await self._get_repo().get_all_entities(
                instance_id=instance_id,
                limit=1000  # Large enough limit
            )

            if not entities:
                return []

            # Filter by tags
            if filter_tags:
                entities = [
                    e for e in entities
                    if e.keywords and any(tag in e.keywords for tag in filter_tags)
                ]

            # Sort
            if sort_by == "recent":
                # Sort by last interaction time descending
                entities.sort(
                    key=lambda e: e.last_interaction_time or datetime.min,
                    reverse=True
                )
            elif sort_by == "frequent":
                # Sort by interaction count descending
                entities.sort(
                    key=lambda e: e.interaction_count,
                    reverse=True
                )
            elif sort_by == "strong":
                # Sort by relationship strength descending
                entities.sort(
                    key=lambda e: e.relationship_strength,
                    reverse=True
                )

            # Limit quantity
            entities = entities[:top_k]

            # Format return (including description!)
            return [
                {
                    "entity_id": e.entity_id,
                    "entity_name": e.entity_name,
                    "entity_description": e.entity_description or "No description yet",  # Key!
                    "last_interaction_time": e.last_interaction_time.isoformat() if e.last_interaction_time else None,
                }
                for e in entities
            ]

        except Exception as e:
            logger.exception(f"Error getting agent stats: {e}")
            return []

    # ============================================================================= Public API (for MCP Server)

    async def extract_and_update_entity_info(
        self,
        entity_id: str,
        instance_id: str,
        updates: Dict[str, Any],
        update_mode: str = "merge"
    ) -> Dict[str, Any]:
        """
        Extract and update entity information (called by MCP Server)

        Refactoring notes (2025-12-24):
        - Added instance_id parameter
        - Uses instance_id for data isolation

        Args:
            entity_id: Entity ID
            instance_id: Instance ID
            updates: Info dict to update
            update_mode: Update mode (merge | replace)

        Returns:
            Operation result
        """
        try:
            # Get existing entity
            repo = self._get_repo()
            existing_entity = await repo.get_entity(
                entity_id=entity_id,
                instance_id=instance_id
            )

            if existing_entity:
                # `entity_name_if_new` names a first-contact entity WITHOUT ever
                # overwriting a real name. On an existing entity it is FILL-IF-
                # EMPTY: it names an entity some other path created nameless (an
                # LLM extraction that only had contact_info), but a non-blank
                # existing name always wins — so a channel display name never
                # clobbers a canonical one.
                _name_if_new = updates.pop("entity_name_if_new", None)
                if _name_if_new and not (existing_entity.entity_name or "").strip():
                    updates["entity_name"] = _name_if_new
                # Merge update
                if update_mode == "merge":
                    # Merge identity_info
                    if "identity_info" in updates:
                        existing_info = existing_entity.identity_info or {}
                        new_info = updates["identity_info"]
                        merged_info = {**existing_info, **new_info}
                        updates["identity_info"] = merged_info

                    # Merge contact_info (deep merge + normalize)
                    if "contact_info" in updates:
                        from narranexus.platform.channel.channel_contact_utils import merge_contact_info
                        existing_contact = existing_entity.contact_info or {}
                        new_contact = updates["contact_info"]
                        updates["contact_info"] = merge_contact_info(existing_contact, new_contact)

                    # Merge keywords (case-insensitive dedup, capped at 10)
                    # Accept both "tags" and "keywords" keys for backward compat
                    if "keywords" in updates:
                        updates["tags"] = updates.pop("keywords")
                    if "tags" in updates:
                        merged = list(existing_entity.keywords or [])
                        existing_lower = {t.lower() for t in merged}
                        for new_tag in updates["tags"]:
                            if new_tag.lower() not in existing_lower:
                                merged.append(new_tag)
                                existing_lower.add(new_tag.lower())
                        if len(merged) > 10:
                            merged = merged[:10]
                        updates["tags"] = merged

                    # Protect entity_description: not allowed to update via this function
                    # entity_description should only be cumulatively updated by after_turn
                    if "entity_description" in updates:
                        logger.warning(
                            "Attempted to update entity_description via extract_entity_info. "
                            "This field is managed by after_turn only. Ignoring."
                        )
                        updates.pop("entity_description")

                # Update
                await repo.update_entity_info(
                    entity_id=entity_id,
                    instance_id=instance_id,
                    updates=updates
                )

                return {
                    "success": True,
                    "message": "Entity info updated successfully",
                    "entity_id": entity_id
                }

            else:
                # Create new entity
                entity_type = updates.pop("entity_type", "user")
                # `entity_name_if_new` names a first-contact entity WITHOUT ever
                # clobbering an existing name (the merge branch above is
                # fill-if-empty: it names a nameless existing entity but keeps a
                # non-blank one). An explicit `entity_name` still wins here.
                entity_name = updates.pop("entity_name", None) or updates.pop(
                    "entity_name_if_new", None
                )
                # Ignore entity_description, managed only by hook
                if "entity_description" in updates:
                    logger.warning(
                        "Ignoring entity_description in updates during entity creation. "
                        "This field is managed by after_turn only."
                    )
                    updates.pop("entity_description")

                identity_info = updates.pop("identity_info", {})
                raw_contact = updates.pop("contact_info", {})
                from narranexus.platform.channel.channel_contact_utils import normalize_contact_info
                contact_info = normalize_contact_info(raw_contact)
                # Accept both "tags" and "keywords" for backward compat
                keywords = updates.pop("keywords", None) or updates.pop("tags", [])

                await repo.add_entity(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    instance_id=instance_id,
                    entity_name=entity_name,
                    entity_description="",  # Empty description, awaiting hook population
                    identity_info=identity_info,
                    contact_info=contact_info,
                    keywords=keywords
                )

                return {
                    "success": True,
                    "message": "New entity created successfully",
                    "entity_id": entity_id
                }

        except Exception as e:
            logger.exception(f"Error in extract_and_update_entity_info: {e}")
            return {
                "success": False,
                "message": f"Error: {str(e)}",
                "entity_id": entity_id
            }

    async def merge_entities(
        self,
        source_entity_id: str,
        target_entity_id: str,
        instance_id: str,
        keep_target_name: bool = True,
    ) -> Dict[str, Any]:
        """
        Merge two entity records into one (called by MCP Server / HTTP route).

        The source entity's data is merged into the target entity, then the
        source is deleted. Tags, contact_info, identity_info, and
        related_job_ids are merged (union). entity_description is appended.
        Interaction counts are summed.

        Args:
            source_entity_id: Entity to merge FROM (deleted after merge)
            target_entity_id: Entity to merge INTO (survives after merge)
            instance_id: Instance ID (data isolation scope)
            keep_target_name: If True, keep target's entity_name; if False, use source's name

        Returns:
            Operation result
        """
        try:
            repo = self._get_repo()

            source = await repo.get_entity(source_entity_id, instance_id)
            target = await repo.get_entity(target_entity_id, instance_id)

            if not source:
                return {"success": False, "message": f"Source entity not found: {source_entity_id}"}
            if not target:
                return {"success": False, "message": f"Target entity not found: {target_entity_id}"}

            # Merge keywords (case-insensitive dedup, capped at 10)
            merged_tags = list(target.keywords or [])
            existing_lower = {t.lower() for t in merged_tags}
            for t in (source.keywords or []):
                if t.lower() not in existing_lower:
                    merged_tags.append(t)
                    existing_lower.add(t.lower())
            if len(merged_tags) > 10:
                merged_tags = merged_tags[:10]

            # Merge identity_info (target takes precedence; source fills in missing keys)
            merged_identity = {**(source.identity_info or {}), **(target.identity_info or {})}

            # Merge contact_info (deep merge + normalize)
            from narranexus.platform.channel.channel_contact_utils import merge_contact_info
            merged_contact = merge_contact_info(source.contact_info or {}, target.contact_info or {})

            # Merge related_job_ids (union)
            merged_jobs = list(set(target.related_job_ids or []) | set(source.related_job_ids or []))

            # Append descriptions
            merged_desc = target.entity_description or ""
            if source.entity_description:
                if merged_desc:
                    merged_desc += f"\n(Merged from {source_entity_id}): {source.entity_description}"
                else:
                    merged_desc = source.entity_description

            # Sum interaction counts
            merged_count = (target.interaction_count or 0) + (source.interaction_count or 0)

            # Determine name
            merged_name = target.entity_name if keep_target_name else (source.entity_name or target.entity_name)

            updates: Dict[str, Any] = {
                "entity_name": merged_name,
                "entity_description": merged_desc,
                "identity_info": merged_identity,
                "contact_info": merged_contact,
                "tags": merged_tags,
                "related_job_ids": merged_jobs,
                "interaction_count": merged_count,
            }
            # Keep the most recent interaction time
            if source.last_interaction_time and target.last_interaction_time:
                updates["last_interaction_time"] = max(
                    source.last_interaction_time, target.last_interaction_time
                )
            elif source.last_interaction_time:
                updates["last_interaction_time"] = source.last_interaction_time

            await repo.update_entity_info(target_entity_id, instance_id, updates)

            # Delete source
            await repo.delete_entity(source_entity_id, instance_id)

            logger.info(f"Merged entity {source_entity_id} into {target_entity_id}")
            return {
                "success": True,
                "message": f"Merged '{source_entity_id}' into '{target_entity_id}'",
                "target_entity_id": target_entity_id,
                "merged_tags": merged_tags,
            }

        except Exception as e:
            logger.exception(f"Error merging entities: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}

    async def delete_entity(self, entity_id: str, instance_id: str) -> Dict[str, Any]:
        """
        Permanently delete a social network entity (called by MCP Server / HTTP route).

        This action is irreversible from the caller's point of view (the
        repository tombstones the row rather than hard-deleting it, but reads
        and search stop seeing it).

        Args:
            entity_id: The entity ID to delete
            instance_id: Instance ID (data isolation scope)

        Returns:
            Operation result with success status.
        """
        try:
            repo = self._get_repo()

            entity = await repo.get_entity(entity_id, instance_id)
            if not entity:
                return {"success": False, "message": f"Entity not found: {entity_id}"}

            entity_name = entity.entity_name or entity_id
            await repo.delete_entity(entity_id, instance_id)

            logger.info(f"Deleted entity '{entity_name}' ({entity_id}) from instance {instance_id}")
            return {
                "success": True,
                "message": f"Entity '{entity_name}' ({entity_id}) has been permanently deleted.",
            }

        except Exception as e:
            logger.exception(f"Error deleting entity: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}

    async def recall_entity_info(self, entity_id: str, instance_id: str) -> Dict[str, Any]:
        """
        Recall entity information (called by MCP Server)

        Refactoring notes (2025-12-24):
        - Added instance_id parameter
        - Uses instance_id for data isolation

        Args:
            entity_id: Entity ID
            instance_id: Instance ID

        Returns:
            Entity info or error message
        """
        try:
            entity_info = await self._load_entity_info(entity_id, instance_id)

            if entity_info:
                return {
                    "success": True,
                    "entity": entity_info
                }
            else:
                return {
                    "success": False,
                    "message": f"No information found for entity: {entity_id}"
                }

        except Exception as e:
            logger.exception(f"Error in recall_entity_info: {e}")
            return {
                "success": False,
                "message": f"Error: {str(e)}"
            }

    async def search_network(
        self,
        search_keyword: str,
        instance_id: str,
        search_type: str = "auto",
        top_k: int = 5
    ) -> Dict[str, Any]:
        """
        Search social network (called by MCP Server)

        Refactoring notes (2025-12-24):
        - Added instance_id parameter
        - Uses instance_id for data isolation

        Args:
            search_keyword: Search keyword (entity_id, name, or tag)
            instance_id: Instance ID
            search_type: Search type (auto | exact_id | tags | semantic)
            top_k: Number of results to return (ignored for exact_id)

        Returns:
            Search results
        """
        try:
            entities = await self._search_entities(search_keyword, instance_id, search_type)

            # Limit return count (exact_id already returns only 1, no limit needed)
            if search_type != "exact_id":
                entities = entities[:top_k]

            return {
                "success": True,
                "search_keyword": search_keyword,
                "search_type": search_type,
                "results": entities,
                "count": len(entities)
            }

        except Exception as e:
            logger.exception(f"Error in search_network: {e}")
            return {
                "success": False,
                "message": f"Error: {str(e)}",
                "results": []
            }

