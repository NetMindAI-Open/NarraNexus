"""
@file_name: chat_module.py
@author: NetMind.AI
@date: 2025-11-15
@description: Chat Module - Provides chat-related functionality

ChatModule provides Agent messaging capabilities on the XYZ-Platform.

Core concept - Thinking vs Speaking:
- All output from Agent's LLM calls, Agent Loop, and tool calls are the Agent's internal thinking, invisible to users
- Only by calling an owner-facing tool does the Agent actually "speak"; plain text reaches nobody
- Like two people talking face-to-face: thinking in your head (invisible) vs speaking out loud (visible)

Included MCP Tools:
- reply_owner / notify_owner: the two registers of speaking to the owner. The
  turn's desk carries exactly one; see expressive_tools / disallowed_tools
- get_chat_history: Get chat history for a Chat Instance

Note: ChatModule itself does not include "multi-turn conversation" capability; multi-turn conversation requires Social-Network/Memory modules
"""


from typing import Optional, Any, List, Dict
from loguru import logger


# Module (same package)
from narranexus.platform.module_system import XYZBaseModule, mcp_server_url
from narranexus.platform.channel.message_source_handler import is_owner_tool
from narranexus.platform.module_system.base import working_source_matches
from narranexus.platform.repository import EventMemoryRepository
from narranexus.platform.schema.module_schema import ModuleDisplay, ModuleDecisionMeta
from narranexus.platform.schema.hook_schema import (
    BUS_PRODUCED_SOURCES,
    is_plain_text_turn,
)

# Schema
from narranexus.platform.schema import (
    ContextData,
    HookAfterExecutionParams,
    ModuleConfig,
    MCPServerConfig,
    WorkingSource,
)
from narranexus.platform.schema.attachment_schema import Attachment

# Utils
from narranexus.platform.utils import DatabaseClient, utc_now

# NOTE (2026-08-05): ChatModule does NOT write the `agent_messages` table.
# Its two imports from that family (`AgentMessageRepository`,
# `MessageSourceType`) were dead and are gone. The chat transcript lives in
# `instance_json_format_memory_chat`, keyed by chat instance_id — that is what
# `persist_turn` below writes and what `/simple-chat-history` replays.
# `agent_messages` has no writer left anywhere and is 0 rows in every
# deployment; it stays as a tombstone table (铁律 #6). Believing otherwise
# already cost one misdiagnosis (0802 ordering report).

