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
from xyz_agent_context.module import XYZBaseModule, mcp_host

# Schema
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    ModuleInstructions,
    HookAfterExecutionParams,
)

# Utils
from xyz_agent_context.utils import DatabaseClient
from xyz_agent_context.agent_framework.llm_api.embedding import get_embedding

# Repository
from xyz_agent_context.repository import SocialNetworkRepository, SocialNetworkEntity, InstanceRepository

# Prompts
from xyz_agent_context.module.social_network_module.prompts import SOCIAL_NETWORK_MODULE_INSTRUCTIONS
from xyz_agent_context.module.social_network_module._social_mcp_tools import create_social_network_mcp_server

# Entity update pipeline (LLM-powered)
from xyz_agent_context.module.social_network_module._entity_updater import (
    summarize_new_entity_info,
    append_to_entity_description,
    update_entity_embedding,
    update_interaction_stats,
    should_update_persona,
    infer_persona,
    update_entity_persona,
    extract_mentioned_entities,
)


class SocialNetworkModule(XYZBaseModule):
    """
    Social Network Module

    Provides social network capabilities:
    1. **Instructions** - Tells the Agent how to use social network features
    2. **Tools (MCP)** - Provides extract_entity_info, recall_entity, search_social_network tools
    3. **Data** - Stored in social_network_entities table
    4. **Hooks**:
       - hook_data_gathering: Load known information about the current interaction entity
       - hook_after_event_execution: Summarize and update entity information (Phase 2 implementation)
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
        self.port = 7802  # Use a different port to avoid conflict with awareness_module (7801)

        # Initialize repository (lazy initialization, since db may only be available at call time)
        self._social_repo: Optional[SocialNetworkRepository] = None

        # Build instructions, dynamically insert agent_id
        self.instructions = SOCIAL_NETWORK_MODULE_INSTRUCTIONS.replace("{agent_id}", agent_id)

    def _get_repo(self) -> SocialNetworkRepository:
        """Get or create SocialNetworkRepository instance"""
        if self._social_repo is None:
            self._social_repo = SocialNetworkRepository(self.db)
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

    def get_config(self) -> ModuleConfig:
        """
        Return SocialNetworkModule configuration
        """
        return ModuleConfig(
            name="SocialNetworkModule",
            priority=3,  # Medium priority
            enabled=True,
            description="Provides social network recording and search capabilities"
        )

    # ============================================================================= Hooks

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
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
        logger.debug("          → SocialNetworkModule.hook_data_gathering() started")

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
                    # Allow JobModule to read and load Job context in subsequent hook_data_gathering
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
            try:
                agent_entities = await self._get_repo().get_all_entities(
                    instance_id=instance_id,
                    entity_type="agent",
                    limit=50,
                )
                if agent_entities:
                    ctx_data.extra_data["known_agent_entities"] = [
                        {
                            "entity_id": e.entity_id,
                            "entity_name": e.entity_name,
                            "entity_description": e.entity_description or "",
                            "keywords": e.keywords or [],
                            "contact_info": e.contact_info or {},
                        }
                        for e in agent_entities
                    ]
                    logger.info(f"            ✓ Loaded {len(agent_entities)} known agent entities to extra_data")
            except Exception as exc:
                logger.warning(f"            Failed to load known agent entities: {exc}")

            logger.debug("          ← SocialNetworkModule.hook_data_gathering() completed")

        except Exception as e:
            logger.error(f"            ❌ Error in hook_data_gathering: {e}")
            logger.exception(e)
            # Set error state value to ensure get_instructions doesn't fail due to missing fields
            # Also clearly indicate an error occurred for debugging
            ctx_data.social_network_current_entity = f"""**⚠️ Social network data loading failed.**

Error: {type(e).__name__}: {str(e)[:100]}

