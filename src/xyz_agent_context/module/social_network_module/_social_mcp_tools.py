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
            User: "我的邮箱是 alice@example.com, Matrix 上是 @alice:localhost"

            extract_entity_info(
                agent_id="your_agent_id",
                entity_id="user_alice_123",
                updates={
                    "contact_info": {
                        "email": "alice@example.com",
                        "channels": {
                            "matrix": {"id": "@alice:localhost"}
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
    async def check_channel_updates(
        agent_id: str,
        channels: str = "",
    ) -> dict:
        """
        Check for recent updates across all communication channels (Matrix, Slack, etc.).

        Returns a summary of unread messages, pending invitations, and recent activity
        from all registered channels.

        Args:
            agent_id: Your agent ID
            channels: Comma-separated channel names to check (empty = all channels)

        Returns:
            Cross-channel summary with updates per channel

        Example:
            check_channel_updates(agent_id="your_agent_id")
            check_channel_updates(agent_id="your_agent_id", channels="matrix,slack")
        """
        from xyz_agent_context.channel.channel_sender_registry import ChannelSenderRegistry

        available = ChannelSenderRegistry.available_channels()

        # Filter channels if specified
        if channels and channels.strip():
            check_list = [ch.strip() for ch in channels.split(",") if ch.strip() in available]
        else:
            check_list = available

        if not check_list:
            return {
                "success": True,
                "message": "No channels to check",
                "channels": [],
                "available_channels": available,
            }

        updates = []
        for channel_name in check_list:
            channel_update = {"channel": channel_name, "status": "connected"}

            # Channel-specific status checks
            if channel_name == "matrix":
                try:
                    from xyz_agent_context.module.matrix_module._matrix_credential_manager import (
                        MatrixCredentialManager,
                    )
                    from xyz_agent_context.module.matrix_module.matrix_client import NexusMatrixClient

                    db = await get_db_client_fn()
                    cred_mgr = MatrixCredentialManager(db)
                    cred = await cred_mgr.get_credential(agent_id)
                    if cred and cred.is_active:
                        client = NexusMatrixClient(server_url=cred.server_url)
                        try:
                            rooms = await client.list_rooms(api_key=cred.api_key)
                            channel_update["rooms_count"] = len(rooms) if rooms else 0
                            channel_update["matrix_user_id"] = cred.matrix_user_id
                        finally:
                            await client.close()
                    else:
                        channel_update["status"] = "no credentials"
                except Exception as e:
                    channel_update["status"] = f"error: {str(e)[:50]}"

            updates.append(channel_update)

        return {
            "success": True,
            "channels_checked": len(updates),
            "updates": updates,
        }

    @mcp.tool()
    async def contact_agent(
        agent_id: str,
        target_entity_id: str,
        message: str,
        channel: str = "",
        room_id: str = "",
    ) -> dict:
        """
        Send a message to another agent or user via the best available channel.

        Automatically selects the communication channel:
        1. If `channel` is specified, uses that channel
        2. Otherwise checks the entity's preferred_channel in contact_info
        3. Falls back to any available registered channel

        Args:
            agent_id: Your agent ID (the sender)
            target_entity_id: Entity ID of the recipient
            message: Message content to send
            channel: Force a specific channel (e.g., "matrix", "slack"). Leave empty for auto-selection.
            room_id: Specific room/conversation ID (optional, creates new if empty)

        Returns:
            Result with success status, channel used, and room_id

        Example 1 - Auto-select channel:
            contact_agent(
                agent_id="your_agent_id",
                target_entity_id="user_alice_123",
                message="Hi Alice, following up on our discussion"
            )

        Example 2 - Force Matrix:
            contact_agent(
                agent_id="your_agent_id",
                target_entity_id="@bob:matrix.example.com",
                message="Hello Bob!",
                channel="matrix"
            )
        """
        from xyz_agent_context.channel.channel_sender_registry import ChannelSenderRegistry
        from xyz_agent_context.channel.channel_contact_utils import (
            get_preferred_channel,
            get_channel_info,
            get_room_id as get_entity_room_id,
        )

        # 1. Look up entity contact_info for channel selection
        temp_module, instance_id, error = await _get_instance_and_module(agent_id)
        if error:
            return {"success": False, "message": error}

        entity_contact_info = {}
        try:
            entity_info = await temp_module._load_entity_info(target_entity_id, instance_id)
            if entity_info:
                entity_contact_info = entity_info.get("contact_info") or {}
        except Exception:
            pass  # Non-critical: proceed with manual channel selection

        # 2. Determine channel
        selected_channel = channel
        if not selected_channel:
            selected_channel = get_preferred_channel(entity_contact_info) or ""

        if not selected_channel:
            # Auto-detect: use the first available channel that has info for this entity
            available = ChannelSenderRegistry.available_channels()
            for ch in available:
                ch_info = get_channel_info(entity_contact_info, ch)
                if ch_info:
                    selected_channel = ch
                    break
            if not selected_channel and available:
                selected_channel = available[0]  # Last resort: first registered channel

        if not selected_channel:
            return {
                "success": False,
                "message": "No communication channel available. Register a channel module first.",
            }

        # 3. Get sender
        sender = ChannelSenderRegistry.get_sender(selected_channel)
        if not sender:
            return {
                "success": False,
                "message": f"Channel '{selected_channel}' is not registered. Available: {ChannelSenderRegistry.available_channels()}",
            }

        # 4. Resolve target_id for the channel
        target_channel_id = target_entity_id
        ch_info = get_channel_info(entity_contact_info, selected_channel)
        if ch_info:
            target_channel_id = ch_info.get("id", target_entity_id)

        # Resolve room_id from entity contact_info if not provided
        if not room_id:
            room_id = get_entity_room_id(entity_contact_info, selected_channel, target_entity_id) or ""

        # 5. Send
        try:
            result = await sender(
                agent_id=agent_id,
                target_id=target_channel_id,
                message=message,
                room_id=room_id,
            )
            result["channel"] = selected_channel
            return result
        except Exception as e:
            logger.error(f"contact_agent send failed: {e}")
            return {"success": False, "message": f"Send failed: {str(e)}", "channel": selected_channel}

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
                source_entity_id="entity_alice_matrix",
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

            # Merge tags (case-insensitive dedup, capped at 10)
            merged_tags = list(target.tags or [])
            existing_lower = {t.lower() for t in merged_tags}
            for t in (source.tags or []):
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
            logger.error(f"Error merging entities: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}

    return mcp
