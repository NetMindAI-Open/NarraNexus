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

from mcp.server.fastmcp import FastMCP


def create_social_network_mcp_server() -> FastMCP:
    """
    Create a SocialNetworkModule MCP Server instance

    Args:

    Returns:
        FastMCP instance with all tools configured

    Note: every tool now routes its data access through the AgentDataStore seam
    (module/data_access) — none reaches the db directly — so the old
    ``get_db_client_fn`` and ``module_class`` parameters are both gone.
    """
    mcp = FastMCP("social_network_module")

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
                 (`entity_name_if_new` is an internal-caller key: it names an
                 entity only if it is new or still nameless, never overwriting an
                 existing name; not for normal use.)
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

        # The write routes through the AgentDataStore seam: DirectStore locally
        # (same instance-resolve + module call as before), HttpStore in cloud
        # (no db creds). The old setup_mcp_llm_context call is gone:
        # extract_and_update_entity_info is pure repository (no LLM), and that
        # setup read the `agents` table (an extra mcp db dependency the seam is
        # meant to shed) and could raise LLMConfigNotConfigured — both broke the
        # seam's "in-band dict, no db in cloud" promise for this path.
        from narranexus.platform.module_system.data_access import get_agent_data_store

        return await get_agent_data_store().extract_entity_info(
            agent_id, entity_id, updates, update_mode
        )

    @mcp.tool()
    async def search_social_network(
        agent_id: str,
        search_keyword: str,
        search_type: str = "auto",
        top_k: int = 5
    ) -> dict:
        """
        Search your social network for people. Supports exact ID lookup,
        keyword fuzzy match (over name / description / tags / aliases),
        and explicit tag-only search.

        Note (2026-05-27): semantic / embedding-based search was removed.
        Phrase your query as keywords found in the person's name,
        description, tags, or aliases — natural-language questions like
        "who showed purchase intent?" will not match anything that does
        not literally contain those words.

        Args:
            agent_id: The ID of the agent who owns this social network
            search_keyword: Can be:
                - Exact entity_id: "user_alice_123", "entity_bob_456"
                - Person's name (substring OK): "Alice", "Bob"
                - Tag: "expert:推荐系统", "architect", "familiar:机器学习"
                - Any keyword that should appear in name / description
                  / tags / aliases
            search_type: Type of search — 'auto' (recommended),
                'exact_id', 'tags', 'keyword'.
                - 'auto': Detects an entity_id (prefix `user_`,
                  `entity_`, or `agent_`) and routes to exact_id;
                  otherwise falls back to keyword.
                - 'exact_id': Force exact entity_id lookup.
                - 'tags': Match against tags only.
                - 'keyword': LIKE-substring match on
                  name / description / tags / aliases (same path
                  `auto` falls through to).
            top_k: Number of results to return (default: 5, ignored for exact_id)

        Returns:
            Search results with matching entities and their information
            (INCLUDING contact_info, so you usually don't need to call
            get_contact_info afterward).

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
        """
        # Routes through the AgentDataStore seam. setup_mcp_llm_context is gone:
        # search_network is pure repository (semantic search was removed
        # 2026-05-27), so the call only added a db read + a raise path.
        from narranexus.platform.module_system.data_access import get_agent_data_store

        return await get_agent_data_store().search_social_network(
            agent_id, search_keyword, search_type, top_k
        )

    @mcp.tool()
    async def get_contact_info(agent_id: str, entity_id: str) -> dict:
        """
        Get the stored contact details (channel, email, handle) for someone
        in your network — i.e. HOW to reach them.

        This returns contact details only; it does NOT contact anyone and
        cannot tell you what another agent is doing or whether they finished
        a task. To actually ask another agent something, message them with
        `message_agent` instead — they will be triggered and reply.

        Args:
            agent_id: Your own agent id (the owner of this social network)
            entity_id: The user_id or agent_id of the person

        Returns:
            Contact information including chat_channel, email, preferred_method, etc.
        """
        from narranexus.platform.module_system.data_access import get_agent_data_store

        return await get_agent_data_store().get_contact_info(agent_id, entity_id)

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
        # Parse filter_tags here (tool-layer input normalization), then route
        # the data read through the seam. The store takes the parsed list.
        filter_tags_list = None
        if filter_tags and filter_tags.strip():
            filter_tags_list = [tag.strip() for tag in filter_tags.split(",")]

        from narranexus.platform.module_system.data_access import get_agent_data_store

        return await get_agent_data_store().get_agent_social_stats(
            agent_id, sort_by, top_k, filter_tags_list
        )

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
        from narranexus.platform.module_system.data_access import get_agent_data_store

        return await get_agent_data_store().merge_entities(
            agent_id, source_entity_id, target_entity_id, keep_target_name
        )

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
        from narranexus.platform.module_system.data_access import get_agent_data_store

        return await get_agent_data_store().delete_entity(agent_id, entity_id)

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
        # Mint the new agent's id HERE (the single entry) and thread it through
        # the seam, so DirectStore (local) and HttpStore→route (cloud) provision
        # the SAME id and return byte-identical output. Owner resolution +
        # provisioning happen inside the store (DirectStore / the create-agent
        # route). See module/data_access.
        from uuid import uuid4

        from narranexus.platform.module_system.data_access import get_agent_data_store

        new_agent_id = f"agent_{uuid4().hex[:12]}"
        return await get_agent_data_store().create_agent(
            agent_id, new_agent_id, agent_name, awareness, agent_description
        )

    return mcp
