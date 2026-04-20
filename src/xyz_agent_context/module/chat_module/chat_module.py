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
- send_message_to_user_directly: Agent speaks to user (the ONLY way to deliver messages to the user)
- get_chat_history: Get chat history for a Chat Instance

Note: ChatModule itself does not include "multi-turn conversation" capability; multi-turn conversation requires Social-Network/Memory modules
"""


from typing import Optional, Any, List, Dict
from loguru import logger


# Module (same package)
from xyz_agent_context.module import XYZBaseModule, mcp_host
from xyz_agent_context.module.event_memory_module import EventMemoryModule

# Schema
from xyz_agent_context.schema import (
    ContextData,
    HookAfterExecutionParams,
    ModuleConfig,
    MCPServerConfig,
)

# Utils
from xyz_agent_context.utils import DatabaseClient, utc_now

# Repository
from xyz_agent_context.repository import AgentMessageRepository

# Schema
from xyz_agent_context.schema.agent_message_schema import MessageSourceType

# Prompts
from xyz_agent_context.module.chat_module.prompts import CHAT_MODULE_INSTRUCTIONS
from xyz_agent_context.bootstrap.template import BOOTSTRAP_GREETING


# =============================================================================
# Bug 8 · Failed-turn isolation
#
# When a turn errors out (rate limit, API hiccup, tool exception), the agent
# loop yields an ErrorMessage and stops early. Pre-fix, ChatModule stored the
# turn as a normal (user, "") pair — so the next turn's prompt showed the
# user's failed question with an empty assistant reply, and the LLM would
# treat it as "I didn't finish last time" and retry instead of answering the
# new user input.
#
# Two halves:
#
# 1. Storage: when ``_detect_error_in_agent_loop`` finds an ErrorMessage in
#    ``agent_loop_response``, we persist ONLY the user question, tagged with
#    ``meta_data.status="failed"`` + ``meta_data.error_type``. No fake
#    assistant row. Partial output that streamed before the crash is
#    discarded — it was never a complete answer.
#
# 2. Load: when feeding history back into the next turn's prompt, we apply
#    ``_apply_failed_turn_filter`` to both long-term and short-term message
#    lists:
#      - failed USER rows → content rewritten to an annotated note that
#        explicitly tells the LLM "this errored, do NOT retry"
#      - failed ASSISTANT rows (legacy, pre-fix) → dropped defensively
# =============================================================================

_FAILED_TURN_ANNOTATION_TEMPLATE = (
    "[Previous turn failed before the agent could reply. "
    "The user's original question was: {original!r}. "
    "An error ({error_type}) occurred and no reply was given. "
    "Do NOT retry this question — focus on the current user input.]"
)


def _detect_error_in_agent_loop(agent_loop_response: List[Any]) -> Optional[Dict[str, str]]:
    """Scan ``agent_loop_response`` for an ``ErrorMessage`` and return the
    first one's signal, or ``None`` if the turn succeeded.

    Import is local so the module doesn't couple to ``runtime_message``
    at import time (keeps test fixtures simple)."""
    from xyz_agent_context.schema import ErrorMessage
    for msg in agent_loop_response:
        if isinstance(msg, ErrorMessage):
            return {
                "error_type": msg.error_type,
                "error_message": msg.error_message,
            }
    return None


def _apply_failed_turn_filter(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prepare a history list for the next turn's prompt, isolating
    failed turns so they can't trick the LLM into retrying them.

    Shape contract (messages are the list stored in Instance JSON memory):
      - rule A: role=user + meta_data.status==failed → content replaced
        with an annotated "do NOT retry" note that also preserves the
        original wording for pronoun resolution.
      - rule B: role=assistant + meta_data.status==failed → dropped.
        (The storage half should never write these after the fix, but
        we tolerate legacy rows.)
      - everything else passes through untouched.

    Returns a NEW list; does not mutate the input messages.
    """
    out: List[Dict[str, Any]] = []
    for msg in messages:
        meta = msg.get("meta_data") or {}
        if meta.get("status") != "failed":
            out.append(msg)
            continue
        role = msg.get("role")
        if role == "user":
            annotated = dict(msg)
            annotated["content"] = _FAILED_TURN_ANNOTATION_TEMPLATE.format(
                original=msg.get("content", ""),
                error_type=meta.get("error_type", "unknown"),
            )
            out.append(annotated)
        # role == "assistant" with status=failed → drop
    return out