# Prompts
from narranexus_plugins.chat_module.prompts import CHAT_MODULE_INSTRUCTIONS
from narranexus.platform.bootstrap.template import BOOTSTRAP_GREETING


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
    """Thin wrapper around ``Attachment.markers_from_dicts``.

    Kept as a module-local alias so existing history-assembly call
    sites (``gather`` at chat_module.py:508 / :889) don't
    have to import the schema helper directly. The runtime-layer
    current-turn injection (context_runtime.build_input_for_framework)
    calls ``Attachment.markers_from_dicts`` directly — the two
    codepaths share one implementation now, so agent behaviour is
    identical for current-turn vs historical attachments.
    """
    return Attachment.markers_from_dicts(
        attachments, agent_id=agent_id, user_id=user_id,
    )


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
    from narranexus.platform.schema import ErrorMessage
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
    Only through an owner-facing tool can the Agent "speak", and only then can users receive the Agent's response.

    Provided capabilities:
    1. **Instructions** - Guide Agent to understand the "thinking vs speaking" distinction
    2. **Tools** (via MCP):
       - reply_owner / notify_owner: the only ways to deliver to the owner,
         one on the desk per turn
       - get_chat_history: Retrieve past conversations for a specific Chat Instance

    Dual-track memory loading (2026-01-21 P1-2):
    - Long-term memory: current Narrative's full conversation history
    - Short-term memory: User's recent cross-Narrative conversations (most recent K messages, no time limit)
    """

    # 2026-05-20 unified-timeline rework (Fix #2): chat history is ONE
    # time-sorted timeline = current narrative (all of it) + the latest
    # cross-narrative messages, merged by time and capped. Cross-narrative is
    # pure recency (no per-instance fairness cap — Owner's call: "只看时间顺序最新的").
    # Each message is tagged with its narrative_id + alias so the agent can tell
    # threads apart and (P3) re-route via tools. The narrative summary + the
    # view_narrative tool cover whatever the cap drops.
    SHORT_TERM_MAX_MESSAGES = 30     # latest cross-narrative rows by time (candidates)
    MERGED_HISTORY_MAX = 30          # final unified-timeline cap (current + cross, by time)

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

        # Narrative-level memory persistence. Depends on the repository
        # layer directly (modules → repository is the allowed direction);
        # importing a sibling Module would violate iron rule #3.
        if if_use_event_memory:
            self.event_memory_module = EventMemoryRepository(agent_id, user_id, database_client)
        else:
            self.event_memory_module = None

        self.instructions = CHAT_MODULE_INSTRUCTIONS
        self.instance_ids = instance_ids    # TODO: Improve this capability in the future


    @staticmethod
    def get_config() -> ModuleConfig:
        """
        Return ChatModule configuration
        """
        return ModuleConfig(
            name="ChatModule",
            role="chat",
            base=True,
            default=True,
            instance_prefix="chat",
            display=ModuleDisplay(icon="💬", name="Chat", desc="Response generation"),
            decision=ModuleDecisionMeta(
                capabilities=["Receive and process user messages", "Maintain conversation history and context", "Send async notifications to users via Inbox"],
                use_cases=["Daily conversations and Q&A", "Main entry point for user interaction", "Message notifications and reminders"],
                instance_type="persistent",
                typical_instance_id="chat_{uuid8}",
            ),
            priority=1,  # High priority (base module)
            enabled=True,
            description="Provides messaging capabilities (chat conversation + history retrieval)"
        )

    @classmethod
    def provides_chat_history(cls) -> bool:
        """ChatModule is the per-user carrier of chat history. The pipeline
        uses this capability flag instead of naming ChatModule directly."""
        return True

    # ============================================================================= MCP Server

    async def mcp_server(self) -> Optional[MCPServerConfig]:
        """
        Return MCP Server configuration

        ChatModule provides MCP Server for:
        - reply_owner / notify_owner: Agent speaks to its owner
        - get_chat_history: Retrieve past conversations

        Returns:
            MCPServerConfig
        """
        return MCPServerConfig(
            server_name="chat_module",
            server_url=mcp_server_url("chat_module"),
            type="sse"
        )

    def claims_source(self, working_source: Any) -> bool:
        """Owner web chat originates CHAT turns (and is the origin-first
        default on turns that fall through with no declared origin owner,
        since priority 1 already sorts it first within its rank)."""
        return working_source_matches(working_source, WorkingSource.CHAT.value)

    async def expressive_tools(self, ctx_data: Any = None) -> list[str]:
        """The owner-facing tool THIS turn can actually deliver through.

        Exactly one of the two, never both — which is the whole reason the old
        `send_message_to_user_directly` was split:

        * an owner-chat turn gets ``reply_owner``. ``notify_owner`` would be a
          second name for the same destination and a choice with no meaning.
        * every other turn gets ``notify_owner``. The owner is not part of that
          conversation, and the "default is not to use this" discipline that
          belongs to it is then the only owner-facing rule on the desk.

        Paired with ``disallowed_tools`` below: declaring one while both
        schemas stay in context is how a rule ends up arguing with a tool the
        model can still see. 615 calls to two tools documented "Do NOT call"
        (prod, 2026-08-17) is what that argument is worth.
        """
        # A plain-text (patrol) turn delivers by SPEAKING — the platform posts
        # the composed line under the room marker. Declaring a reply tool here is
        # what made both frameworks' reply reminders name `notify_owner` on a turn
        # whose prompt says "write plain text, do NOT call a tool", pushing the
        # lead to DM the owner instead of writing the room's status line (or to
        # fall silent on the contradiction). Withdraw the DECLARATION only; the
        # schema stays on the desk (escalating to the owner mid-sweep is
        # legitimate — disallowed_tools is unchanged).
        if is_plain_text_turn(ctx_data):
            return []
        config = await self.mcp_server()
        name = "reply_owner" if self._is_owner_chat_turn(ctx_data) else "notify_owner"
        return [f"mcp__{config.server_name}__{name}"]

    async def disallowed_tools(self, ctx_data: Any = None) -> list[str]:
        """Take the owner tool that does NOT apply this turn off the desk.

        The declaration above only decides what the reply REMINDER names. The
        schemas reach the model separately, so without this the agent still sees
        both and has to pick — and the disciplines attached to the two are
        opposites, so picking wrong is not free.

        Reads the turn from its own ``ctx_data``, not from state the
        declaration left behind: the runtime calls THIS hook first.
        """
        config = await self.mcp_server()
        drop = "notify_owner" if self._is_owner_chat_turn(ctx_data) else "reply_owner"
        return [f"mcp__{config.server_name}__{drop}"]

    def _is_owner_chat_turn(self, ctx_data: Any) -> bool:
        """Is the owner the one who started this turn?

        Both hooks that ask this question are handed the turn's own ctx_data,
        so they cannot disagree about which turn it is — a mismatch would put
        both tools on the desk, or neither.
        """
        source = getattr(ctx_data, "working_source", None)
        if not source:
            # No declared origin — ChatModule's own fall-through rule (see
            # `claims_source`) says that is the owner's desk. It is also
            # the safer of the two wrong answers: guessing `notify_owner` on a
            # real chat turn hands the agent a tool whose documented discipline
            # is "default is not to use this", and the owner gets silence for
            # something they just said. Guessing `reply_owner` on a non-chat
            # turn only misses a register.
            return True
        return working_source_matches(source, WorkingSource.CHAT.value)

    def create_mcp_server(self) -> Optional[Any]:
        """
        Create MCP Server

        Delegates tool registration to _chat_mcp_tools module.
        """
        from narranexus_plugins.chat_module._chat_mcp_tools import create_chat_mcp_server
        return create_chat_mcp_server()


    # ============================================================================= Private Helper Methods

    async def _get_or_create_mcp_url(self) -> str:
        """
        Get or create MCP Server URL

        Returns:
            MCP Server URL
        """
        return mcp_server_url("chat_module")
    
    
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
        notify_owner; Lark also accepts lark_cli
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
        # Same marker persist_turn writes; the frontend's copy lives in
        # frontend/src/lib/turnMarkers.ts (NO_RESPONSE_MARKER).
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
          - the owner-facing tool (``notify_owner`` on an IM turn), which
            surfaces in the owner's chat panel for the "this is important,
            the owner should know about it" carve-out spelled out in the
            iron rules.

        Both currently get joined into one ``assistant_content`` string,
        which means downstream consumers can't tell them apart. The
        chat-history endpoint needs the split so it can render the
        direct-notify text in full while replacing the routine IM reply
        with a "Background activity" placeholder.

        Returns ``(im_reply, direct_notify, combined)``:
          - ``im_reply``: parts that came from non-direct platform tools.
          - ``direct_notify``: parts from the owner-facing tool
            (``reply_owner`` / ``notify_owner``).
          - ``combined``: the original "\\n\\n"-joined string, preserved
            for callers (long-term memory write, log lines) that want
            the full picture.

        For working_source="chat", direct_notify holds everything (the
        default handler matches only the two owner-facing names) and im_reply
        is empty.
        """
        from narranexus.platform.schema import ProgressMessage
        from narranexus.platform.channel.message_source_handler import (
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
            # Owner-visible only: this split decides what lands in the
            # owner's chat history. A bus reply to a peer agent delivered
            # fine, but persisting it here would surface an agent-to-agent
            # exchange in the owner's view — those turns fall through to
            # the background activity row instead.
            reply = handler.extract_owner_visible_text(tool_name, arguments)
            if not reply:
                continue
            # The owner-facing path, whichever of its two registers the turn
            # put on the desk. `reply_owner` (owner chat) and `notify_owner`
            # (every other surface) are one delivery split in two so the agent
            # knows which voice it is speaking in — they are the SAME
            # destination, so classification must accept both. Matching only
            # one sends every owner reply on the other surface into the IM
            # bucket, where the chat panel renders it as "Background activity".
            if is_owner_tool(tool_name):
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
                f"handler={handler.name} no owner-visible reply tool matched "
                f"(checked patterns={handler.effective_owner_visible_names})"
            )
        return im_reply, direct_notify, combined

    @staticmethod
    def _origin_delivered_text(working_source: str, agent_loop_response: list) -> str:
        """What this turn actually said to whoever contacted it, or "".

        The origin-delivery question: same extractor, same full
        `user_reply_tool_names` list, but it returns the TEXT rather than a
        verdict, because a turn that replied has to be able to record what it
        replied WITH.

        Deliberately the origin extractor and not the owner-visible one. A team
        room's reply reaches the room, not the owner's chat panel, so the
        owner-visible gate says None for it — correctly, and that gate must keep
        saying None or every team reply would re-anchor the owner's session.
        "Did the owner see it" and "is this a real thing the agent said" are
        different questions, and only the second one decides whether the agent
        can remember it next turn.
        """
        from narranexus.platform.schema import ProgressMessage
        from narranexus.platform.channel.message_source_handler import (
            MessageSourceRegistry,
        )

        try:
            handler = MessageSourceRegistry.get(working_source)
            parts: list[str] = []
            for response in agent_loop_response or []:
                if not (isinstance(response, ProgressMessage) and response.details):
                    continue
                text = handler.extract_reply_text(
                    response.details.get("tool_name", ""),
                    response.details.get("arguments", {}) or {},
                )
                if text and text.strip():
                    parts.append(text.strip())
            return "\n\n".join(parts)
        except Exception as e:  # noqa: BLE001 — never turn-fatal
            logger.warning(f"_origin_delivered_text extraction failed: {e}")
            return ""

    @staticmethod
    def _build_activity_summary(
        working_source: str, meta: dict
    ) -> str:
        """
        Build a human-readable activity summary for background turns that
        produced nothing owner-visible.

        Args:
            working_source: Execution source ("job", "message_bus", etc.)
            meta: Shared meta_data dict (may contain channel_tag)

        Returns:
            Short activity description string
        """
        # Say WHAT happened, not just the source — the UI already badges the
        # source (working_source) with its own colour + name, so repeating
        # "(wechat)" here is noise. Use the channel_tag the IM triggers attach
        # (sender / room) to make the one-line summary actually informative.
        tag = meta.get("channel_tag") or {}
        who = tag.get("sender_name") or tag.get("room_name")

        if working_source == "job":
            return "Ran a scheduled job"
        if working_source in BUS_PRODUCED_SOURCES:
            # No "Replied to X" arm: a turn that delivered anything recovers its
            # text upstream and is written as a real assistant row, so it never
            # reaches this summary. Keeping a branch that cannot run would leave
            # the next reader thinking activity rows still classify delivery.
            return (
                f"Read messages from {who} (no reply sent)"
                if who
                else "Handled a peer-agent message (no reply sent)"
            )
        if who:
            return f"Handled a message from {who}"
        return "Handled a background activity"

    async def gather(self, ctx_data: ContextData) -> ContextData:
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
            logger.debug(f"ChatModule.gather: Long-term memory Instance IDs: {len(current_instance_ids)}")
        elif self.instance_id:
            current_instance_ids = [self.instance_id]
            logger.debug(f"ChatModule.gather: Long-term memory single Instance ID: {self.instance_id}")

        if not current_instance_ids:
            logger.debug("ChatModule.gather: No instance_id, skipping history retrieval")
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
                        # Tag with the current narrative (for the unified-timeline
                        # [time · alias · nar_id] prefix). Long-term = the current
                        # conversation thread the user is looking at.
                        msg["meta_data"]["narrative_id"] = getattr(ctx_data, "narrative_id", None)

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

        # 2026-05-20: load the CURRENT narrative in full (no per-track cap).
        # The unified timeline (current + cross) is capped once, by time, at
        # MERGED_HISTORY_MAX below — so the current thread isn't pre-truncated
        # before it competes with cross-narrative recency.
        logger.info(f"[ChatHistory] long-term (current narrative) loaded: {len(long_term_messages)} messages")

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

        # ========== 3. Merge into ONE time-sorted timeline, cap, tag aliases ==========
        # Current narrative (long-term) + cross-narrative (short-term) become a
        # single timeline ordered by absolute time. The agent sees every line
        # tagged [time · alias · nar_id] and can tell threads apart / re-route.
        all_messages = long_term_messages + short_term_messages

        if all_messages:
            def get_timestamp(msg):
                meta = msg.get("meta_data", {})
                timestamp = meta.get("timestamp", "")
                return timestamp if timestamp else "0000-00-00T00:00:00"

            all_messages.sort(key=get_timestamp)  # oldest -> newest

            # Cap the unified timeline at the latest MERGED_HISTORY_MAX by time.
            # Whatever falls off is reachable via the view_narrative tool (P3).
            pre_cap = len(all_messages)
            if pre_cap > self.MERGED_HISTORY_MAX:
                all_messages = all_messages[-self.MERGED_HISTORY_MAX:]

            # Resolve narrative_id -> human alias (name) for the tags.
            await self._tag_narrative_aliases(all_messages)

            # Strong logging (Owner req): assembly must be verifiable from logs.
            from collections import Counter
            by_nar = Counter(
                (m.get("meta_data") or {}).get("narrative_alias")
                or (m.get("meta_data") or {}).get("narrative_id")
                or "unknown"
                for m in all_messages
            )
            logger.info(
                f"[ChatHistory] unified timeline assembled: "
                f"long-term(current)={len(long_term_messages)}, "
                f"short-term(cross)={len(short_term_messages)}, "
                f"merged={pre_cap} -> kept latest {len(all_messages)} (cap={self.MERGED_HISTORY_MAX})"
            )
            logger.info(
                "[ChatHistory] per-narrative in timeline: "
                + ", ".join(f"{name}×{cnt}" for name, cnt in by_nar.most_common())
            )
            # Debug aid (Owner req): record WHICH events are in context so we can
            # reconstruct/inspect later — ids only, not raw text. Each message's
            # event_id is also surfaced to the agent in its timeline tag so it
            # can drill in via view_event().
            evt_ids = [(m.get("meta_data") or {}).get("event_id") for m in all_messages]
            logger.info(f"[ChatHistory] timeline event_ids ({len(evt_ids)}): {evt_ids}")
        else:
            logger.debug("ChatModule.gather: No history messages retrieved")

        # Splice persisted reasoning (see after_turn) back
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

        # P2: recent background-activity records (the centered small-text items
        # in the UI) — surfaced as a separate compact list, NOT mixed into the
        # conversation timeline. context_runtime renders them with event_ids so
        # the agent can drill into any via view_event().
        try:
            recent_actions = await self._load_recent_actions()
            if getattr(ctx_data, "extra_data", None) is None:
                ctx_data.extra_data = {}
            ctx_data.extra_data["recent_actions"] = recent_actions
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ChatModule.gather: recent-actions load failed: {e}")

        # Fill merged history messages into ctx_data
        ctx_data.chat_history = all_messages
        return ctx_data

    RECENT_ACTIONS_MAX = 10  # latest background activity records to surface

    async def _load_recent_actions(self) -> List[Dict[str, Any]]:
        """Collect the latest background 'activity' records (Fix #2 P2).

        These are the centered small-text items in the chat box: turns where the
        agent did background work WITHOUT replying to the user (job runs, IM
        activations, bus pings). They are intentionally kept OUT of the chat
        timeline (filtered as message_type='activity' in the loaders); here we
        surface the latest N as a compact list so the agent knows what happened.
        Each carries its event_id for view_event() drill-down; job rows get a
        best-effort job title from the event's env_context.

        Returns: list of {timestamp, working_source, summary, event_id,
        narrative_id, title?} sorted oldest -> newest.
        """
        from narranexus.platform.utils.db.db_factory import get_db_client
        from narranexus.platform.repository import InstanceRepository

        db = await get_db_client()
        repo = InstanceRepository(db)
        instances = await repo.get_chat_instances_by_user(
            agent_id=self.agent_id, user_id=self.user_id, exclude_instance_ids=[]
        )
        nar_by_instance = await self._resolve_instance_narratives(
            [i.instance_id for i in instances]
        )
        actions: List[Dict[str, Any]] = []
        for inst in instances:
            memory = await self.event_memory_module.search_instance_json_format_memory(
                self.config.name, inst.instance_id
            )
            if not memory or "messages" not in memory:
                continue
            nid = nar_by_instance.get(inst.instance_id)
            for m in memory.get("messages", []):
                meta = m.get("meta_data", {})
                if meta.get("message_type") != "activity":
                    continue
                actions.append({
                    "timestamp": meta.get("timestamp", ""),
                    "working_source": meta.get("working_source", "?"),
                    "summary": (m.get("content") or "").strip()[:200],
                    "event_id": meta.get("event_id"),
                    "narrative_id": nid,
                })

        actions.sort(key=lambda a: a.get("timestamp", ""), reverse=True)
        actions = actions[:self.RECENT_ACTIONS_MAX]
        actions.reverse()  # chronological for display

        # Best-effort job-title enrichment from each job event's env_context.
        job_eids = [a["event_id"] for a in actions if a["working_source"] == "job" and a["event_id"]]
        if job_eids:
            try:
                import re
                import json as _json
                rows = await db.get_by_ids("events", "event_id", job_eids)
                title_by_eid: Dict[str, str] = {}
                for r in rows or []:
                    if not r:
                        continue
                    ec = r.get("env_context")
                    txt = ""
                    if isinstance(ec, str):
                        try:
                            txt = (_json.loads(ec) or {}).get("input", "") or ec
                        except Exception:
                            txt = ec
                    m = re.search(r"Title\**\s*[:：]\s*\**\s*([^\n*]+)", txt)
                    if m and r.get("event_id"):
                        title_by_eid[r["event_id"]] = m.group(1).strip()
                for a in actions:
                    if a["event_id"] in title_by_eid:
                        a["title"] = title_by_eid[a["event_id"]]
            except Exception as e:  # noqa: BLE001
                logger.warning(f"_load_recent_actions: job-title enrich failed ({e})")

        logger.info(
            f"[RecentActions] loaded {len(actions)} activity records "
            f"(sources={[a['working_source'] for a in actions]}, "
            f"event_ids={[a['event_id'] for a in actions]})"
        )
        return actions

    async def _tag_narrative_aliases(self, messages: List[Dict[str, Any]]) -> None:
        """Stamp meta_data['narrative_alias'] (the narrative name) onto each
        message, resolved in one batch from its narrative_id. Powers the
        [time · alias · nar_id] tag the agent sees in the unified timeline.
        Best-effort: on a miss the renderer falls back to the raw id.
        """
        nar_ids = {
            (m.get("meta_data") or {}).get("narrative_id") for m in messages
        }
        nar_ids.discard(None)
        if not nar_ids:
            return
        alias_by_id: Dict[str, str] = {}
        try:
            from narranexus.platform.utils.db.db_factory import get_db_client
            import json as _json
            db = await get_db_client()
            rows = await db.get_by_ids("narratives", "narrative_id", list(nar_ids))
            for row in rows or []:
                if not row:
                    continue
                nid = row.get("narrative_id")
                info = row.get("narrative_info")
                name = None
                if isinstance(info, str):
                    try:
                        name = (_json.loads(info) or {}).get("name")
                    except Exception:
                        name = None
                elif isinstance(info, dict):
                    name = info.get("name")
                if nid and name:
                    alias_by_id[nid] = name
        except Exception as e:  # noqa: BLE001
            logger.warning(f"_tag_narrative_aliases: resolve failed ({e}); tags fall back to id")
        for m in messages:
            meta = m.get("meta_data") or {}
            nid = meta.get("narrative_id")
            if nid:
                meta["narrative_alias"] = alias_by_id.get(nid)
                m["meta_data"] = meta
        logger.info(f"[ChatHistory] resolved {len(alias_by_id)}/{len(nar_ids)} narrative aliases for timeline tags")

    async def _resolve_instance_narratives(
        self, instance_ids: List[str]
    ) -> Dict[str, Optional[str]]:
        """
        Map each chat instance_id -> its linked narrative_id.

        InstanceRepository.get_chat_instances_by_user returns base
        ModuleInstanceRecord objects, which do NOT carry linked_narrative_ids:
        that field lives only on the ModuleInstance runtime subclass and is not
        populated by the SELECT. So the narrative each cross-narrative chat
        instance belongs to must be resolved from instance_narrative_links here,
        not read off the record attribute.
        """
        from narranexus.platform.utils.db.db_factory import get_db_client
        from narranexus.platform.repository.instance_link_repository import (
            InstanceNarrativeLinkRepository,
        )

        db = await get_db_client()
        link_repo = InstanceNarrativeLinkRepository(db)
        result: Dict[str, Optional[str]] = {}
        for iid in instance_ids:
            nids = await link_repo.get_narratives_for_instance(iid)
            result[iid] = nids[0] if nids else None
        return result

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
        from narranexus.platform.utils.db.db_factory import get_db_client
        from narranexus.platform.repository import InstanceRepository

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

        nar_by_instance = await self._resolve_instance_narratives(
            [i.instance_id for i in other_instances]
        )

        # Pure recency (2026-05-20): flatten every other narrative's messages,
        # then take the latest SHORT_TERM_MAX_MESSAGES by timestamp. No
        # per-instance fairness cap — the agent sees the genuinely most recent
        # cross-narrative activity, tagged by narrative, and can drill into any
        # thread via the view_narrative tool.
        short_term_messages: List[Dict[str, Any]] = []

        for instance in other_instances:
            memory = await self.event_memory_module.search_instance_json_format_memory(
                module_name, instance.instance_id
            )
            if not memory or "messages" not in memory:
                continue

            messages = memory.get("messages", [])
            # The narrative this cross-narrative instance belongs to (for the
            # [time · alias · nar_id] tag the agent sees in the unified timeline).
            # Resolved from instance_narrative_links (NOT off the record — the
            # base ModuleInstanceRecord has no linked_narrative_ids attribute).
            inst_nar_id = nar_by_instance.get(instance.instance_id)

            # Filter + tag this instance's messages.
            keepers: List[Dict[str, Any]] = []
            for msg in messages:
                meta = msg.get("meta_data", {})

                # Same activity-row filter as long_term — see
                # gather for the why.
                if meta.get("message_type") == "activity":
                    continue

                # Messages from non-chat sources (job/a2a): only load assistant side
                working_source = meta.get("working_source", "chat")
                if working_source != "chat" and msg.get("role") != "assistant":
                    continue

                # Mark as short-term memory + tag its narrative.
                if "meta_data" not in msg:
                    msg["meta_data"] = {}
                msg["meta_data"]["instance_id"] = instance.instance_id
                msg["meta_data"]["memory_type"] = "short_term"
                msg["meta_data"]["narrative_id"] = inst_nar_id

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

            short_term_messages.extend(keepers)

        # Global cap + final chronological ordering: latest N by time.
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

    async def _resolve_bootstrap_greeting(self) -> str:
        """
        Per-agent bootstrap greeting override.

        Reads `agents.agent_metadata.bootstrap_greeting` (set by scenario
        provisioners such as the Arena onboarding flow) and falls back to the
        generic `BOOTSTRAP_GREETING` constant. Keeps the generic constant
        scenario-free (铁律 #4); only runs on the first turn (rare).
        """
        try:
            from narranexus.platform.utils.db.db_factory import get_db_client
            from narranexus.platform.repository.agent_repository import AgentRepository

            db = await get_db_client()
            agent = await AgentRepository(db).get_agent(self.agent_id)
            if agent and agent.agent_metadata:
                custom = agent.agent_metadata.get("bootstrap_greeting")
                if custom:
                    return custom
        except Exception as e:  # noqa: BLE001 — greeting override is best-effort
            logger.warning(f"ChatModule: bootstrap greeting override failed: {e}")
        return BOOTSTRAP_GREETING

    async def persist_turn(self, params: HookAfterExecutionParams) -> None:
        """
        Synchronous, next-turn-critical persistence: write THIS turn's
        conversation row (user message + assistant reply) to the instance's
        JSON-format chat memory, keyed by instance_id.

        Runs in-request (before the WS closes / before background hooks fire), so
        a user who fires a reply the instant they see the answer cannot race the
        write — the next turn's gather is guaranteed to see this
        exchange. (This is the fix for the short-reply "amnesia": previously the
        write lived in the backgrounded after_turn, which could
        lag seconds-to-tens-of-seconds.)
        it is deferred to the background after_turn below.

        Note: assistant messages store the content parameter from the
        notify_owner tool call, not final_output (the Agent's
        thinking result). This ensures chat history displays the Agent's actual
        reply to the user, not the internal thinking process.

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
                f"ChatModule.after_turn: Missing necessary information, skipping "
                f"(instance_id={instance_id}, event_memory_module={self.event_memory_module is not None})"
            )
            return

        # ========== 1. Update conversation history (Instance-based JSON Format Memory) ==========
        # Get existing history (using instance_id)
        existing_memory = await self.event_memory_module.search_instance_json_format_memory(module_name, instance_id)
        messages = existing_memory.get("messages", []) if existing_memory else []

        # ── Silent batch write path ──────────────────────────────────────
        # AgentRuntime.run(silent=True) skipped step_3, so there is no
        # assistant reply to persist. The trigger passed per-message metadata
        # via trigger_extra_data["batch_messages"] (list of
        # {event_id, timestamp, sender_id, content, attachments?}) so we can
        # write one user row per original event with its own timestamp /
        # sender_id, rather than collapsing them into a single row keyed by
        # the merged input_content.
        # Used by IM channels (Matrix / Lark / Slack) for group non-@
        # messages and reconnect burst backfill — see
        # channel_trigger_base._build_and_run_agent_silent_batch.
        batch_messages_raw: Any = None
        if params.ctx_data and params.ctx_data.extra_data:
            batch_messages_raw = params.ctx_data.extra_data.get("batch_messages")
        if isinstance(batch_messages_raw, list) and batch_messages_raw:
            batch_working_source = (
                params.execution_ctx.working_source.value
                if params.execution_ctx
                else "unknown"
            )
            channel_tag_data = None
            if params.ctx_data and params.ctx_data.extra_data:
                channel_tag_data = params.ctx_data.extra_data.get("channel_tag")
                if channel_tag_data is not None and hasattr(channel_tag_data, "to_dict"):
                    channel_tag_data = channel_tag_data.to_dict()
            appended = 0
            for item in batch_messages_raw:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content") or "")
                if not content.strip() and not item.get("attachments"):
                    continue
                meta: Dict[str, Any] = {
                    "event_id": item.get("event_id") or params.event_id,
                    "timestamp": item.get("timestamp") or utc_now().isoformat(),
                    "instance_id": instance_id,
                    "working_source": batch_working_source,
                    "silent": True,
                }
                if item.get("sender_id"):
                    meta["sender_id"] = item["sender_id"]
                if item.get("sender_name"):
                    meta["sender_name"] = item["sender_name"]
                if channel_tag_data:
                    meta["channel_tag"] = channel_tag_data
                row: Dict[str, Any] = {
                    "role": "user",
                    "content": content,
                    "meta_data": meta,
                }
                attachments_raw = item.get("attachments")
                if isinstance(attachments_raw, list) and attachments_raw:
                    row["attachments"] = [a for a in attachments_raw if isinstance(a, dict)]
                messages.append(row)
                appended += 1
            memory = {
                "messages": messages,
                "last_event_id": params.event_id,
                "updated_at": utc_now().isoformat(),
            }
            await self.event_memory_module.add_instance_json_format_memory(
                module_name, instance_id, memory
            )
            logger.info(
                f"ChatModule.persist_turn: silent batch wrote {appended} "
                f"user rows (no assistant row) to instance_id={instance_id}"
            )
            return

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
            # First-contact scope is per-(agent, user), NOT per-instance
            # (Shenzhen-r2 B2): every new narrative brings a fresh empty
            # instance, and prepending here on each one re-greeted mid-
            # conversation — the extra assistant row rendered as a second
            # reply to the user's question. Sibling history = already
            # greeted; skip.
            from narranexus_plugins.chat_module._chat_writes import (
                agent_chat_has_history,
                build_bootstrap_greeting_row,
            )
            already_greeted = False
            if self.db is not None:
                try:
                    already_greeted = await agent_chat_has_history(
                        self.db, self.agent_id, self.user_id
                    )
                except Exception as e:  # noqa: BLE001 — best-effort; worst case is a re-greet
                    logger.warning(f"ChatModule: sibling-history check failed: {e}")
            if not already_greeted:
                greeting = await self._resolve_bootstrap_greeting()
                base_dt = (
                    params.event.created_at
                    if params.event is not None and params.event.created_at is not None
                    else utc_now()
                )
                # Shared row builder (chat_module owns the shape + timestamp
                # rule) so this lazy prepend and the step_1 provision-time
                # seed stay identical. NO event_id: the greeting belongs to no
                # run — stamping the current run's id made it share the
                # (role, event_id) identity the frontend timeline dedups on
                # with the turn's REAL reply (the other half of B2).
                messages.append(
                    build_bootstrap_greeting_row(greeting, base_dt, instance_id)
                )
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
        # it on assistant meta_data so gather can splice it
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
        # Interrupt continuity (2026-07-30): a user-stopped turn persists
        # its partial work; the placeholder must say "cut short by the
        # user", never "chose not to answer" — the next turn's model reads
        # this row and the two mean opposite things.
        turn_interrupted = bool(getattr(params.io_data, "interrupted", False))
        # Strip-for-emptiness: a whitespace-only reply ("\n" left over
        # from citation-token stripping, or literal spaces) is truthy but
        # renders as a blank bubble — same placeholder as no reply at all.
        # Mirrors the silent-batch branch above. NOTE: this is the SECOND
        # net — the first is extract_reply_text's blank guard, which
        # already keeps whitespace parts out of the split's join. Kept as
        # cross-layer defense in case a future extractor path regresses.
        # Both marker strings are a cross-repo contract: the frontend keeps a
        # byte-identical copy in frontend/src/lib/turnMarkers.ts, whose
        # localizeTurnMarker maps them to localized labels by exact match. A
        # wording change here must be made there too, or the UI silently
        # falls back to showing the raw English marker.
        if not assistant_content.strip():
            assistant_content = (
                "(Interrupted by user)"
                if turn_interrupted
                else "(Agent decided no response needed)"
            )
        is_no_response = assistant_content == "(Agent decided no response needed)"

        # A turn that DID reply, just not somewhere the owner can see, is not a
        # turn with no response. The owner-visible split says nothing here on
        # purpose: a team room's reply lands in the room, and the gate that
        # keeps it out of `assistant_content` is the same gate that stops every
        # team reply from re-anchoring the owner's chat session.
        #
        # But "the owner did not see it" was being read as "nothing was said",
        # so the row became an `activity` row and both history loaders dropped
        # it — the agent came back to a room it had no memory of speaking in.
        # Recording delivery without recording WHAT was delivered fixed the
        # metric and left the amnesia in place.
        # `turn_interrupted` cannot be true here — an interrupted turn's fallback
        # text is "(Interrupted by user)", which is not the no-response marker.
        if is_no_response:
            delivered_text = self._origin_delivered_text(
                working_source, params.agent_loop_response
            )
            if delivered_text:
                assistant_content = delivered_text
                is_no_response = False
                assistant_meta["delivered_to_origin"] = True
        if turn_interrupted:
            assistant_meta["interrupted"] = True

        # NOTE (2026-05-12): the previous "use io_data.final_output as
        # reply" fallback was removed — it violated the thinking-vs-
        # speaking design (final_output is the agent's internal reasoning,
        # not a user-facing reply). The real no-reply recovery now lives
        # one layer up: step_3_agent_loop detects a chat turn that ended
        # without notify_owner and asks the helper_llm
        # to generate a real reply, streamed to the frontend and emitted
        # as a synthetic send_message ProgressMessage. By the time we get
        # here, _extract_user_visible_response will have already picked
        # that up; if it didn't, the helper_llm fallback failed too and
        # persisting a placeholder is the honest record.

        # Attachments forwarded by the trigger (WebSocket / Lark / etc.)
        # land in ctx_data.extra_data via context_runtime's
        # trigger_extra_data merge. We persist them on the user message
        # row so that gather can synthesize markers when this
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
            # (see step_3_agent_loop._stream_fallback_recovery), it
            # emits a synthetic notify_owner
            # ProgressMessage tagged details.reply_via="helper_llm_*"
            # (either helper_llm_no_reply or helper_llm_after_error).
            # Surface that tag on the persisted row so observability
            # tooling can tell organic vs. recovered replies apart, and
            # so the UI can distinguish the two recovery modes (no_reply
            # = info badge, after_error = warning badge).
            for r in (params.agent_loop_response or []):
                d = getattr(r, "details", None)
                if not isinstance(d, dict):
                    continue
                tag = d.get("reply_via")
                if isinstance(tag, str) and tag.startswith("helper_llm_"):
                    asst_meta["reply_via"] = tag
                    break
            # Stash the owner-notify portion on meta_data when the turn
            # was IM-triggered AND the agent explicitly called
            # notify_owner. The chat-history endpoint
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
                    f"placeholder_reply=True — no non-blank owner-visible "
                    f"reply, persisting placeholder. Either no reply tool "
                    f"fired (cancellation / zero LLM output) or the reply "
                    f"text stripped down to blank."
                )
        else:
            # Background task (job/lark/message_bus) with nothing owner-visible:
            # store a lightweight activity record instead of a fake conversation
            # pair. Two honest sub-cases — "delivered to the origin but not
            # owner-visible" (a bus reply to a peer agent) vs "genuinely
            # silent". The distinction IS the no-reply metric: counting
            # delivered bus turns as NO-REPLY is what poisoned the 8/1
            # numbers and would poison the fallback decision built on them.
            # Reaching here means the turn said nothing to ANYONE: a turn that
            # delivered to its origin had its text recovered further up and left
            # `is_no_response` False, so it never arrives in this branch. There
            # used to be a `delivered_to_origin` split here with its own
            # [DELIVERED-BG] log; once the recovery above existed that branch
            # became unreachable, and a log line that can never print is worse
            # than none — anyone watching it would read "no bus turn ever
            # delivers".
            activity_summary = self._build_activity_summary(
                working_source, shared_meta
            )
            logger.info(
                f"[NO-REPLY-BG] event_id={params.event_id} working_source={working_source} "
                f"writing activity row (background trigger, no reply of any kind)"
            )
            messages.append({
                "role": "assistant",
                "content": activity_summary,
                "meta_data": {
                    **assistant_meta,
                    "message_type": "activity",
                    "delivered_to_origin": False,
                },
            })

        # Save updated history (using instance_id)
        memory = {
            "messages": messages,
            "last_event_id": params.event_id,
            "updated_at": utc_now().isoformat()
        }
        await self.event_memory_module.add_instance_json_format_memory(module_name, instance_id, memory)

        # (chat is no longer mirrored into memory_chat — the per-interaction
        # search index is built once in step_4 as kind="event"; design §5.)

        logger.debug(
            f"ChatModule.after_turn: Conversation record saved successfully, "
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
