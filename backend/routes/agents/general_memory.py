"""
@file_name: general_memory.py
@author:
@date: 2026-08-10
@description: General-memory endpoints for the MCP data-access seam.

Backend counterparts of the GeneralMemoryModule MCP tools (remember /
grep_memory / memory_retain) so the Http path of AgentDataStore can serve
them without db credentials in the mcp container.

Endpoints (mounted under /api/agents by agents/core.py):
  GET  /{agent_id}/memory/remember  ?query=&limit=15        (query 1-512, limit 1-100)
  GET  /{agent_id}/memory/grep      ?pattern=&regex=false&limit=30  (pattern 1-256, limit 1-200)
  POST /{agent_id}/memory/retain    {content, source}

grep now serves regex (it used to refuse it): the engine bounds the untrusted
pattern with a per-match timeout + a per-request budget and offloads the scan
off the event loop (see retrieval.grep_filter / engine.grep). Added 2026-08-10
with the grep seam migration.

Each mirrors the matching MCP tool in
``_general_memory_mcp_tools.py`` byte-for-byte on call shape (same
MemoryCoordinator(MemoryEngine(db, agent_id)) construction, the shared
``memory.format_memory_hits`` renderer, same response dict keys) so an
HttpStore-backed caller gets identical payloads to the in-process MCP path.

Note the pydantic bounds below (query 1-512, limit 1-100, content <=64KB,
source <=512): unlike the tool body's own {"success": false} failures, an
out-of-bounds argument is rejected by FastAPI as a 422 BEFORE the handler
runs. HttpStore._parse_dict surfaces that 422 as its own in-band
"invalid arguments" dict so the agent can correct — see store.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

from narranexus.platform.memory import (
    MemoryCoordinator,
    MemoryEngine,
    MemoryRecord,
    SCOPE_AGENT,
    format_memory_hits,
)
from narranexus.platform.utils.db.db_factory import get_db_client

# Ownership gate (backend/routes/_ownership.py): agent_id is attacker-
# controlled input — without the owner check a cross-tenant IDOR opens up
# (read/write another user's memory). Local mode (no JWT identity) does not
# enforce; see the helper's security-posture docstring before relying on it.
from backend.routes._ownership import assert_owned

router = APIRouter()


class MemoryRetainBody(BaseModel):
    # 64KB cap: the MCP twin takes model-generated content; over HTTP the
    # field is caller-controlled and this family bounds every free-text input.
    content: str = Field(min_length=1, max_length=65536)
    source: str = Field(default="", max_length=512)


@router.get("/{agent_id}/memory/remember")
async def remember(
    request: Request,
    agent_id: str,
    query: str = Query(min_length=1, max_length=512),
    limit: int = Query(15, ge=1, le=100),
) -> dict:
    """Cross-kind ranked recall by meaning. Mirrors the ``remember`` MCP tool."""
    await assert_owned(request, agent_id)
    try:
        db = await get_db_client()
        coord = MemoryCoordinator(MemoryEngine(db, agent_id))
        hits = await coord.remember(query, limit=limit)
        return {"success": True, "query": query, "memories": format_memory_hits(hits)}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[memory.remember] failed for agent {agent_id}: {e}")
        return {"success": False, "error": str(e), "memories": []}


@router.get("/{agent_id}/memory/grep")
async def grep_memory(
    request: Request,
    agent_id: str,
    pattern: str = Query(min_length=1, max_length=256),
    regex: bool = False,
    limit: int = Query(30, ge=1, le=200),
) -> dict:
    """Substring (default) or regex search over memory content — byte-parity
    Http twin of the ``grep_memory`` MCP tool, and the shared implementation
    behind the AgentDataStore seam's DirectStore.

    Regex mode is now served here (it used to be refused): the engine
    (``retrieval.grep_filter``) runs the untrusted pattern through the ``regex``
    package with a per-match timeout + a total wall-clock budget, so a
    catastrophic-backtracking pattern can no longer wedge the shared API loop —
    it is skipped, not run to completion. Owner-gated."""
    await assert_owned(request, agent_id)
    try:
        db = await get_db_client()
        coord = MemoryCoordinator(MemoryEngine(db, agent_id))
        hits, truncated = await coord.grep_memory(pattern, regex=regex, limit=limit)
        return {"success": True, "pattern": pattern,
                "matches": format_memory_hits(hits), "truncated": truncated}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[memory.grep_memory] failed for agent {agent_id}: {e}")
        return {"success": False, "error": str(e), "matches": []}


@router.post("/{agent_id}/memory/retain")
async def memory_retain(request: Request, agent_id: str, body: MemoryRetainBody) -> dict:
    """Write one durable fact into long-term memory. Mirrors the ``memory_retain`` MCP tool."""
    await assert_owned(request, agent_id)
    try:
        content = body.content
        if not content or not content.strip():
            return {"success": False, "error": "content is empty"}
        db = await get_db_client()
        engine = MemoryEngine(db, agent_id)
        tags = ["imported"] if body.source else []
        rec = await engine.retain(MemoryRecord(
            agent_id=agent_id, scope_type=SCOPE_AGENT, kind="observation",
            subtype="world", content_text=content.strip(),
            tags=tags, proof_count=1,
            source_ref={"kind": "import", "id": body.source} if body.source else None,
        ))
        return {"success": True, "record_id": rec.record_id}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[memory.memory_retain] failed for agent {agent_id}: {e}")
        return {"success": False, "error": str(e)}