class ChatModule(XYZBaseModule):
    """
    Chat Module - Core module for Agent-user communication

    Core concept - Thinking vs Speaking:
    Agent's internal processing (LLM calls, Agent Loop, tool calls) is like thinking in your head, completely invisible to users.
    Only through the send_message_to_user_directly tool can the Agent "speak", and only then can users receive the Agent's response.

    Provided capabilities:
    1. **Instructions** - Guide Agent to understand the "thinking vs speaking" distinction
    2. **Tools** (via MCP):
       - send_message_to_user_directly: The ONLY way to deliver messages to the user
       - get_chat_history: Retrieve past conversations for a specific Chat Instance

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
            description="Provides messaging capabilities (chat conversation + history retrieval)"
        )

    # ============================================================================= MCP Server

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """
        Return MCP Server configuration

        ChatModule provides MCP Server for:
        - send_message_to_user_directly: Agent speaks to user
        - get_chat_history: Retrieve past conversations

        Returns:
            MCPServerConfig
        """
        return MCPServerConfig(
            server_name="chat_module",
            server_url=f"http://{mcp_host()}:{self.port}/sse",
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
        return f"http://{mcp_host()}:{self.port}/sse"
    
    
    # ============================================================================= Hooks

    def _extract_user_visible_response(self, agent_loop_response: list) -> str:
        """
        Extract user-visible response content from agent_loop_response

        Iterates through agent_loop_response, looking for ALL send_message_to_user_directly
        tool calls, and concatenates their content. Agent may call this tool multiple times
        in a single turn (e.g. sending a greeting then a detailed answer).

        Args:
            agent_loop_response: Raw response list from Agent Loop, containing ProgressMessage etc.

        Returns:
            str: Concatenated user-visible response content; returns default message if not called
        """
        from xyz_agent_context.schema import ProgressMessage

        parts = []
        for response in agent_loop_response:
            # Check if it's a ProgressMessage (tool calls are wrapped as ProgressMessage)
            if isinstance(response, ProgressMessage) and response.details:
                tool_name = response.details.get("tool_name", "")
                # Match send_message_to_user_directly (frontend uses endsWith matching)
                if tool_name.endswith("send_message_to_user_directly"):
                    arguments = response.details.get("arguments", {})
                    content = arguments.get("content", "")
                    if content:
                        parts.append(content)

        if parts:
            combined = "\n\n".join(parts)
            logger.debug(
                f"ChatModule._extract_user_visible_response: "
                f"Extracted {len(parts)} reply(s), total length {len(combined)}"
            )
            return combined

        # send_message_to_user_directly call not found
        logger.debug(
            "ChatModule._extract_user_visible_response: "
            "send_message_to_user_directly tool call not found, Agent did not reply to user"
        )
        return "(Agent decided no response needed)"

    @staticmethod
    def _build_activity_summary(working_source: str, meta: dict) -> str:
        """
        Build a human-readable activity summary for background tasks
        where the agent chose not to send a message to the user.

        Args:
            working_source: Execution source ("job", "message_bus", etc.)
            meta: Shared meta_data dict (may contain channel_tag)

        Returns:
            Short activity description string
        """
        if working_source == "job":
            return "Executed a background job"

        return f"Background activity ({working_source})"

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

        # ========== 1. Load long-term memory (always from ChatModule DB) ==========
        # After EverMemOS decoupling: always load from DB. EverMemOS episodes are
        # provided separately as "Relevant Memory" in the system prompt.
        long_term_messages = []

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
                        f"[ChatHistory-A] Instance {instance_id}: {len(messages)} messages loaded"
                    )

        # Limit to most recent 30 messages (Part A: recency-based)
        MAX_RECENT_MESSAGES = 30
        if len(long_term_messages) > MAX_RECENT_MESSAGES:
            original_count = len(long_term_messages)
            long_term_messages = long_term_messages[-MAX_RECENT_MESSAGES:]
            logger.info(
                f"[ChatHistory-A] Truncated: {original_count} → {MAX_RECENT_MESSAGES} messages"
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

        # Bug 8: transform failed-turn rows before feeding history back
        # into the next prompt — failed user rows get an annotated "do
        # NOT retry" note, failed assistant rows (legacy) are dropped.
        long_term_messages = _apply_failed_turn_filter(long_term_messages)
        short_term_messages = _apply_failed_turn_filter(short_term_messages)

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

    async def _embed_message_pair(
        self,
        instance_id: str,
        message_index: int,
        user_content: str,
        assistant_content: str,
        event_id: str = "",
    ) -> None:
        """
        Embed a user+assistant message pair and store for Part B retrieval.

        The content is stored in the same format used for prompt context building:
        "User: {user_content}\nAssistant: {assistant_content}"
        """
        from xyz_agent_context.utils.db_factory import get_db_client
        from xyz_agent_context.repository.chat_message_embedding_repository import (
            ChatMessageEmbeddingRepository,
        )
        from xyz_agent_context.agent_framework.llm_api.embedding import get_embedding

        # Build content in prompt format
        content = f"User: {user_content}\nAssistant: {assistant_content}"

        # Generate embedding (truncate source text for embedding to ~500 chars)
        source_text = content[:500]
        embedding = await get_embedding(source_text)

        if not embedding:
            return

        db = await get_db_client()
        repo = ChatMessageEmbeddingRepository(db)
        await repo.upsert(
            instance_id=instance_id,
            message_index=message_index,
            content=content,
            embedding=embedding,
            source_text=source_text,
            event_id=event_id,
            role="pair",
        )

        logger.debug(
            f"[ChatHistory-B] Embedded message pair: instance={instance_id}, "
            f"index={message_index}, content_len={len(content)}"
        )

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

        # Bug 8: detect failure FIRST. If the agent loop raised, persist
        # only the user question with status=failed so the next turn's
        # prompt (after _apply_failed_turn_filter) shows an explicit
        # "do not retry" annotation instead of a fake completed pair.
        error_signal = _detect_error_in_agent_loop(params.agent_loop_response)

        # Extract the user-visible response (from send_message_to_user_directly tool call)
        assistant_content = self._extract_user_visible_response(params.agent_loop_response)
        is_no_response = assistant_content == "(Agent decided no response needed)"

        if error_signal is not None:
            # Failed turn: preserve user question (for reference), skip assistant.
            messages.append({
                "role": "user",
                "content": params.input_content,
                "meta_data": {
                    **shared_meta,
                    "status": "failed",
                    "error_type": error_signal["error_type"],
                },
            })
        elif working_source == "chat" or not is_no_response:
            # Normal conversation: store user message + assistant reply
            messages.append({
                "role": "user",
                "content": params.input_content,
                "meta_data": {**shared_meta},
            })
            messages.append({
                "role": "assistant",
                "content": assistant_content,
                "meta_data": {**shared_meta},
            })
        else:
            # Background task (job/lark/message_bus) where agent chose not to message user:
            # Store a lightweight activity record instead of a fake conversation pair
            activity_summary = self._build_activity_summary(working_source, shared_meta)
            messages.append({
                "role": "assistant",
                "content": activity_summary,
                "meta_data": {**shared_meta, "message_type": "activity"},
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

        # ========== 3. Embed message pair for Part B retrieval ==========
        try:
            await self._embed_message_pair(
                instance_id=instance_id,
                message_index=len(messages) - 1,  # index of the last pair
                user_content=params.input_content,
                assistant_content=assistant_content,
                event_id=params.event_id,
            )
        except Exception as e:
            logger.warning(f"[ChatHistory-B] Embedding failed (non-fatal): {e}")

