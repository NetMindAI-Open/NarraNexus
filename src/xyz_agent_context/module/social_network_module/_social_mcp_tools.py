"""
@file_name: _social_mcp_tools.py
@author: NetMind.AI
@date: 2025-11-21
@description: SocialNetworkModule MCP Server tool definitions

Separates MCP tool registration logic from the SocialNetworkModule main class.

Tools:
- extract_entity_info: Extract and update entity information
- search_social_network: Search social network
- get_contact_info: Get contact information
- get_agent_social_stats: Get Agent social statistics
"""

from typing import Optional, Any

from loguru import logger
from mcp.server.fastmcp import FastMCP

from xyz_agent_context.repository import InstanceRepository
from xyz_agent_context.agent_framework.api_config import setup_mcp_llm_context


def create_social_network_mcp_server(port: int, get_db_client_fn, module_class) -> FastMCP:
    """
    Create a SocialNetworkModule MCP Server instance

    Args:
        port: MCP Server port
        get_db_client_fn: Async function to get database connection
        module_class: SocialNetworkModule class reference (avoids circular imports)

    Returns:
        FastMCP instance with all tools configured
    """
    mcp = FastMCP("social_network_module")
    mcp.settings.port = port

    async def _get_instance_and_module(agent_id: str):
        """Common helper: get db, instance_id and create temp module"""
        db = await get_db_client_fn()
        instance_repo = InstanceRepository(db)
        instances = await instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="SocialNetworkModule"
        )
        if not instances:
            return None, None, f"Error: No SocialNetworkModule instance found for agent_id={agent_id}"
        instance_id = instances[0].instance_id
        temp_module = module_class(agent_id=agent_id, database_client=db, instance_id=instance_id)
        return temp_module, instance_id, None

    @mcp.tool()
    async def extract_entity_info(
        agent_id: str,
        entity_id: str,
        updates: dict | str,
        update_mode: str = "merge"
    ) -> dict:
        """
        IMMEDIATELY call this when someone introduces themselves or shares personal/professional information.

        Extract and persistently store information about users, agents, or organizations.
        This is how you build and maintain your social network memory with structured tags and identity data.

        **When to call (DO NOT WAIT)**:
        - User introduces themselves (name, role, company, expertise)
        - Someone mentions another person/agent/organization
        - Contact info is shared (email, phone, website)
        - Any biographical or professional detail appears

        **Tagging Discipline (IMPORTANT)**:
        - Tags are expensive — only add tags that carry clear, lasting signal
        - Aim for 2-3 tags per entity. Most updates need ZERO new tags
        - Before adding a tag, consider if the entity already has a similar one
        - Use canonical forms consistently (e.g. "expert:recommendation_system", not "expert:recommender_systems")
        - One expertise level per domain, one stage tag at a time

        Args:
            agent_id: The ID of the agent who owns this social network
            entity_id: The user_id or agent_id of the person
            updates: Information to update (entity_name, identity_info, contact_info, tags)
                 DO NOT include entity_description - it's auto-managed by conversation summaries
            update_mode: How to update: 'merge' combines with existing info, 'replace' overwrites (default: 'merge')

        Returns:
            Operation result with success status and message

        Example 1 - User introduces themselves with clear expertise:
            User: "你好，我是Alice，我是推荐系统专家"

            extract_entity_info(
                agent_id="your_agent_id",
                entity_id="user_alice_123",
                updates={
                    "entity_type": "user",
                    "entity_name": "Alice",
                    "tags": ["expert:recommendation_system"]
                }
            )

        Example 2 - User shares role and company (store in identity_info, minimal tags):
            User: "我叫Bob，在Acme Corp做前端开发"

            extract_entity_info(
                agent_id="your_agent_id",
                entity_id="user_bob_456",
                updates={
                    "entity_type": "user",
                    "entity_name": "Bob",
                    "identity_info": {
                        "organization": "Acme Corp",
                        "position": "前端工程师"
                    },
                    "tags": ["engineer"]
                }
            )

        Example 3 - Adding contact info (use channels structure for IM channels):
            User: "我的邮箱是 alice@example.com, 飞书 open_id 是 ou_alice_open_id"

            extract_entity_info(
                agent_id="your_agent_id",
                entity_id="user_alice_123",
                updates={
                    "contact_info": {
                        "email": "alice@example.com",
                        "channels": {
                            "lark": {"id": "ou_alice_open_id"}
                        }
                    }
                }
            )
        """
        import json as _json

        # Process updates parameter
        if isinstance(updates, str):
            try:
                updates = _json.loads(updates)
            except _json.JSONDecodeError as e:
                return {
                    "success": False,
                    "message": f"Error: updates must be a valid JSON object, got string that failed to parse: {e}",
                    "entity_id": entity_id
                }

        if not isinstance(updates, dict):
            return {
                "success": False,
                "message": f"Error: updates must be a dictionary, got {type(updates).__name__}",
                "entity_id": entity_id
            }

        await setup_mcp_llm_context(agent_id)
        temp_module, instance_id, error = await _get_instance_and_module(agent_id)
        if error:
            return {"success": False, "message": error}

        return await temp_module.extract_and_update_entity_info(
            entity_id=entity_id,
            instance_id=instance_id,
            updates=updates,
            update_mode=update_mode
        )

    @mcp.tool()
    async def search_social_network(
        agent_id: str,
        search_keyword: str,
        search_type: str = "auto",
        top_k: int = 5
    ) -> dict:
        """
        Search your social network for people. Supports exact lookup, tag search, and semantic search.

        Args:
            agent_id: The ID of the agent who owns this social network
            search_keyword: Can be:
                - Exact entity_id: "user_alice_123", "entity_bob_456"
                - Person's name: "Alice", "Bob"
                - Tag: "expert:推荐系统", "architect", "familiar:机器学习"
                - Natural language query (for semantic search): "谁最近表现出购买意向？"
            search_type: Type of search - 'auto' (recommended), 'exact_id', 'tags', 'semantic'
                - 'auto': Automatically detects if it's an entity_id or tag/name
                - 'exact_id': Force exact entity_id lookup
                - 'tags': Search by tags only
                - 'semantic': Natural language semantic search using embeddings
            top_k: Number of results to return (default: 5, ignored for exact_id)

        Returns:
            Search results with matching entities and their information (INCLUDING contact_info)
            For semantic search, results also include 'similarity_score' (0-1)

        Example 1 - Find specific person by entity_id:
            search_social_network(
                agent_id="your_agent_id",
                search_keyword="user_alice_123",
                search_type="auto"
            )

        Example 2 - Find person by name:
            search_social_network(
                agent_id="your_agent_id",
                search_keyword="Bob",
                search_type="auto"
            )

        Example 3 - Find experts by tag:
            search_social_network(
                agent_id="your_agent_id",
                search_keyword="expert:推荐系统",
                search_type="tags",
                top_k=5
            )

        Example 4 - Semantic search (natural language):
            search_social_network(
                agent_id="your_agent_id",
                search_keyword="谁最近表现出购买意向？",
                search_type="semantic",
                top_k=5
            )

        Note: Results include contact_info, so you usually don't need to call get_contact_info afterward.
        """
        await setup_mcp_llm_context(agent_id)
        temp_module, instance_id, error = await _get_instance_and_module(agent_id)
        if error:
            return {"success": False, "message": error, "results": []}

        return await temp_module.search_network(
            search_keyword=search_keyword,
            instance_id=instance_id,
            search_type=search_type,
            top_k=top_k
        )

    @mcp.tool()
    async def get_contact_info(agent_id: str, entity_id: str) -> dict:
        """
        Get contact information for reaching out to someone in your network.
        Use this when you need to know how to contact a specific person.

        Args:
            agent_id: The ID of the agent who owns this social network
            entity_id: The user_id or agent_id of the person

        Returns:
            Contact information including chat_channel, email, preferred_method, etc.
        """
        temp_module, instance_id, error = await _get_instance_and_module(agent_id)
        if error:
            return {"success": False, "message": error}

        result = await temp_module.recall_entity_info(entity_id, instance_id)

        if result["success"]:
            entity = result["entity"]
            return {
                "success": True,
                "entity_id": entity_id,
                "entity_name": entity.get("entity_name"),
                "contact_info": entity.get("contact_info", {})
            }
        else:
            return {"success": False, "message": result["message"]}

    @mcp.tool()
    async def get_agent_social_stats(
        agent_id: str,
        sort_by: str = "recent",
        top_k: int = 5,
        filter_tags: str = ""
    ) -> dict:
        """
        View your social network from Agent's perspective - perfect for sales/outreach tracking!

        This tool lets you (the Agent's owner) ask questions like:
        - "Who did you contact recently?"
        - "Which customers engage with you most?"
        - "Show me your best relationships"

        Args:
            agent_id: The ID of the agent
            sort_by: How to sort results:
                - "recent": Most recently contacted (best for "who did you talk to lately?")
                - "frequent": Most interactions (best for "who engages most?")
                - "strong": Strongest relationships (best for "your best contacts?")
            top_k: Number of results to return (default: 5)
            filter_tags: Optional comma-separated tags to filter (e.g., "expert:前端,architect")

        Returns:
            Sorted list with FULL entity info including:
            - entity_name, entity_description ← Key! Shows conversation summary
            - interaction_count, last_interaction_time
            - tags, contact_info, relationship_strength

        Example 1 - Sales Agent reporting recent contacts:
            get_agent_social_stats(
                agent_id="sales_agent_001",
                sort_by="recent",
                top_k=5
            )

        Example 2 - Find most active customers:
            get_agent_social_stats(
                agent_id="sales_agent_001",
                sort_by="frequent",
                top_k=10
            )

        Example 3 - Check progress with frontend experts:
            get_agent_social_stats(
                agent_id="sales_agent_001",
                sort_by="recent",
                filter_tags="expert:前端"
            )
        """
        temp_module, instance_id, error = await _get_instance_and_module(agent_id)
        if error:
            return {"success": False, "message": error, "results": []}

        # Parse filter_tags
        filter_tags_list = None
        if filter_tags and filter_tags.strip():
            filter_tags_list = [tag.strip() for tag in filter_tags.split(",")]

        results = await temp_module._get_agent_stats(
            instance_id=instance_id,
            sort_by=sort_by,
            top_k=top_k,
            filter_tags=filter_tags_list
        )

        return {
            "success": True,
            "sort_by": sort_by,
            "count": len(results),
            "results": results
        }

    @mcp.tool()
    async def merge_entities(
        agent_id: str,
        source_entity_id: str,
        target_entity_id: str,
        keep_target_name: bool = True,
    ) -> dict:
        """
        Merge two entity records into one (e.g., duplicates from different channels).

        The source entity's data is merged into the target entity, then the source is deleted.
        Tags, contact_info, identity_info, and related_job_ids are merged (union).
        entity_description is appended. Interaction counts are summed.

        Args:
            agent_id: The ID of the agent who owns this social network
            source_entity_id: Entity to merge FROM (will be deleted after merge)
            target_entity_id: Entity to merge INTO (survives after merge)
            keep_target_name: If True, keep target's entity_name; if False, use source's name

        Returns:
            Operation result

        Example:
            merge_entities(
                agent_id="your_agent_id",
                source_entity_id="entity_alice_lark",
                target_entity_id="user_alice_123"
            )
        """
        temp_module, instance_id, error = await _get_instance_and_module(agent_id)
        if error:
            return {"success": False, "message": error}

        try:
            from xyz_agent_context.repository import SocialNetworkRepository
            db = await get_db_client_fn()
            repo = SocialNetworkRepository(db)

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
            from xyz_agent_context.channel.channel_contact_utils import merge_contact_info
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

            # Update target
            updates = {
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

    @mcp.tool()
    async def delete_entity(
        agent_id: str,
        entity_id: str,
    ) -> dict:
        """
        Delete a social network entity permanently.

        **WHEN TO CALL**: When the user explicitly asks you to remove a contact/entity
        from your social network — e.g., "delete Alice", "remove that entity",
        "clean up duplicate entries". Also useful for removing test or junk entries.

        This action is irreversible. The entity and all its associated data
        (tags, contact info, interaction history) will be permanently deleted.

        **NOTE**: If the user refers to an entity by name (not ID), use
        `search_social_network` first to find the matching entity_id.
        Multiple entities may share the same name — confirm with the user
        if there are ambiguous matches before deleting.

        Args:
            agent_id: The ID of the agent who owns this social network.
            entity_id: The unique entity ID to delete (e.g., "user_alice_123").
                       Use search_social_network to find the ID if you only have a name.

        Returns:
            Operation result with success status.
        """
        temp_module, instance_id, error = await _get_instance_and_module(agent_id)
        if error:
            return {"success": False, "message": error}

        try:
            from xyz_agent_context.repository import SocialNetworkRepository
            db = await get_db_client_fn()
            repo = SocialNetworkRepository(db)

            # Verify entity exists
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

    @mcp.tool()
    async def create_agent(
        agent_id: str,
        agent_name: str,
        awareness: str,
        agent_description: str = "",
    ) -> dict:
        """
        Create a new agent with a name and awareness (self-identity).

        **WHEN TO CALL**: When the user asks you to create a new agent — e.g.,
        "create an agent called Scout", "set up a new agent for research".

        This tool creates the agent, its workspace, and sets its initial awareness.
        The new agent will appear in the user's agent list in the frontend.

        **IMPORTANT**: This only creates the agent with a name and awareness.
        If the user needs further configuration (skills, jobs, MCP tools, etc.),
        tell them to switch to the new agent and interact with it directly.

        Args:
            agent_id: YOUR agent ID (the creator). The new agent's owner will be
                      the same user who owns you.
            agent_name: Display name for the new agent (e.g., "Scout").
            awareness: The new agent's self-awareness / identity description.
                       This defines who the agent is, what it does, and how it behaves.
            agent_description: Optional short description of the agent's purpose.

        Returns:
            Operation result with the new agent's ID.
        """
        try:
            from uuid import uuid4
            import os

            db = await get_db_client_fn()

            # Resolve the creator's user_id (the owner of the calling agent)
            from xyz_agent_context.repository import AgentRepository
            agent_repo = AgentRepository(db)
            caller = await agent_repo.get_agent(agent_id)
            if not caller or not caller.created_by:
                return {"success": False, "message": "Cannot determine your owner (created_by). Aborting."}

            owner_user_id = caller.created_by
            new_agent_id = f"agent_{uuid4().hex[:12]}"

            # 1. Create agent record in DB
            await agent_repo.add_agent(
                agent_id=new_agent_id,
                agent_name=agent_name,
                created_by=owner_user_id,
                agent_description=agent_description or f"Agent created by {caller.agent_name or agent_id}",
                agent_type="chat",
            )
            logger.info(f"Created agent {new_agent_id} ('{agent_name}') for owner {owner_user_id}")

            # 2. Create workspace directory + Bootstrap.md
            from xyz_agent_context.settings import settings
            workspace_path = os.path.join(
                settings.base_working_path,
                f"{new_agent_id}_{owner_user_id}"
            )
            os.makedirs(workspace_path, exist_ok=True)

            try:
                from xyz_agent_context.bootstrap.template import BOOTSTRAP_MD_TEMPLATE
                bootstrap_file = os.path.join(workspace_path, "Bootstrap.md")
                with open(bootstrap_file, "w", encoding="utf-8") as f:
                    f.write(BOOTSTRAP_MD_TEMPLATE)
            except Exception as e:
                logger.warning(f"Failed to write Bootstrap.md for {new_agent_id}: {e}")

            # 3. Create awareness instance and set awareness text
            instance_repo = InstanceRepository(db)
            from xyz_agent_context.schema.instance_schema import ModuleInstanceRecord, InstanceStatus
            awareness_instance_id = f"aware_{uuid4().hex[:8]}"
            new_instance = ModuleInstanceRecord(
                instance_id=awareness_instance_id,
                module_class="AwarenessModule",
                agent_id=new_agent_id,
                is_public=True,
                status=InstanceStatus.ACTIVE,
                description="Agent self-awareness module instance",
            )
            await instance_repo.create_instance(new_instance)

            from xyz_agent_context.repository import InstanceAwarenessRepository
            awareness_repo = InstanceAwarenessRepository(db)
            await awareness_repo.upsert(awareness_instance_id, awareness)
            logger.info(f"Set awareness for {new_agent_id}: {len(awareness)} chars")

            return {
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

        except Exception as e:
            logger.exception(f"Error creating agent: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}

    return mcp
