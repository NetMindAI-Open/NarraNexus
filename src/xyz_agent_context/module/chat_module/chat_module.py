"""
@file_name: chat_module.py
@author: NetMind.AI
@date: 2025-11-15
@description: Chat Module - Provides chat-related functionality

ChatModule provides Agent messaging capabilities on the XYZ-Platform.

Core concept - Thinking vs Speaking:
- All output from Agent's LLM calls, Agent Loop, and tool calls are the Agent's internal thinking, invisible to users
- Only by calling the send_message_to_user_directly tool does the Agent actually "speak", and only then can users receive a response
- Like two people talking face-to-face: thinking in your head (invisible) vs speaking out loud (visible)

Included MCP Tools:
- send_message_to_user_directly: Agent speaks to user (real-time conversation, must be called for user to see response)
- agent_send_content_to_user_inbox: Agent proactively sends message to user's Inbox (async notification)
- get_inbox_status: Get user Inbox status

Note: ChatModule itself does not include "multi-turn conversation" capability; multi-turn conversation requires Social-Network/Memory modules
"""


from typing import Optional, Any, List, Dict
from loguru import logger


# Module (same package)
from xyz_agent_context.module import XYZBaseModule
from xyz_agent_context.module.event_memory_module import EventMemoryModule

# Schema
from xyz_agent_context.schema import (
    ContextData,
    HookAfterExecutionParams,
    ModuleConfig,
    MCPServerConfig,
    InboxMessageType,
)

# Utils
from xyz_agent_context.utils import DatabaseClient, utc_now

# Repository
from xyz_agent_context.repository import InboxRepository, AgentMessageRepository

# Schema
from xyz_agent_context.schema.agent_message_schema import MessageSourceType

# Prompts
from xyz_agent_context.module.chat_module.prompts import CHAT_MODULE_INSTRUCTIONS
from xyz_agent_context.bootstrap.template import BOOTSTRAP_GREETING


