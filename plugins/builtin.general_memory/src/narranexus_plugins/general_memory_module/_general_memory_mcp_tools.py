"""
@file_name: _general_memory_mcp_tools.py
@author: NetMind.AI
@date: 2026-06-03
@description: The agent-facing memory tools — `remember`, `grep_memory`,
`memory_retain`.

These are the unified "回忆" surface (design §6.3): one cross-kind ranked
recall + one cross-kind exact/regex search + one explicit durable write,
replacing the fragmented per-module recall tools (view_narrative /
search_social_network / get_chat_history / …). All three (`remember`,
`grep_memory`, `memory_retain`) route through the AgentDataStore seam
(DirectStore locally / HttpStore in cloud — see module/data_access), so the
cloud mcp container needs no db credentials. grep's regex path is now safe on
the shared API: retrieval.grep_filter runs the untrusted pattern through the
`regex` package with a per-match timeout + total budget (was the last blocker —
its HTTP twin used to refuse regex).
`agent_id` is a tool parameter (the LLM passes its own id — same convention as
every other module's tools).
"""
from __future__ import annotations

from loguru import logger
from mcp.server.fastmcp import FastMCP


def create_general_memory_mcp_server() -> FastMCP:
    mcp = FastMCP("general_memory_module")

    @mcp.tool(
        description=(
            "Recall what you remember across ALL of your memory (entities, chat, "
            "observations, narratives, jobs, messages) by meaning. Use this when "
            "you need context about a person, topic, past decision, or anything you "
            "may have learned before. Returns the most relevant memories, ranked."
        )
    )
    async def remember(agent_id: str, query: str, limit: int = 15) -> dict:
        # Routes through the AgentDataStore seam: DirectStore locally (same
        # MemoryCoordinator call as before), HttpStore in cloud (no db creds
        # in mcp). Both return the identical dict — see module/data_access.
        from narranexus.platform.module_system.data_access import get_agent_data_store

        return await get_agent_data_store().remember(agent_id, query, limit)

    @mcp.tool(
        description=(
            "Search your memory for an EXACT string or regex pattern (like grep). "
            "Use this when you need a precise token you saw before — an id, URL, "
            "order number, file path, exact name spelling — that semantic recall "
            "might miss. Set regex=true to use a regular expression. "
            "If the response has truncated=true, the search hit its time budget "
            "and the result may be INCOMPLETE — narrow the pattern and retry "
            "before concluding that nothing matched."
        )
    )
    async def grep_memory(agent_id: str, pattern: str, regex: bool = False, limit: int = 30) -> dict:
        # Route through the AgentDataStore seam: DirectStore (local) or HttpStore
        # (cloud, backend API — no db creds in mcp). The regex engine is
        # ReDoS-guarded in retrieval.grep_filter, so serving it over HTTP is safe.
        from narranexus.platform.module_system.data_access import get_agent_data_store
        return await get_agent_data_store().grep_memory(agent_id, pattern, regex, limit)

    @mcp.tool(
        description=(
            "Explicitly write ONE durable fact into your long-term General Memory. "
            "Use when you learn something worth keeping that the automatic per-turn "
            "extraction would miss — e.g. importing a fact from another agent, or a "
            "user preference they asked you to remember verbatim. `content` is the "
            "fact in natural language; `source` optionally notes where it came from "
            "(e.g. 'imported from MEMORY.md'). Facts are deduplicated by meaning."
        )
    )
    async def memory_retain(agent_id: str, content: str, source: str = "") -> dict:
        # Routes through the AgentDataStore seam (see remember above).
        from narranexus.platform.module_system.data_access import get_agent_data_store

        return await get_agent_data_store().memory_retain(agent_id, content, source)

    logger.info(
        "GeneralMemory MCP: remember + grep_memory + memory_retain registered"
    )
    return mcp
