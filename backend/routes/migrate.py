"""
@file_name: migrate.py
@author: NetMind.AI
@date: 2026-07-21
@description: Agent Migration Scanner API (under /api/migrate) — LOCAL ONLY.

Detect + extract other-framework agent configs (Claude Code / Hermes /
OpenClaw / Codex) from the user's local filesystem into the standardized JSON.

**Local/desktop only.** In cloud mode the executor/backend is remote — there is
no user filesystem to scan — so every endpoint returns 503 `migration_local_only`.
Consumers (Import Button, Migration Skill) do the map+write; this route never
writes anything to NarraNexus.

Design: reference/self_notebook/specs/2026-07-21-agent-migration-tech-design.md
"""

import asyncio
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from narranexus.platform.utils.deployment_mode import is_cloud_mode
from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.migration import scanner
from narranexus.platform.migration.mapper import build_plan
from narranexus.platform.migration import hurry
from narranexus.platform.migration.applier import apply_plan
from narranexus.platform.schema.migration_schema import Framework, StandardizedAgentImport
from backend.auth import resolve_current_user_id

router = APIRouter()


def _require_local_or_raise() -> None:
    if is_cloud_mode():
        raise HTTPException(
            status_code=503,
            detail="migration_local_only: the agent-import scanner reads your local "
            "filesystem and is only available in the desktop/local app.",
        )


class ScanRequest(BaseModel):
    # Explicit source dir (e.g. "~/.claude"). If omitted, auto-detect across the
    # standard home locations and scan the highest-confidence framework.
    path: Optional[str] = None
    framework: Optional[Framework] = None


@router.get("/detect")
async def detect(request: Request) -> dict:
    """List every known framework found in the standard home locations."""
    _require_local_or_raise()
    # scanner.detect walks the filesystem — run it off the event loop so it
    # can't stall the shared loop (this fires on every local app load).
    detections = await asyncio.to_thread(scanner.detect)
    return {"detections": [d.model_dump() for d in detections]}


@router.post("/scan")
async def scan(request: Request, payload: ScanRequest) -> dict:
    """Scan one source into the standardized JSON (detect + extract, no write)."""
    _require_local_or_raise()
    try:
        # Extraction parses session .jsonl files that can be 100MB+ — keep that
        # synchronous, blocking work off the event loop.
        result = await asyncio.to_thread(
            scanner.scan, path=payload.path, framework=payload.framework
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"migrate.scan failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=400, detail=f"scan failed: {e}")
    logger.info(
        f"migrate.scan: framework={result.source.framework} "
        f"skills={len(result.skills)} memory={len(result.memory)} mcp={len(result.mcp_servers)}"
    )
    return result.model_dump()


class ApplyRequest(BaseModel):
    # The standardized JSON from /scan (or a user-edited copy).
    import_data: StandardizedAgentImport
    # Target agent; omit to create a new one.
    agent_id: Optional[str] = None
    # Client-generated handle for THIS apply, so /hurry below can reach it
    # mid-flight. Optional: an apply without one simply can't be hurried.
    import_id: Optional[str] = None


class HurryRequest(BaseModel):
    # Bounded because the id is kept in a process-level registry (hurry.py)
    # and this route is exempt from the body-size middleware. Ownership is NOT
    # checked: the route is local-only (single user, single process) and the
    # id is minted by that same client — a check would guard against nobody.
    import_id: str = Field(min_length=1, max_length=128)


@router.post("/hurry")
async def hurry_import(request: Request, payload: HurryRequest) -> dict:
    """Tell a RUNNING apply to stop summarizing and just finish.

    The user pressed stop while a project was importing. Cutting the HTTP
    request would leave a half-populated agent, and waiting out N sequential
    helper-LLM summaries can take minutes (Owner objection 2026-09-03) — so this
    is the third option: the apply keeps going but drops to the deterministic
    no-LLM summary for every session it has left. Every session still lands.

    Best-effort by construction: the mark is in-process, so it has to reach the
    worker running that apply. Local (the only place migration runs) is
    single-process. If it misses, the import keeps its LLM summaries and the
    user waits as before — never corrupt, just not faster.
    """
    _require_local_or_raise()
    await resolve_current_user_id(request)
    hurry.mark(payload.import_id)
    logger.info(f"migrate.hurry: {payload.import_id}")
    return {"success": True}


@router.post("/apply")
async def apply(request: Request, payload: ApplyRequest) -> dict:
    """Execute the migration: create/populate an agent from the scanned JSON.

    Local-only, like detect/scan: Agent Migration is a desktop/local feature
    (Owner decision — cloud has no user filesystem to import from). detect/scan
    already 503 on cloud, so there is no legitimate cloud path to import_data;
    gating apply too closes the direct-POST hole. `user_id` comes from auth.
    """
    _require_local_or_raise()
    user_id = await resolve_current_user_id(request)
    db = await get_db_client()
    # When reusing an existing agent, `agent_id` is attacker-controlled input —
    # verify the caller OWNS it, or importing overwrites another user's Awareness
    # / injects memory/skills/MCP into their agent (IDOR). Mirrors the ownership
    # check in home_assistant / lark routes.
    if payload.agent_id:
        agent = await db.get_one("agents", {"agent_id": payload.agent_id})
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent {payload.agent_id} not found.")
        if user_id and agent.get("created_by") != user_id:
            raise HTTPException(status_code=403, detail="Permission denied: you do not own this agent.")
    plan = build_plan(payload.import_data)
    result = await apply_plan(
        db, user_id, plan, agent_id=payload.agent_id, import_id=payload.import_id
    )
    return result.model_dump()
