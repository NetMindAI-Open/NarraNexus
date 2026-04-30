"""
@file_name: message_bus_module.py
@author: NarraNexus
@date: 2026-04-02
@description: MessageBusModule - Agent-to-agent communication via MessageBus

Protocol-agnostic message bus for agent-to-agent communication. Provides MCP
tools for sending/receiving messages, managing channels, and discovering agents.

Instance level: Agent-level (one per Agent, is_public=True).

Behavior design:
- Reply Discipline: prevent infinite trigger loops between agents
- Selective mark_read: messages the agent ignores stay unread (resurface next turn)
- Context caps: unread/channels/known_agents all bounded to prevent pollution
- Source recognition: incoming bus messages are prefixed with [MessageBus · ...]
  so the agent can distinguish them from owner commands
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule, mcp_host
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
    WorkingSource,
)


# MCP server port for MessageBus tools
MESSAGE_BUS_MCP_PORT = 7820

# Context-injection caps to prevent pollution
MAX_UNREAD_IN_CONTEXT = 20
MAX_CHANNELS_IN_CONTEXT = 20
MAX_KNOWN_AGENTS_IN_CONTEXT = 50


class MessageBusModule(XYZBaseModule):
    """
    MessageBus communication module.

    Enables Agents to communicate with each other via the MessageBus service.
    Provides MCP tools for messaging, channel management, and agent discovery.

    Instance level: Agent-level (one per Agent, is_public=True).
    """

    # =========================================================================
    # Configuration
    # =========================================================================

    def get_config(self) -> ModuleConfig:
        return ModuleConfig(
            name="MessageBusModule",
            priority=5,
            enabled=True,
            description=(
                "Agent-to-agent communication via message bus. "
                "Provides tools for sending/receiving messages, managing channels, "
                "and discovering other agents."
            ),
            module_type="capability",
        )

    # =========================================================================
    # MCP Server
    # =========================================================================

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(
            server_name="message_bus_module",
            server_url=f"http://{mcp_host()}:{MESSAGE_BUS_MCP_PORT}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        try:
            # Use the official mcp SDK's FastMCP (same as every other module)
            # so FASTMCP_HOST, TransportSecuritySettings, and the shared
            # module_runner._run_mcp_in_thread configuration all apply
            # uniformly. The standalone `fastmcp` v2 package has a different
            # API and does not honour those settings, which caused this MCP
            # to silently fail cross-container reachability.
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP("message_bus_module")
            mcp.settings.port = MESSAGE_BUS_MCP_PORT

            from ._message_bus_mcp_tools import register_message_bus_mcp_tools
            register_message_bus_mcp_tools(mcp, get_message_bus_fn=_get_default_bus_async)

            logger.info(f"MessageBusModule MCP server created on port {MESSAGE_BUS_MCP_PORT}")
            return mcp
        except Exception as e:
            logger.exception(f"Failed to create MessageBusModule MCP server: {e}")
            return None

    # =========================================================================
    # Instructions — natural language guidance for the agent
    # =========================================================================

    async def get_instructions(self, ctx_data: ContextData) -> str:
        parts = [
            "## MessageBus — Agent-to-Agent Communication",
            "",
            "MessageBus is your **inter-agent messaging channel**. Use it to collaborate with other Agents, "
            "exchange information, coordinate tasks, or reach out to contacts you cannot talk to directly.",
            "",
            f"Your agent ID: `{self.agent_id}`",
            "",
            "### When to Use MessageBus",
            "- You need to **contact another Agent** (ask a question, share information, coordinate work)",
            "- Your owner asks you to **send a message** to another agent",
            "- You want to **proactively reach out** based on your current task (e.g., gather intel, request help)",
            "- Use `bus_search_agents` to discover agents you haven't talked to yet",
            "",
            "### Available Tools",
            "- **bus_send_to_agent**: Direct-message another agent by `agent_id` (auto-creates a DM channel)",
            "- **bus_send_message**: Send a message to an existing channel (supports @mentions)",
            "- **bus_create_channel**: Create a new channel and invite members",
            "- **bus_get_messages**: Read recent history of a channel",
            "- **bus_search_agents**: Discover agents by capability or description",
            "- **bus_get_channel_members**: List channel members",
            "- **bus_get_agent_profile**: View another agent's profile",
            "- **bus_leave_channel**: Leave a channel you no longer need",
            "- **bus_kick_member**: Remove another agent from a channel (creator only)",
            "",
            "### DM (1-on-1) Workflow",
            "1. Call `bus_send_to_agent(to_agent_id='agent_xxx', content='...')`",
            "2. A direct channel is auto-created on first use; subsequent messages reuse it",
            "",
            "### Group Chat Workflow",
            "1. Call `bus_create_channel(name='Project Alpha Coordination', members='agent_a,agent_b')`",
            "2. Use `bus_send_message(channel_id=..., content=..., mention_list=...)` to talk",
            "3. **Always provide a meaningful channel `name`** — e.g., 'Sales Sync' not 'Untitled'",
            "",
            "### @Mention Rules (Group Channels)",
            "In group channels, **only @-mentioned agents are activated** by your message. "
            "Mentioning another agent **triggers it to run a full agent turn** — use mentions deliberately:",
            "- **Mention specific agents**: `mention_list='agent_a,agent_b'` — only they are triggered",
            "- **Mention everyone**: `mention_list='@everyone'` — **all** channel members are triggered (use sparingly)",
            "- **No mention_list**: nobody is triggered; the message is delivered passively",
            "- In **DM channels**, `mention_list` is ignored — the recipient is always triggered",
            "",
            "### Message Source Recognition",
            "Every incoming bus message carries a channel tag at the start of the input, e.g.: "
            "`[MessageBus · AgentName · agent_xxx · ch_yyy]`",
            "- When you see `[MessageBus · ...]` at the beginning of user input, the message came from **another agent**, NOT from your owner",
            "- Treat bus messages as **peer-to-peer agent communication** — be concise, professional, task-focused",
            "- When there is no `[MessageBus · ...]` tag, the message is from your owner via the main chat interface",
            "",
            "### Autonomy — Be Proactive",
            "You are expected to handle bus messages **autonomously**, not as a passive intermediary.",
            "- When another agent asks you a question within your capabilities, **answer it directly** — do NOT ask your owner for permission first",
            "- When another agent requests your help with a task you can perform, **perform it and reply with the result**",
            "- Treat bus messages as real work, just like requests from your owner",
            "- Only escalate to your owner if the request is outside your capabilities, requires owner-specific approval, or involves sensitive decisions",
            "",
            "### Reply Discipline — CRITICAL (prevents infinite loops)",
            "Autonomy does NOT mean replying to everything. A reply triggers the other agent to run another turn, which can cascade into loops. Apply these rules:",
            "- **Silence when the thread is done** — if the other party only acknowledges ('thanks', 'got it', '好的', '谢谢'), do NOT reply again. The conversation has reached a natural end.",
            "- **Do NOT ping-pong** — once you've answered a question and the other party has acknowledged, stop. Another reply adds zero value and triggers another round.",
            "- **Do NOT repeat yourself** — if you've already said X, do not rephrase X just to fill space.",
            "- **Substance only** — reply only when you have new information, a concrete answer, a clarifying question, or a task result. Do not reply with filler like 'I'm thinking about it', 'got your message', 'will get back to you'.",
            "- **Ignored messages resurface** — messages you choose not to reply to stay unread and will appear again next turn. This is intentional: you can defer without forgetting.",
            "- In group channels, you only see messages that @mention you — reply to those with intent (or decline via silence if a reply would just be filler).",
            "",
            "### When your owner asks about your inbox",
            "If the owner asks 'what messages do you have' or 'check your inbox', **report the contents directly**. Do not use this as an excuse to reply to peer agents — the owner is asking for a status report, not delegating.",
            "",
            "### When NOT to Call Tools",
            "- **Do NOT call `bus_get_unread`** — unread messages are already injected into your context automatically. Only call it if you suspect new messages arrived mid-turn.",
            "- **Do NOT call `bus_register_agent`** unless your profile needs updating. You are auto-registered on every turn.",
            "- **Do NOT call `bus_get_messages`** for channels whose history you already have in context.",
        ]

        # Known agents (capped + filtered)
        known = ctx_data.extra_data.get("bus_known_agents", [])
        if known:
            parts.append("")
            parts.append(f"### Known Agents (top {min(len(known), MAX_KNOWN_AGENTS_IN_CONTEXT)})")
            for a in known[:MAX_KNOWN_AGENTS_IN_CONTEXT]:
                name = a.get("agent_name") or a.get("agent_id", "")
                desc = a.get("agent_description") or a.get("description", "")
                aid = a.get("agent_id", "")
                line = f"- `{aid}` — {name}"
                if desc:
                    line += f": {desc[:80]}"
                parts.append(line)

        # Channels (capped)
        channels = ctx_data.extra_data.get("bus_channels", [])
        if channels:
            parts.append("")
            parts.append(f"### Your Channels (top {min(len(channels), MAX_CHANNELS_IN_CONTEXT)})")
            for ch in channels[:MAX_CHANNELS_IN_CONTEXT]:
                cid = ch.get("channel_id", "")
                cname = ch.get("name", "unnamed")
                ctype = ch.get("channel_type", "group")
                parts.append(f"- `{cid}` — {cname} ({ctype})")

        # Unread messages (capped, with source tag preview)
        unread = ctx_data.extra_data.get("bus_unread_messages", [])
        if unread:
            total = len(unread)
            shown = min(total, MAX_UNREAD_IN_CONTEXT)
            parts.append("")
            parts.append(f"### Unread Messages: {total} (showing {shown})")
            parts.append("> Remember: apply Reply Discipline. Ignored messages stay unread.")
            for m in unread[:MAX_UNREAD_IN_CONTEXT]:
                from_agent = m.get("from_agent", "unknown")
                channel = m.get("channel_id", "")
                content = (m.get("content") or "")[:200]
                parts.append(f"- `[MessageBus · {from_agent} · {channel}]` {content}")

        return "\n".join(parts)

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Inject MessageBus context into agent data.

        1. Auto-register current agent in bus_agent_registry (idempotent)
        2. Fetch known agents (filtered + capped)
        3. Fetch unread messages (capped)
        4. Fetch channel list (capped)
        5. If this execution was triggered BY a bus message, prefix the input
           with a source tag so the agent can recognize where it came from.
        """
        try:
            bus = await _get_default_bus_async()
            if bus is None:
                return ctx_data

            # --- 1. Auto-register this agent in bus_agent_registry ---
            try:
                db = await _get_shared_db()
                if db:
                    agent_row = await db.get_one("agents", {"agent_id": self.agent_id})
                    if agent_row:
                        owner = agent_row.get("created_by", "")
                        name = agent_row.get("agent_name", "")
                        desc = agent_row.get("agent_description", "")
                        is_public = agent_row.get("is_public", 0)
                        await bus.register_agent(
                            agent_id=self.agent_id,
                            owner_user_id=owner,
                            capabilities=[],
                            description=f"{name}: {desc}" if desc else name,
                            visibility="public" if is_public else "private",
                        )
            except Exception as e:
                logger.debug(f"Failed to auto-register agent in bus: {e}")

            # --- 2. Fetch known agents (same owner + public, excluding self) ---
            known_agents = []
            try:
                db = await _get_shared_db()
                if db:
                    agent_row = await db.get_one("agents", {"agent_id": self.agent_id})
                    my_owner = agent_row.get("created_by", "") if agent_row else ""

                    # Get agents created by same owner OR public agents
                    all_agents = await db.get("agents", {})
                    for a in all_agents:
                        if a.get("agent_id") == self.agent_id:
                            continue
                        # Only include: (a) same owner, or (b) public
                        same_owner = my_owner and a.get("created_by") == my_owner
                        is_public = bool(a.get("is_public", 0))
                        if not (same_owner or is_public):
                            continue
                        known_agents.append({
                            "agent_id": a.get("agent_id"),
                            "agent_name": a.get("agent_name", ""),
                            "agent_description": a.get("agent_description", ""),
                            "is_public": a.get("is_public", 0),
                            "created_by": a.get("created_by", ""),
                        })
                        if len(known_agents) >= MAX_KNOWN_AGENTS_IN_CONTEXT:
                            break
                if known_agents:
                    ctx_data.extra_data["bus_known_agents"] = known_agents
            except Exception as e:
                logger.debug(f"Failed to fetch known agents: {e}")

            # --- 3. Fetch unread messages (capped) ---
            unread_models = []
            try:
                unread = await bus.get_unread(self.agent_id)
                if unread:
                    unread_models = unread[:MAX_UNREAD_IN_CONTEXT]
                    ctx_data.extra_data["bus_unread_messages"] = [
                        msg.model_dump() for msg in unread_models
                    ]
            except Exception as e:
                logger.debug(f"Failed to fetch unread messages: {e}")

            # --- 4. Fetch channels (capped) ---
            try:
                rows = await bus._db.execute(
                    "SELECT c.* FROM bus_channels c "
                    "JOIN bus_channel_members cm ON c.channel_id = cm.channel_id "
                    "WHERE cm.agent_id = %s "
                    "ORDER BY c.updated_at DESC "
                    "LIMIT %s",
                    (self.agent_id, MAX_CHANNELS_IN_CONTEXT),
                )
                if rows:
                    ctx_data.extra_data["bus_channels"] = [dict(r) for r in rows]
            except Exception as e:
                logger.debug(f"Failed to load bus channels: {e}")

            # --- 5. Source recognition: prefix input with bus tag if triggered by bus ---
            # When the agent execution was triggered by a MessageBus message, the
            # input_content comes from another agent, not the owner. Add a source
            # tag at the start so the LLM can distinguish it.
            try:
                working_source = ctx_data.extra_data.get("working_source")
                if working_source == WorkingSource.MESSAGE_BUS and unread_models:
                    # Use the first unread message as the source (most recent trigger)
                    trigger = unread_models[0]
                    from_agent = trigger.from_agent if hasattr(trigger, 'from_agent') else ""
                    channel_id = trigger.channel_id if hasattr(trigger, 'channel_id') else ""
                    tag = f"[MessageBus · {from_agent} · {channel_id}]"
                    current = ctx_data.extra_data.get("input_content", "")
                    if current and not current.startswith("[MessageBus"):
                        ctx_data.extra_data["input_content"] = f"{tag} {current}"
            except Exception as e:
                logger.debug(f"Failed to inject source tag: {e}")

        except Exception as e:
            logger.exception(f"MessageBusModule hook_data_gathering failed: {e}")
        return ctx_data

    async def hook_after_event_execution(
        self, params: HookAfterExecutionParams
    ) -> None:
        """
        Post-execution cleanup for MessageBus.

        Selective mark_read: only mark messages as read if the agent actually
        replied to them. Messages the agent ignored stay unread and will
        resurface on the next turn — this is the "silence is acceptable"
        mechanism.

        We detect replies by inspecting trace for bus_send_message and
        bus_send_to_agent tool calls.
        """
        # Only process if this was a bus-triggered execution OR if the agent
        # actually sent bus messages this turn (could be user-initiated outreach)
        try:
            bus = await _get_default_bus_async()
            if bus is None:
                return

            # Extract channel IDs that were replied to
            replied_channels: set[str] = set()
            replied_agents: set[str] = set()  # For bus_send_to_agent

            if params.trace and params.trace.agent_loop_response:
                for response in params.trace.agent_loop_response:
                    tool_name = getattr(response, "tool_name", None)
                    tool_input = getattr(response, "tool_input", None)

                    # Tool names come through as mcp__message_bus_module__bus_send_message
                    # or just bus_send_message depending on how the SDK reports them
                    if not tool_name:
                        continue
                    if not isinstance(tool_input, dict):
                        continue

                    if "bus_send_message" in tool_name:
                        cid = tool_input.get("channel_id")
                        if cid:
                            replied_channels.add(cid)
                    elif "bus_send_to_agent" in tool_name:
                        target = tool_input.get("to_agent_id")
                        if target:
                            replied_agents.add(target)

            # Only mark read for channels where we actually replied
            if not replied_channels and not replied_agents:
                logger.debug(
                    f"MessageBus: agent {self.agent_id} did not reply to any bus "
                    f"messages this turn — unread messages stay unread"
                )
                return

            # Get all unread and filter to only the replied-to conversations
            unread = await bus.get_unread(self.agent_id)
            if not unread:
                return

            to_mark = []
            for m in unread:
                if m.channel_id in replied_channels:
                    to_mark.append(m.message_id)
                    continue
                # For bus_send_to_agent: we sent a DM to some agent. Mark read
                # any unread DM from that same agent (the DM channel includes both).
                if m.from_agent in replied_agents:
                    to_mark.append(m.message_id)

            if to_mark:
                await bus.mark_read(self.agent_id, to_mark)
                logger.info(
                    f"MessageBus: selective mark_read — {len(to_mark)}/{len(unread)} "
                    f"messages marked read for agent {self.agent_id} "
                    f"(replied to {len(replied_channels)} channels, {len(replied_agents)} DMs)"
                )
            else:
                logger.debug(
                    f"MessageBus: agent {self.agent_id} replied to channels "
                    f"{replied_channels} but no matching unread messages to mark"
                )
        except Exception as e:
            logger.exception(f"MessageBusModule hook_after_event_execution failed: {e}")


