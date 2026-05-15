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


from datetime import timedelta
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
from xyz_agent_context.schema.attachment_schema import Attachment

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
    "Error type: {error_type}. Detail: {error_message}. "
    "Do NOT retry this question — focus on the current user input.]"
)


def _synthesize_attachment_markers(
    attachments: Optional[List[Dict[str, Any]]],
    agent_id: str,
    user_id: str,
) -> str:
    """Render a list of attachment dicts as natural-language markers.

    Used by hook_data_gathering when assembling chat_history for the LLM —
    the persisted message keeps its `content` as the user's original text,
    and the markers (which carry the resolved absolute path on disk) are
    appended only into the in-memory copy fed to the next turn's prompt.
    Invalid entries are skipped silently rather than failing the whole
    turn (defensive: the dict shape is data we control, but a future
    schema change shouldn't crash old conversations).

    Path resolution requires the workspace owner's identity, so
    `agent_id` and `user_id` are threaded through; for orphaned uploads
    (file deleted) the marker still reports `path=<unavailable>`.
    """
    if not attachments:
        return ""
    lines: List[str] = []
    for att in attachments:
        try:
            marker = Attachment.model_validate(att).synthesize_marker(
                agent_id=agent_id, user_id=user_id,
            )
            lines.append(marker)
        except Exception as e:
            logger.warning(f"Skipping malformed attachment in chat history: {e}")
    return "\n".join(lines)


def _detect_fatal_error_in_agent_loop(
    agent_loop_response: List[Any],
) -> Optional[Dict[str, str]]:
    """Scan ``agent_loop_response`` for a **fatal** ``ErrorMessage`` and
    return its signal, or ``None`` if the turn either succeeded or only
    saw recoverable errors.

    Why "fatal" only: tearing down a whole turn into a `status=failed`
    user-only row is the right answer for unrecoverable framework
    errors (CLI timeout, SDK crash, auth failure) — the agent literally
    cannot reply. But for recoverable signals (transient rate-limit,
    one-shot 5xx that the SDK already retried, etc.) the agent loop
    can keep going; treating those as turn-killers is exactly what
    caused the "agent decided no response needed" baseline noise.

    Import is local so the module doesn't couple to ``runtime_message``
    at import time (keeps test fixtures simple)."""
    from xyz_agent_context.schema import ErrorMessage
    for msg in agent_loop_response:
        if not isinstance(msg, ErrorMessage):
            continue
        if getattr(msg, "severity", "fatal") != "fatal":
            continue
        return {
            "error_type": msg.error_type,
            "error_message": msg.error_message,
        }
    return None


