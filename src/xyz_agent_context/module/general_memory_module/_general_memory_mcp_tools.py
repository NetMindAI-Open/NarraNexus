"""
@file_name: _general_memory_mcp_tools.py
@author: NetMind.AI
@date: 2026-06-03
@description: The agent-facing memory tools — `remember` and `grep_memory`.

These are the unified "回忆" surface (design §6.3): one cross-kind ranked
recall + one cross-kind exact/regex search, replacing the fragmented per-module
recall tools (view_narrative / search_social_network / get_chat_history / …).
Both are thin wrappers over MemoryCoordinator. `agent_id` is a tool parameter
(the LLM passes its own id — same convention as every other module's tools).
"""
from __future__ import annotations

from typing import Any, Dict, List

from loguru import logger
from mcp.server.fastmcp import FastMCP

from xyz_agent_context.memory import MemoryCoordinator, MemoryEngine
from xyz_agent_context.module.base import XYZBaseModule


def _format(hits: List[Any]) -> List[Dict[str, Any]]:
    """Render hits for the agent — text + provenance (which kind, when)."""
    out: List[Dict[str, Any]] = []
    for h in hits:
        r = h.record
        item: Dict[str, Any] = {
            "kind": h.kind,
            "memory": r.content_text,
            "when": (r.created_at.isoformat() if r.created_at else None),
            "tags": r.tags,
        }
        # Projection kinds carry a pointer back to the live original. The agent
        # fetches full / current detail via the matching by-id tool, e.g.
        # source {"kind":"job","id":...} → job_retrieval_by_id,
        # {"kind":"event","id":...} → view_event, {"kind":"narrative",...} →
        # view_narrative. Self-contained kinds (observation/entity) omit it.
        if r.source_ref:
            item["source"] = r.source_ref
        out.append(item)
    return out


def create_general_memory_mcp_server(port: int) -> FastMCP:
    mcp = FastMCP("general_memory_module")
    mcp.settings.port = port

    @mcp.tool(
        description=(
            "Recall what you remember across ALL of your memory (entities, chat, "
            "observations, narratives, jobs, messages) by meaning. Use this when "
            "you need context about a person, topic, past decision, or anything you "
            "may have learned before. Returns the most relevant memories, ranked."
        )
    )
    async def remember(agent_id: str, query: str, limit: int = 15) -> dict:
        try:
            db = await XYZBaseModule.get_mcp_db_client()
            coord = MemoryCoordinator(MemoryEngine(db, agent_id))
            hits = await coord.remember(query, limit=limit)
            return {"success": True, "query": query, "memories": _format(hits)}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[memory.remember] failed: {e}")
            return {"success": False, "error": str(e), "memories": []}

    @mcp.tool(
        description=(
            "Search your memory for an EXACT string or regex pattern (like grep). "
            "Use this when you need a precise token you saw before — an id, URL, "
            "order number, file path, exact name spelling — that semantic recall "
            "might miss. Set regex=true to use a regular expression."
        )
    )
    async def grep_memory(agent_id: str, pattern: str, regex: bool = False, limit: int = 30) -> dict:
        try:
            db = await XYZBaseModule.get_mcp_db_client()
            coord = MemoryCoordinator(MemoryEngine(db, agent_id))
            hits = await coord.grep_memory(pattern, regex=regex, limit=limit)
            return {"success": True, "pattern": pattern, "matches": _format(hits)}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[memory.grep_memory] failed: {e}")
            return {"success": False, "error": str(e), "matches": []}

    logger.info(f"GeneralMemory MCP: remember + grep_memory registered on port {port}")
    return mcp