# =============================================================================
# Module-level helpers
# =============================================================================

_bus_instances: dict[int, Any] = {}  # keyed by event loop id


async def _get_default_bus_async():
    """Get or create a LocalMessageBus bound to the current event loop.

    Uses ``get_db_client()`` which already handles event-loop changes and
    creates a fresh aiomysql pool on the correct loop.  The bus instance is
    cached per-loop so subsequent calls on the same loop are free.
    """
    import asyncio

    loop = asyncio.get_running_loop()
    loop_id = id(loop)

    if loop_id in _bus_instances:
        return _bus_instances[loop_id]

    try:
        from xyz_agent_context.message_bus import LocalMessageBus
        from xyz_agent_context.utils.db_factory import get_db_client

        db = await get_db_client()
        backend = db._backend
        if backend is None:
            logger.warning("MessageBus: database backend is None")
            return None

        bus = LocalMessageBus(backend=backend)
        _bus_instances[loop_id] = bus
        logger.info(f"MessageBus: created instance for event loop {loop_id}")
        return bus
    except Exception as e:
        logger.exception(f"Failed to initialize default MessageBus: {e}")
        return None


def _get_default_bus():
    """Sync wrapper — only works if a bus was already created for the current loop."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        return _bus_instances.get(id(loop))
    except RuntimeError:
        return None


async def _get_shared_db():
    """Get the shared AsyncDatabaseClient."""
    try:
        from xyz_agent_context.utils.db_factory import get_db_client
        return await get_db_client()
    except Exception as e:
        logger.debug(f"Failed to get shared DB client: {e}")
        return None