# Backwards-compatible alias — existing tests / callers import the old
# name. New code should use _detect_fatal_error_in_agent_loop directly.
_detect_error_in_agent_loop = _detect_fatal_error_in_agent_loop


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
                error_message=meta.get("error_message", "no detail captured"),
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
    SHORT_TERM_MAX_MESSAGES = 15    # Hard cap on total short-term rows (across all other ChatModule instances)
    SHORT_TERM_PER_INSTANCE = 5      # Per-instance cap before global merge (fairness — see _load_short_term_memory)
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

    def _extract_user_visible_response(
        self,
        agent_loop_response: list,
        working_source: str,
    ) -> str:
        """Extract user-visible reply text emitted during this turn.

        Per-source dispatch via MessageSourceRegistry: each WorkingSource
        (chat / lark / message_bus / job / …) registers which tool names
        count as the agent replying to the user. Chat uses
        send_message_to_user_directly; Lark also accepts lark_cli
        +messages-send / +messages-reply; bus accepts its own bus_send;
        etc. Without this dispatch, Lark turns where the agent really
        did reply via lark_cli would be misclassified as "no response"
        and persisted as activity rows — the actual P0 we are fixing.
        """
        _im, direct, combined = self._split_user_visible_response(
            agent_loop_response, working_source
        )
        if combined:
            return combined
        return "(Agent decided no response needed)"

    def _split_user_visible_response(
        self,
        agent_loop_response: list,
        working_source: str,
    ) -> tuple[str, str, str]:
        """Split the user-visible response into IM-tool vs direct-notify halves.

        For IM working_sources (telegram/slack/lark/...), the agent may
        legitimately fire BOTH paths in one turn:
          - the platform reply tool (tg_cli sendMessage, slack_cli
            chat.postMessage, lark_cli +messages-send/reply), which goes
            back to the IM sender;
          - ``send_message_to_user_directly``, which surfaces in the
            owner's chat panel for the "this is important, the owner
            should know about it" carve-out spelled out in the iron
            rules.

        Both currently get joined into one ``assistant_content`` string,
        which means downstream consumers can't tell them apart. The
        chat-history endpoint needs the split so it can render the
        direct-notify text in full while replacing the routine IM reply
        with a "Background activity" placeholder.

        Returns ``(im_reply, direct_notify, combined)``:
          - ``im_reply``: parts that came from non-direct platform tools.
          - ``direct_notify``: parts from ``send_message_to_user_directly``.
          - ``combined``: the original "\\n\\n"-joined string, preserved
            for callers (long-term memory write, log lines) that want
            the full picture.

        For working_source="chat", direct_notify will hold everything
        (the handler only matches ``send_message_to_user_directly``)
        and im_reply will be empty — backward-compatible.
        """
        from xyz_agent_context.schema import ProgressMessage
        from xyz_agent_context.channel.message_source_handler import (
            MessageSourceRegistry,
        )

        handler = MessageSourceRegistry.get(working_source)
        im_parts: List[str] = []
        direct_parts: List[str] = []
        for response in agent_loop_response:
            if not (isinstance(response, ProgressMessage) and response.details):
                continue
            tool_name = response.details.get("tool_name", "")
            arguments = response.details.get("arguments", {})
            reply = handler.extract_reply_text(tool_name, arguments)
            if not reply:
                continue
            # send_message_to_user_directly is the owner-notify path
            # regardless of which channel triggered the turn.
            if "send_message_to_user_directly" in tool_name:
                direct_parts.append(reply)
            else:
                im_parts.append(reply)

        im_reply = "\n\n".join(im_parts)
        direct_notify = "\n\n".join(direct_parts)
        combined = "\n\n".join(p for p in (im_reply, direct_notify) if p)

        if combined:
            logger.info(
                f"[CHAT-CTX] _split_user_visible_response: ws={working_source} "
                f"handler={handler.name} im_parts={len(im_parts)} "
                f"direct_parts={len(direct_parts)} total_len={len(combined)}"
            )
        else:
            logger.info(
                f"[CHAT-CTX] _split_user_visible_response: ws={working_source} "
                f"handler={handler.name} no reply tool matched "
                f"(checked patterns={handler.user_reply_tool_names})"
            )
        return im_reply, direct_notify, combined

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

                        # Drop background activity rows — they are agent
                        # housekeeping (Matrix/Lark cascade forwards a
                        # job did not reply to a user about), not real
                        # dialogue. Once Phase 2 lands new turns will
                        # only get message_type=activity when the agent
                        # truly did not reply to anyone; pre-existing
                        # mislabelled rows from before that fix are no
                        # longer salvageable (content was already
                        # rewritten by _build_activity_summary), so
                        # filtering loses nothing the LLM could use.
                        if msg["meta_data"].get("message_type") == "activity":
                            continue

                        # Messages from non-chat sources (job/a2a): only load assistant side
                        working_source = msg.get("meta_data", {}).get("working_source", "chat")
                        if working_source != "chat" and msg.get("role") != "assistant":
                            continue

                        # If the user attached files in this turn, append
                        # natural-language markers to the in-memory copy of
                        # the message. Markers carry the resolved absolute
                        # path so the agent can call its built-in `Read`
                        # tool directly. Persisted `content` is left
                        # untouched.
                        attachments = msg.get("attachments")
                        if attachments:
                            markers = _synthesize_attachment_markers(
                                attachments,
                                agent_id=self.agent_id,
                                user_id=self.user_id or "",
                            )
                            if markers:
                                original_content = msg.get("content") or ""
                                msg = {
                                    **msg,
                                    "content": (
                                        f"{original_content}\n{markers}"
                                        if original_content
                                        else markers
                                    ),
                                }

                        long_term_messages.append(msg)
                    logger.debug(
                        f"[ChatHistory-A] Instance {instance_id}: {len(messages)} messages loaded"
                    )

        # Limit to most recent N messages (Part A: recency-based).
        # 40 (raised from 30 on 2026-05-11) — long narratives were
        # hitting the old cap and losing earlier context. 40 is a
        # compromise: enough headroom for a meaningful conversation arc
        # without bloating the prompt; if this becomes the limiting
        # factor again, consider a token-based cap instead of a count.
        MAX_RECENT_MESSAGES = 40
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

        # Splice persisted reasoning (see hook_after_event_execution) back
        # into assistant message content, wrapped with tag markers so the
        # next turn's LLM can tell "what I thought last turn" apart from
        # "what I said to the user last turn". Tool-call outputs are not
        # preserved across turns — this splicing is the mechanism that
        # lets the Agent carry machine-readable values (device codes,
        # job ids, fresh URLs) forward, by relying on the Agent having
        # restated them in its own reasoning before ending the turn.
        for _msg in all_messages:
            if _msg.get("role") != "assistant":
                continue
            _reasoning = (_msg.get("meta_data") or {}).get("reasoning")
            if not _reasoning:
                continue
            _original = _msg.get("content", "") or ""
            _msg["content"] = (
                f"<my_reasoning>\n{_reasoning}\n</my_reasoning>\n\n"
                f"<reply_to_user>\n{_original}\n</reply_to_user>"
            )

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

        # Two-stage budgeting (2026-05-11 fairness fix):
        # Stage A — per instance, keep only the most recent K rows
        #          (SHORT_TERM_PER_INSTANCE = 5). Prevents one chatty
        #          instance from saturating the 15-slot global cap and
        #          starving every other narrative the user has touched.
        # Stage B — flatten all instances' Stage-A survivors, sort by
        #          timestamp descending, take SHORT_TERM_MAX_MESSAGES.
        short_term_messages: List[Dict[str, Any]] = []
        per_instance_kept = 0

        for instance in other_instances:
            memory = await self.event_memory_module.search_instance_json_format_memory(
                module_name, instance.instance_id
            )
            if not memory or "messages" not in memory:
                continue

            messages = memory.get("messages", [])

            # Filter + tag this instance's messages.
            keepers: List[Dict[str, Any]] = []
            for msg in messages:
                meta = msg.get("meta_data", {})

                # Same activity-row filter as long_term — see
                # hook_data_gathering for the why.
                if meta.get("message_type") == "activity":
                    continue

                # Messages from non-chat sources (job/a2a): only load assistant side
                working_source = meta.get("working_source", "chat")
                if working_source != "chat" and msg.get("role") != "assistant":
                    continue

                # Mark as short-term memory
                if "meta_data" not in msg:
                    msg["meta_data"] = {}
                msg["meta_data"]["instance_id"] = instance.instance_id
                msg["meta_data"]["memory_type"] = "short_term"

                # Append attachment markers for the in-memory chat-history
                # copy without touching the persisted content. Path is
                # resolved against the *current* agent's workspace — that's
                # where the file actually lives even when this short-term
                # message was authored under a different Narrative.
                attachments = msg.get("attachments")
                if attachments:
                    markers = _synthesize_attachment_markers(
                        attachments,
                        agent_id=self.agent_id,
                        user_id=self.user_id or "",
                    )
                    if markers:
                        original_content = msg.get("content") or ""
                        msg = {
                            **msg,
                            "content": (
                                f"{original_content}\n{markers}"
                                if original_content
                                else markers
                            ),
                        }

                keepers.append(msg)

            # Stage A: per-instance cap.
            if len(keepers) > self.SHORT_TERM_PER_INSTANCE:
                keepers.sort(
                    key=lambda m: m.get("meta_data", {}).get("timestamp", ""),
                    reverse=True,
                )
                keepers = keepers[:self.SHORT_TERM_PER_INSTANCE]

            short_term_messages.extend(keepers)
            per_instance_kept += len(keepers)

        # Stage B: global cap + final chronological ordering.
        if short_term_messages:
            short_term_messages.sort(
                key=lambda m: m.get("meta_data", {}).get("timestamp", ""),
                reverse=True
            )
            short_term_messages = short_term_messages[:self.SHORT_TERM_MAX_MESSAGES]
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
        #
        # Timestamp anchor: must be strictly earlier than the user's first
        # message, otherwise the chat-history API and the frontend timeline
        # (both sort by meta_data.timestamp ascending) render the greeting
        # AFTER the user's query bubble. event.created_at is turn-start,
        # so we subtract 1ms; the fallback (no event) uses now()-1ms which
        # also stays below the user message stamped a moment later.
        if len(messages) == 0 and getattr(params.ctx_data, 'bootstrap_active', False):
            base_dt = (
                params.event.created_at
                if params.event is not None and params.event.created_at is not None
                else utc_now()
            )
            greeting_ts_iso = (base_dt - timedelta(milliseconds=1)).isoformat()
            messages.append({
                "role": "assistant",
                "content": BOOTSTRAP_GREETING,
                "meta_data": {
                    "event_id": params.event_id,
                    "timestamp": greeting_ts_iso,
                    "instance_id": instance_id,
                    "bootstrap": True,
                }
            })
            logger.debug("ChatModule: Prepended bootstrap greeting as first assistant message")

        # Append this conversation
        # Get working_source (execution source: chat/job/a2a)
        working_source = params.execution_ctx.working_source.value if params.execution_ctx else "unknown"

        # Timestamp policy (fix: frontend dedup window) — user and assistant
        # messages get DIFFERENT timestamps:
        #   - user    → Event.created_at   (when the turn started, ~= when the
        #              user pressed Enter; matches frontend session ts within RTT)
        #   - assistant → utc_now()         (when this hook runs, ~= when the
        #              agent finished; matches frontend stopStreaming ts)
        # Before this split, both messages shared utc_now() which, for a slow
        # turn, put the persisted user-message ts minutes past the frontend
        # session ts. The dedup in ChatPanel (|session_ts - history_ts| <
        # window) then failed and the user bubble rendered twice.
        now_iso = utc_now().isoformat()
        user_ts_iso = (
            params.event.created_at.isoformat()
            if params.event is not None and params.event.created_at is not None
            else now_iso
        )

        # Build shared meta_data fields (assistant uses now; user overrides below)
        shared_meta = {
            "event_id": params.event_id,
            "timestamp": now_iso,
            "instance_id": instance_id,
            "working_source": working_source,
        }

        # Reasoning persistence (2026-04-23): tool-call outputs are ephemeral
        # to the turn, but the Agent's written reasoning (final_output) is
        # the one channel that can carry machine-readable values (device
        # codes, job ids, freshly minted URLs) into the next turn. Capture
        # it on assistant meta_data so hook_data_gathering can splice it
        # back into content when building next turn's chat history. Stored
        # full — truncation was explored and rejected: (a) the Agent writes
        # the reasoning itself, so it's already self-limited; (b) a cap
        # risks cutting exactly the value the Agent wanted to carry across.
        assistant_reasoning: str = (
            (params.io_data.final_output if params.io_data else "") or ""
        )
        assistant_meta = (
            {**shared_meta, "reasoning": assistant_reasoning}
            if assistant_reasoning
            else {**shared_meta}
        )

        # Inject channel_tag if available (set by Triggers for source tracking)
        if params.ctx_data and params.ctx_data.extra_data:
            channel_tag_data = params.ctx_data.extra_data.get("channel_tag")
            if channel_tag_data:
                # Ensure channel_tag is always stored as dict (not ChannelTag object)
                if hasattr(channel_tag_data, "to_dict"):
                    channel_tag_data = channel_tag_data.to_dict()
                shared_meta["channel_tag"] = channel_tag_data

        user_meta = {**shared_meta, "timestamp": user_ts_iso}

        # Bug 8 + 2026-05-11: detect FATAL framework error first. Recoverable
        # ErrorMessages (severity="recoverable") are intentionally NOT
        # treated as turn-killers — they're agent-visible information, not
        # framework failures. Only fatal errors (TimeoutError, SDK crash,
        # auth failure, etc.) collapse the whole turn into a failed
        # user-only row.
        error_signal = _detect_fatal_error_in_agent_loop(params.agent_loop_response)

        # Extract the user-visible response, dispatched by working_source
        # so per-source reply tools (e.g. lark_cli for Lark) are recognised.
        # We split into two parts so the chat-history endpoint can render
        # the owner-notify text in full while the routine IM reply gets a
        # "Background activity" placeholder. ``assistant_content`` keeps
        # the combined form for long-term memory and log lines.
        im_reply_content, direct_notify_content, assistant_content = (
            self._split_user_visible_response(
                params.agent_loop_response, working_source
            )
        )
        if not assistant_content:
            assistant_content = "(Agent decided no response needed)"
        is_no_response = assistant_content == "(Agent decided no response needed)"

        # NOTE (2026-05-12): the previous "use io_data.final_output as
        # reply" fallback was removed — it violated the thinking-vs-
        # speaking design (final_output is the agent's internal reasoning,
        # not a user-facing reply). The real no-reply recovery now lives
        # one layer up: step_3_agent_loop detects a chat turn that ended
        # without send_message_to_user_directly and asks the helper_llm
        # to generate a real reply, streamed to the frontend and emitted
        # as a synthetic send_message ProgressMessage. By the time we get
        # here, _extract_user_visible_response will have already picked
        # that up; if it didn't, the helper_llm fallback failed too and
        # persisting a placeholder is the honest record.

        # Attachments forwarded by the trigger (WebSocket / Lark / etc.)
        # land in ctx_data.extra_data via context_runtime's
        # trigger_extra_data merge. We persist them on the user message
        # row so that hook_data_gathering can synthesize markers when this
        # turn is replayed in future prompts.
        turn_attachments: List[Dict[str, Any]] = []
        if params.ctx_data and params.ctx_data.extra_data:
            raw = params.ctx_data.extra_data.get("attachments")
            if isinstance(raw, list):
                turn_attachments = [a for a in raw if isinstance(a, dict)]

        if error_signal is not None:
            # Fatal turn: preserve user question (for reference), skip assistant.
            # Persist BOTH error_type and error_message so the next turn's
            # annotation tells the agent (and ops) *why* it failed.
            logger.warning(
                f"[TURN-FAILED] event_id={params.event_id} working_source={working_source} "
                f"error_type={error_signal['error_type']} "
                f"error_message={error_signal['error_message'][:200]!r}"
            )
            user_msg = {
                "role": "user",
                "content": params.input_content,
                "meta_data": {
                    **user_meta,
                    "status": "failed",
                    "error_type": error_signal["error_type"],
                    "error_message": error_signal["error_message"],
                },
            }
            if turn_attachments:
                user_msg["attachments"] = turn_attachments
            messages.append(user_msg)
        elif working_source == "chat" or not is_no_response:
            # Normal conversation: store user message + assistant reply
            user_msg = {
                "role": "user",
                "content": params.input_content,
                "meta_data": {**user_meta},
            }
            if turn_attachments:
                user_msg["attachments"] = turn_attachments
            messages.append(user_msg)
            asst_meta = {**assistant_meta}
            # If the upstream agent loop's helper_llm fallback fired
            # (see step_3_agent_loop._generate_fallback_reply_stream),
            # it emits a synthetic send_message_to_user_directly
            # ProgressMessage tagged details.reply_via="helper_llm_fallback".
            # Surface that tag on the persisted row so observability
            # tooling can tell organic vs. recovered replies apart.
            for r in (params.agent_loop_response or []):
                d = getattr(r, "details", None)
                if isinstance(d, dict) and d.get("reply_via") == "helper_llm_fallback":
                    asst_meta["reply_via"] = "helper_llm_fallback"
                    break
            # Stash the owner-notify portion on meta_data when the turn
            # was IM-triggered AND the agent explicitly called
            # send_message_to_user_directly. The chat-history endpoint
            # shows this string verbatim, falling back to the
            # "Background activity (...)" placeholder when absent. We
            # only set it for non-chat triggers — on chat-triggered
            # turns the assistant_content IS the owner-facing reply
            # and the endpoint shows it as-is.
            if working_source != "chat" and direct_notify_content:
                asst_meta["owner_notify_content"] = direct_notify_content
            messages.append({
                "role": "assistant",
                "content": assistant_content,
                "meta_data": asst_meta,
            })
            if is_no_response:
                logger.warning(
                    f"[NO-REPLY] event_id={params.event_id} working_source={working_source} "
                    f"agent_loop_response_size={len(params.agent_loop_response)} "
                    f"final_output_empty=True — persisting placeholder. "
                    f"Likely cancellation or LLM produced zero output."
                )
        else:
            # Background task (job/lark/message_bus) where agent chose not to message user:
            # Store a lightweight activity record instead of a fake conversation pair
            activity_summary = self._build_activity_summary(working_source, shared_meta)
            logger.info(
                f"[NO-REPLY-BG] event_id={params.event_id} working_source={working_source} "
                f"writing activity row (background trigger, no user-facing reply)"
            )
            messages.append({
                "role": "assistant",
                "content": activity_summary,
                "meta_data": {**assistant_meta, "message_type": "activity"},
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

        # ========== 2. Update status report (Report Memory) — DISABLED ==========
        # Originally this fed a per-narrative ChatModule status string into
        # `module_report_memory` so the Narrative could read each module's
        # current state when deciding whether to keep it active. The reader
        # half of that contract was never implemented (`get_report_memory`
        # has no callers anywhere in the codebase as of 2026-04-28), and
        # the writer was failing in production anyway because the on-disk
        # table still has a legacy `instance_id NOT NULL` column that the
        # new schema doesn't fill. We comment out the write rather than
        # delete the code so reviving the feature is a one-block-change
        # job; see .mindflow/mirror/.../event_memory_module.py.md for the
        # full background and the recipe to re-enable.
        # if narrative_id:
        #     total_rounds = len(messages) // 2
        #     last_user_msg = params.input_content[:50] + "..." if len(params.input_content) > 50 else params.input_content
        #     last_assistant_msg = assistant_content[:50] + "..." if len(assistant_content) > 50 else assistant_content
        #
        #     report = (
        #         f"Conversation rounds: {total_rounds} | "
        #         f"Instance: {instance_id} | "
        #         f"Latest user message: {last_user_msg} | "
        #         f"Latest reply: {last_assistant_msg}"
        #     )
        #
        #     await self.event_memory_module.update_report_memory(
        #         narrative_id=narrative_id,
        #         module_name=module_name,
        #         report_memory=report,
        #     )

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