class ChatModule(XYZBaseModule):
    """
    Chat Module - Core module for Agent-user communication

    Core concept - Thinking vs Speaking:
    Agent's internal processing (LLM calls, Agent Loop, tool calls) is like thinking in your head, completely invisible to users.
    Only through the send_message_to_user_directly tool can the Agent "speak", and only then can users receive the Agent's response.

    Provided capabilities:
    1. **Instructions** - Guide Agent to understand the "thinking vs speaking" distinction
    2. **Tools** (via MCP):
       - send_message_to_user_directly: Real-time response to user (must be called for user to see response)
       - agent_send_content_to_user_inbox: Async notification to Inbox
       - get_inbox_status: Query Inbox status
    3. **Data** - User's Inbox unread message count

    Dual-track memory loading (2026-01-21 P1-2):
    - Long-term memory: Current Narrative's EverMemOS semantically relevant history (2026-02-09 optimization)
    - Short-term memory: User's recent cross-Narrative conversations (most recent K messages, no time limit)
    """

    # Short-term memory configuration parameters (2026-02-09 optimization: removed time limit)
    SHORT_TERM_MAX_MESSAGES = 15    # Max short-term memory messages (most recent K across Narratives)
    # Note: Long-term memory count is controlled by EverMemOS retrieval top_k (see narrative/config.py)

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        if_use_event_memory: bool = True,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None
    ):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)

        if if_use_event_memory:
            self.event_memory_module = EventMemoryModule(agent_id, user_id, database_client)
        else:
            self.event_memory_module = None

        self.port = 7804  # MCP Server port (avoid conflict with SocialNetworkModule 7802)

        self.instructions = CHAT_MODULE_INSTRUCTIONS
        self.instance_ids = instance_ids    # TODO: Improve this capability in the future


    def get_config(self) -> ModuleConfig:
        """
        Return ChatModule configuration
        """
        return ModuleConfig(
            name="ChatModule",
            priority=1,  # High priority (base module)
            enabled=True,
            description="Provides messaging capabilities (instant chat + Inbox notifications)"
        )

    # ============================================================================= MCP Server

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """
        Return MCP Server configuration

        ChatModule provides MCP Server for:
        - agent_send_content_to_user_inbox: Agent proactively sends messages to users

        Returns:
            MCPServerConfig
        """
        return MCPServerConfig(
            server_name="chat_module",
            server_url=f"http://127.0.0.1:{self.port}/sse",
            type="sse"
        )

    def create_mcp_server(self) -> Optional[Any]:
        """
        Create MCP Server

        Delegates tool registration to _chat_mcp_tools module.
        """
        from xyz_agent_context.module.chat_module._chat_mcp_tools import create_chat_mcp_server
        return create_chat_mcp_server(self.port, ChatModule.get_mcp_db_client)


    # ============================================================================= Private Helper Methods

    async def _get_or_create_mcp_url(self) -> str:
        """
        Get or create MCP Server URL

        Returns:
            MCP Server URL
        """
        return f"http://127.0.0.1:{self.port}/sse"
    
    
    # ============================================================================= Hooks

    def _extract_user_visible_response(self, agent_loop_response: list) -> str:
        """
        Extract user-visible response content from agent_loop_response

        Iterates through agent_loop_response, looking for send_message_to_user_directly tool calls,
        and extracts the content parameter as the actual content displayed to the user.

        Logic is consistent with getUserVisibleResponse in the frontend chatStore.ts:
        - Tool name ends with 'send_message_to_user_directly' (Claude SDK format: mcp__chat_module__send_message_to_user_directly)
        - Extracts content field from tool_input/arguments

        Args:
            agent_loop_response: Raw response list from Agent Loop, containing ProgressMessage etc.

        Returns:
            str: User-visible response content; returns default message if send_message_to_user_directly was not called
        """
        from xyz_agent_context.schema import ProgressMessage

        for response in agent_loop_response:
            # Check if it's a ProgressMessage (tool calls are wrapped as ProgressMessage)
            if isinstance(response, ProgressMessage) and response.details:
                tool_name = response.details.get("tool_name", "")
                # Match send_message_to_user_directly (frontend uses endsWith matching)
                if tool_name.endswith("send_message_to_user_directly"):
                    arguments = response.details.get("arguments", {})
                    content = arguments.get("content", "")
                    if content:
                        logger.debug(
                            f"ChatModule._extract_user_visible_response: "
                            f"Extracted reply content from {tool_name}, length {len(content)}"
                        )
                        return content

        # send_message_to_user_directly call not found
        logger.debug(
            "ChatModule._extract_user_visible_response: "
            "send_message_to_user_directly tool call not found, Agent did not reply to user"
        )
        return "(Agent decided no response needed)"

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Data gathering phase - Dual-track memory loading (2026-01-21 P1-2)

        ChatModule in this phase:
        1. Load long-term memory: Current Narrative's ChatModule instance history
        2. Load short-term memory: User's recent conversations in other Narratives (most recent N minutes)
        3. Mark each message's memory_type (long_term / short_term)
        4. Fill merged conversation history into ctx_data.chat_history

        Args:
            ctx_data: ContextData, containing instance_id and instance_ids list

        Returns:
            ContextData: Context data with chat_history populated
        """
        module_name = self.config.name

        # Get the Instance ID list to query (long-term memory)
        # Prioritize self.instance_ids (set in AgentRuntime)
        current_instance_ids = []
        if self.instance_ids:
            current_instance_ids = self.instance_ids
            logger.debug(f"ChatModule.hook_data_gathering: Long-term memory Instance IDs: {len(current_instance_ids)}")
        elif self.instance_id:
            current_instance_ids = [self.instance_id]
            logger.debug(f"ChatModule.hook_data_gathering: Long-term memory single Instance ID: {self.instance_id}")

        if not current_instance_ids:
            logger.debug("ChatModule.hook_data_gathering: No instance_id, skipping history retrieval")
            ctx_data.chat_history = []
            return ctx_data

        # ========== 1. Load long-term memory (Current Narrative's EverMemOS semantically relevant history) ==========
        # 2026-02-09 optimization: Use EverMemOS episode_contents instead of full DB history
        long_term_messages = []
        current_narrative_id = ctx_data.narrative_id
        evermemos_memories = ctx_data.extra_data.get("evermemos_memories") if ctx_data.extra_data else None

        if evermemos_memories and current_narrative_id:
            # Priority path: Extract current Narrative's episode_contents from EverMemOS cache
            # Count is already controlled by EverMemOS retrieval top_k, no further limiting needed
            current_narrative_data = evermemos_memories.get(current_narrative_id)
            if current_narrative_data:
                episode_contents = current_narrative_data.get("episode_contents", [])
                topic_hint = current_narrative_data.get("topic_hint", "")

                for content in episode_contents:
                    # Directly use raw text format ("User: xxx\nAssistant: xxx")
                    long_term_messages.append({
                        "role": "context",  # Special role, indicates context from EverMemOS
                        "content": content,
                        "meta_data": {
                            "memory_type": "long_term",
                            "source": "evermemos",
                            "narrative_id": current_narrative_id,
                            "topic_hint": topic_hint,
                            "timestamp": ""  # EverMemOS has no timestamp
                        }
                    })

                logger.info(
                    f"ChatModule: Long-term memory - Retrieved {len(long_term_messages)} episodes from EverMemOS, "
                    f"topic_hint: {topic_hint}"
                )
                # Output preview of each episode content (for debugging)
                for i, content in enumerate(episode_contents):
                    # Truncate to first 500 characters as preview (for more complete viewing)
                    preview = content[:500].replace('\n', ' ')
                    if len(content) > 500:
                        preview += "..."
                    logger.debug(
                        f"ChatModule: Long-term memory [{i+1}/{len(episode_contents)}] (length {len(content)}): {preview}"
                    )
        else:
            # Fallback path: Load Instance conversation history from DB event_memory_module
            if self.event_memory_module:
                for instance_id in current_instance_ids:
                    memory = await self.event_memory_module.search_instance_json_format_memory(module_name, instance_id)

                    if memory and "messages" in memory:
                        messages = memory.get("messages", [])
                        for msg in messages:
                            if "meta_data" not in msg:
                                msg["meta_data"] = {}
                            msg["meta_data"]["instance_id"] = instance_id
                            msg["meta_data"]["memory_type"] = "long_term"

                            # Messages from non-chat sources (job/a2a): only load assistant side
                            working_source = msg.get("meta_data", {}).get("working_source", "chat")
                            if working_source != "chat" and msg.get("role") != "assistant":
                                continue

                            long_term_messages.append(msg)
                        logger.debug(
                            f"ChatModule: Long-term memory (DB fallback) - Instance {instance_id} retrieved {len(messages)} messages"
                        )
            else:
                logger.debug(
                    f"ChatModule: Long-term memory is empty - "
                    f"narrative_id={current_narrative_id}, "
                    f"evermemos_memories/event_memory_module both unavailable"
                )

        # Limit long-term memory count: keep only the most recent 20 conversation rounds (40 messages)
        MAX_LONG_TERM_ROUNDS = 20
        MAX_LONG_TERM_MESSAGES = MAX_LONG_TERM_ROUNDS * 2  # Each round = 1 user + 1 assistant
        if len(long_term_messages) > MAX_LONG_TERM_MESSAGES:
            original_count = len(long_term_messages)
            long_term_messages = long_term_messages[-MAX_LONG_TERM_MESSAGES:]
            logger.info(
                f"ChatModule: Long-term memory truncated - original {original_count} messages, kept most recent {MAX_LONG_TERM_MESSAGES}"
            )

        # ========== 2. Load short-term memory (recent cross-Narrative conversations) ==========
        short_term_messages = []
        if self.event_memory_module and self.agent_id and self.user_id:
            try:
                short_term_messages = await self._load_short_term_memory(
                    module_name=module_name,
                    exclude_instance_ids=current_instance_ids
                )
                if short_term_messages:
                    logger.debug(
                        f"ChatModule: Short-term memory - Retrieved {len(short_term_messages)} messages"
                    )
            except Exception as e:
                logger.warning(f"ChatModule: Short-term memory loading failed: {e}")

        # ========== 3. Merge and sort ==========
        all_messages = long_term_messages + short_term_messages

        if all_messages:
            def get_timestamp(msg):
                meta = msg.get("meta_data", {})
                timestamp = meta.get("timestamp", "")
                return timestamp if timestamp else "0000-00-00T00:00:00"

            all_messages.sort(key=get_timestamp)
            logger.info(
                f"ChatModule.hook_data_gathering: Dual-track loading complete - "
                f"long-term memory {len(long_term_messages)} messages, "
                f"short-term memory {len(short_term_messages)} messages, "
                f"total {len(all_messages)} messages"
            )
        else:
            logger.debug("ChatModule.hook_data_gathering: No history messages retrieved")

        # Fill merged history messages into ctx_data
        ctx_data.chat_history = all_messages
        return ctx_data

    async def _load_short_term_memory(
        self,
        module_name: str,
        exclude_instance_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Load short-term memory (recent cross-Narrative conversations) (2026-01-21 P1-2, 2026-02-09 optimization)

        Query user's ChatModule instances in other Narratives, get the most recent K messages (no time limit).

        Optimization notes (2026-02-09):
        - Removed 30-minute time window limit
        - Changed to return the most recent SHORT_TERM_MAX_MESSAGES messages
        - Reason: Time limit caused short-term memory to be empty for inactive users

        Args:
            module_name: Module name (used for querying memory table)
            exclude_instance_ids: Instance IDs to exclude (current Narrative's instances)

        Returns:
            Short-term memory message list (marked with memory_type="short_term")
        """
        from xyz_agent_context.utils.db_factory import get_db_client
        from xyz_agent_context.repository import InstanceRepository

        # Get all other ChatModule instances for the user
        db_client = await get_db_client()
        instance_repo = InstanceRepository(db_client)

        other_instances = await instance_repo.get_chat_instances_by_user(
            agent_id=self.agent_id,
            user_id=self.user_id,
            exclude_instance_ids=exclude_instance_ids
        )

        if not other_instances:
            logger.debug("ChatModule._load_short_term_memory: No other ChatModule instances")
            return []

        # Get messages from each instance (no longer time-limited)
        short_term_messages = []

        for instance in other_instances:
            memory = await self.event_memory_module.search_instance_json_format_memory(
                module_name, instance.instance_id
            )

            if not memory or "messages" not in memory:
                continue

            messages = memory.get("messages", [])

            # Collect all messages (no longer filtered by time)
            for msg in messages:
                meta = msg.get("meta_data", {})

                # Messages from non-chat sources (job/a2a): only load assistant side
                working_source = meta.get("working_source", "chat")
                if working_source != "chat" and msg.get("role") != "assistant":
                    continue

                # Mark as short-term memory
                if "meta_data" not in msg:
                    msg["meta_data"] = {}
                msg["meta_data"]["instance_id"] = instance.instance_id
                msg["meta_data"]["memory_type"] = "short_term"
                short_term_messages.append(msg)

        # Sort by time, limit count
        if short_term_messages:
            short_term_messages.sort(
                key=lambda m: m.get("meta_data", {}).get("timestamp", ""),
                reverse=True
            )
            # Take the most recent N messages
            short_term_messages = short_term_messages[:self.SHORT_TERM_MAX_MESSAGES]
            # Sort by time in ascending order again
            short_term_messages.sort(
                key=lambda m: m.get("meta_data", {}).get("timestamp", "")
            )

        logger.debug(
            f"ChatModule._load_short_term_memory: Retrieved "
            f"{len(short_term_messages)} short-term memory messages from {len(other_instances)} instances"
        )

        return short_term_messages

    async def hook_after_event_execution(self, params: HookAfterExecutionParams) -> None:
        """
        Post-event execution phase - Save conversation records to EventMemoryModule

        ChatModule in this phase:
        1. Append this conversation (input + output) to conversation history (stored by instance_id)
        2. Update Module status report (Report Memory) for Narrative orchestration use

        Note: assistant messages store the content parameter from the send_message_to_user_directly tool call,
        not final_output (Agent's thinking result). This ensures chat history displays the Agent's
        actual reply to the user, not the internal thinking process.

        Args:
            params: HookAfterExecutionParams, containing execution context, input/output, etc.
        """
        # Get necessary information
        instance_id = self.instance_id
        narrative_id = params.ctx_data.narrative_id if params.ctx_data else None
        module_name = self.config.name

        # If no instance_id or event_memory_module, skip
        if not instance_id or not self.event_memory_module:
            logger.debug(
                f"ChatModule.hook_after_event_execution: Missing necessary information, skipping "
                f"(instance_id={instance_id}, event_memory_module={self.event_memory_module is not None})"
            )
            return

        # ========== 1. Update conversation history (Instance-based JSON Format Memory) ==========
        # Get existing history (using instance_id)
        existing_memory = await self.event_memory_module.search_instance_json_format_memory(module_name, instance_id)
        messages = existing_memory.get("messages", []) if existing_memory else []

        # Bootstrap greeting injection: if this is the first turn and bootstrap is active,
        # prepend the static greeting as the first assistant message so DB history starts with it.
        if len(messages) == 0 and getattr(params.ctx_data, 'bootstrap_active', False):
            messages.append({
                "role": "assistant",
                "content": BOOTSTRAP_GREETING,
                "meta_data": {
                    "event_id": params.event_id,
                    "timestamp": utc_now().isoformat(),
                    "instance_id": instance_id,
                    "bootstrap": True,
                }
            })
            logger.debug("ChatModule: Prepended bootstrap greeting as first assistant message")

        # Append this conversation
        # Get working_source (execution source: chat/job/a2a)
        working_source = params.execution_ctx.working_source.value if params.execution_ctx else "unknown"

        # Build shared meta_data fields
        shared_meta = {
            "event_id": params.event_id,
            "timestamp": utc_now().isoformat(),
            "instance_id": instance_id,
            "working_source": working_source,
        }

        # Inject channel_tag if available (set by Triggers for source tracking)
        if params.ctx_data and params.ctx_data.extra_data:
            channel_tag_data = params.ctx_data.extra_data.get("channel_tag")
            if channel_tag_data:
                # Ensure channel_tag is always stored as dict (not ChannelTag object)
                if hasattr(channel_tag_data, "to_dict"):
                    channel_tag_data = channel_tag_data.to_dict()
                shared_meta["channel_tag"] = channel_tag_data

        # User message
        messages.append({
            "role": "user",
            "content": params.input_content,
            "meta_data": {**shared_meta},
        })

        # Assistant message - Extract actual reply content from send_message_to_user_directly tool call
        # Instead of using final_output (which is the Agent's internal thinking result)
        assistant_content = self._extract_user_visible_response(params.agent_loop_response)

        messages.append({
            "role": "assistant",
            "content": assistant_content,
            "meta_data": {**shared_meta},
        })

        # Save updated history (using instance_id)
        memory = {
            "messages": messages,
            "last_event_id": params.event_id,
            "updated_at": utc_now().isoformat()
        }
        await self.event_memory_module.add_instance_json_format_memory(module_name, instance_id, memory)

        logger.debug(
            f"ChatModule.hook_after_event_execution: Conversation record saved successfully, "
            f"instance_id={instance_id}, total messages={len(messages)}"
        )

        # ========== 2. Update status report (Report Memory) ==========
        # Generate status report for Narrative orchestration decision use
        # Note: Report Memory still uses narrative_id because it is a Narrative-level report
        if narrative_id:
            # TODO: This part may need additional LLM processing.
            total_rounds = len(messages) // 2  # Conversation rounds
            last_user_msg = params.input_content[:50] + "..." if len(params.input_content) > 50 else params.input_content
            # Use actual reply content (assistant_content) instead of final_output
            last_assistant_msg = assistant_content[:50] + "..." if len(assistant_content) > 50 else assistant_content

            report = (
                f"Conversation rounds: {total_rounds} | "
                f"Instance: {instance_id} | "
                f"Latest user message: {last_user_msg} | "
                f"Latest reply: {last_assistant_msg}"
            )

            await self.event_memory_module.update_report_memory(
                narrative_id=narrative_id,
                module_name=module_name,
                report_memory=report
            )

            logger.debug(
                f"ChatModule.hook_after_event_execution: Status report updated successfully, "
                f"narrative_id={narrative_id}, instance_id={instance_id}"
            )
        
        