Please check the database connection and ensure all required tables exist.
Tables are auto-created on startup via schema_registry.auto_migrate()."""

        return ctx_data

    async def hook_after_event_execution(self, params: HookAfterExecutionParams) -> None:
        """
        Automatically update entity_description after Event execution

        Functionality:
        1. Detect if the conversation contains identity information keywords
        2. If so, call LLM to summarize new information
        3. Append to entity_description (cumulative, not overwriting)
        4. Update interaction_count and last_interaction_time

        Division of responsibilities with extract_entity_info:
        - extract_entity_info: Actively called by Agent, updates structured info (tags, contact_info, identity_info)
        - hook_after_event_execution: Automatically executed, cumulatively updates natural language description (entity_description)

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
        logger.debug("          → SocialNetworkModule.hook_after_event_execution() started")

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
                logger.warning("            ⚠ No user_id found, skipping hook")
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
                    logger.error(f"            ❌ Failed to create entity: {e}")
                    return

            primary_name = entity.entity_name or user_id if entity else user_id

            # Get agent's own name for self-exclusion in extraction
            agent_name = ""
            if params.ctx_data:
                agent_name = getattr(params.ctx_data, 'agent_name', '') or ''

            # 1. Run independent LLM calls in parallel: summary + batch extraction
            new_summary, mentioned = await asyncio.gather(
                summarize_new_entity_info(input_content, final_output),
                extract_mentioned_entities(
                    input_content, final_output, primary_name,
                    agent_name=agent_name, agent_id=self.agent_id,
                ),
            )

            # 2. Process summary results
            if new_summary and new_summary.strip():
                logger.info(f"            New summary generated: {new_summary[:100]}...")
                await append_to_entity_description(repo, user_id, instance_id, new_summary)
                await update_entity_embedding(repo, user_id, instance_id)

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
                    job_info=job_info_str, recent_conversation=recent_conversation
                )
                if new_persona:
                    await update_entity_persona(repo, user_id, instance_id, new_persona)

            logger.success(f"            ✅ Entity updated for {user_id}")

            # 5. Process mentioned entities (dedup pipeline)
            try:
                await self._process_mentioned_entities(repo, instance_id, mentioned)
            except Exception as e:
                logger.warning(f"            Batch entity extraction failed (non-critical): {e}")

        except Exception as e:
            logger.error(f"            ❌ Error in hook_after_event_execution: {e}")
            logger.exception(e)

        logger.debug("          ← SocialNetworkModule.hook_after_event_execution() completed")

    async def _process_mentioned_entities(self, repo, instance_id: str, mentioned: list) -> None:
        """
        3-stage dedup pipeline for entities extracted from conversation.

        For each mentioned entity:
          Stage 1: Exact name+alias match → if 1 match, UPDATE
          Stage 2: Vector similarity search (threshold + topK) → if candidates, go to Stage 3
          Stage 3: LLM merge decision → MERGE or CREATE_NEW
        """
        from xyz_agent_context.module.social_network_module._entity_updater import (
            append_to_entity_description,
            decide_merge_or_create,
            DEDUP_SIMILARITY_THRESHOLD,
            DEDUP_TOP_K,
        )

        for mentioned_entity in mentioned:
            try:
                entity_id_candidate = f"entity_{mentioned_entity.name.lower().replace(' ', '_')}"
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
                    )
                    if decision == "MERGE" and matched:
                        existing = matched

                # ── STAGE 2: Vector similarity search ──
                if not existing:
                    try:
                        embed_text = f"{mentioned_entity.name} {mentioned_entity.summary}".strip()
                        if embed_text:
                            query_embedding = await get_embedding(embed_text)
                            sim_results = await repo.semantic_search(
                                instance_id=instance_id,
                                query_embedding=query_embedding,
                                limit=DEDUP_TOP_K,
                                min_similarity=DEDUP_SIMILARITY_THRESHOLD,
                            )
                            if sim_results:
                                # ── STAGE 3: Single LLM call with all candidates ──
                                sim_entities = [e for e, _ in sim_results]
                                for e, score in sim_results:
                                    logger.info(
                                        f"            Stage 2 candidate: '{e.entity_name}' "
                                        f"(similarity={score:.3f})"
                                    )
                                decision, matched = await decide_merge_or_create(
                                    mentioned_entity.name, mentioned_entity.summary,
                                    candidate_aliases, sim_entities,
                                )
                                if decision == "MERGE" and matched:
                                    existing = matched
                    except Exception as e:
                        logger.warning(f"            Stage 2 vector search failed: {e}")

                # ── UPDATE existing or CREATE NEW ──
                if existing:
                    matched_id = existing.entity_id
                    if mentioned_entity.summary:
                        await append_to_entity_description(
                            repo, matched_id, instance_id, mentioned_entity.summary
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
                logger.warning(f"            Failed to process entity '{mentioned_entity.name}': {e}")

    # ============================================================================= MCP Server

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
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
            server_url=f"http://{mcp_host()}:{self.port}/sse",
            type="sse"
        )

    def create_mcp_server(self) -> Optional[Any]:
        """
        Create MCP Server instance

        Tool definitions have been extracted to _social_mcp_tools.py.
        """
        return create_social_network_mcp_server(
            self.port, SocialNetworkModule.get_mcp_db_client, SocialNetworkModule
        )

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

        # Semantic: semantic search (Feature 2.3)
        if search_type == "semantic":
            # Generate embedding for query text
            query_embedding = await get_embedding(search_keyword)

            # Execute semantic search
            results_with_scores = await repo.semantic_search(
                instance_id=instance_id,
                query_embedding=query_embedding,
                limit=10,  # Get more results, later limited by top_k
                min_similarity=0.3
            )

            return [
                {
                    "entity_id": e.entity_id,
                    "entity_name": e.entity_name,
                    "entity_description": e.entity_description,
                    "keywords": e.keywords or [],
                    "contact_info": e.contact_info or {},
                    "relationship_strength": e.relationship_strength,
                    "similarity_score": round(score, 3),  # Semantic similarity score
                }
                for e, score in results_with_scores
            ]

        return []

    async def _get_agent_stats(
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
            logger.error(f"Error getting agent stats: {e}")
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
                        from xyz_agent_context.channel.channel_contact_utils import merge_contact_info
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
                    # entity_description should only be cumulatively updated by hook_after_event_execution
                    if "entity_description" in updates:
                        logger.warning(
                            "Attempted to update entity_description via extract_entity_info. "
                            "This field is managed by hook_after_event_execution only. Ignoring."
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
                entity_name = updates.pop("entity_name", None)
                # Ignore entity_description, managed only by hook
                if "entity_description" in updates:
                    logger.warning(
                        "Ignoring entity_description in updates during entity creation. "
                        "This field is managed by hook_after_event_execution only."
                    )
                    updates.pop("entity_description")

                identity_info = updates.pop("identity_info", {})
                raw_contact = updates.pop("contact_info", {})
                from xyz_agent_context.channel.channel_contact_utils import normalize_contact_info
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
            logger.error(f"Error in extract_and_update_entity_info: {e}")
            return {
                "success": False,
                "message": f"Error: {str(e)}",
                "entity_id": entity_id
            }

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
            logger.error(f"Error in recall_entity_info: {e}")
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
            logger.error(f"Error in search_network: {e}")
            return {
                "success": False,
                "message": f"Error: {str(e)}",
                "results": []
            }

