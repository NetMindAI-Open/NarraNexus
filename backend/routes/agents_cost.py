"""
@file_name: agents_cost.py
@author: Bin Liang
@date: 2026-03-12
@description: Agent cost tracking routes

Provides endpoints for:
- GET /{agent_id}/costs - Get cost summary and recent records

Per-viewer tenancy:
- Cloud mode: viewer_id from request.state.user_id (JWT middleware populated).
- Local mode: viewer_id from request.state.user_id (X-User-Id header populated
  by auth_middleware; falls back to singleton "first user" for legacy clients).
- Never trust ?user_id= query param (TDR-12 impersonation vector).
- Cost rows are scoped to agents the viewer OWNS (agents.created_by = viewer_id).
  Public agents that the viewer can merely SEE do NOT contribute to their cost
  view — costs were paid by the owner, exposing them would leak owner spend.
"""

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger

from backend.auth import resolve_current_user_id
from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.schema import (
    CostResponse,
    CostSummary,
    CostModelBreakdown,
    CostDailyEntry,
    CostRecord,
)


router = APIRouter()


async def _resolve_viewer_id(request: Request) -> str:
    """Resolve viewer_id from the session — never from a query param.

    Identity is populated by ``auth_middleware``:
    - cloud: from the verified JWT Bearer token
    - local: from the ``X-User-Id`` header set by the frontend
             (fallback to singleton "first user" if header is missing)

    The query-param rejection below is defensive: even if some legacy
    caller passes ``?user_id=...`` we refuse, to avoid ambiguity over
    which identity wins.
    """
    if "user_id" in request.query_params:
        raise HTTPException(
            status_code=400,
            detail="user_id query param not accepted; viewer identified by session",
        )
    return await resolve_current_user_id(request)


@router.get("/{agent_id}/costs", response_model=CostResponse)
async def get_agent_costs(
    request: Request,
    agent_id: str,
    days: int = Query(default=7, ge=1, le=90, description="Number of days to look back"),
    limit: int = Query(default=50, ge=1, le=200, description="Max recent records to return"),
):
    """
    Get cost summary and recent records for an Agent.

    Returns aggregated stats (total cost, by-model breakdown, daily trend)
    plus the most recent individual records.

    agent_id == "_all" aggregates across every agent the viewer owns.
    Otherwise, the viewer must own the requested agent or the call 404s.
    """
    viewer_id = await _resolve_viewer_id(request)

    try:
        db = await get_db_client()

        # Calculate cutoff in Python (works for both MySQL and SQLite).
        from datetime import datetime, timedelta, timezone as dt_tz
        cutoff = (datetime.now(dt_tz.utc) - timedelta(days=days)).isoformat()

        if agent_id == "_all":
            # Aggregate across every agent the viewer owns.
            owned = await db.execute(
                "SELECT agent_id FROM agents WHERE created_by=%s",
                (viewer_id,),
            )
            owned_ids = [r["agent_id"] for r in owned]
            if not owned_ids:
                return CostResponse(
                    success=True,
                    summary=CostSummary(),
                    records=[],
                    total_count=0,
                )
            placeholders = ",".join(["%s"] * len(owned_ids))
            rows = await db.execute(
                f"""
                SELECT id, agent_id, event_id, call_type, model,
                       input_tokens, output_tokens, total_cost_usd, created_at
                FROM cost_records
                WHERE agent_id IN ({placeholders})
                  AND created_at >= %s
                ORDER BY created_at DESC
                """,
                (*owned_ids, cutoff),
            )
        else:
            # Single-agent: enforce ownership.
            owner_row = await db.execute(
                "SELECT created_by FROM agents WHERE agent_id=%s LIMIT 1",
                (agent_id,),
            )
            if not owner_row or owner_row[0]["created_by"] != viewer_id:
                raise HTTPException(status_code=404, detail="Agent not found")
            rows = await db.execute(
                """
                SELECT id, agent_id, event_id, call_type, model,
                       input_tokens, output_tokens, total_cost_usd, created_at
                FROM cost_records
                WHERE agent_id = %s
                  AND created_at >= %s
                ORDER BY created_at DESC
                """,
                (agent_id, cutoff),
            )

        if not rows:
            return CostResponse(
                success=True,
                summary=CostSummary(),
                records=[],
                total_count=0,
            )

        # Build summary
        total_cost = 0.0
        total_input = 0
        total_output = 0
        by_model: dict[str, dict] = {}
        daily_map: dict[str, dict] = {}

        for row in rows:
            cost = float(row["total_cost_usd"])
            inp = row["input_tokens"]
            out = row["output_tokens"]
            model = row["model"]

            total_cost += cost
            total_input += inp
            total_output += out

            if model not in by_model:
                by_model[model] = {"cost": 0.0, "input_tokens": 0, "output_tokens": 0, "call_count": 0}
            by_model[model]["cost"] += cost
            by_model[model]["input_tokens"] += inp
            by_model[model]["output_tokens"] += out
            by_model[model]["call_count"] += 1

            ca = row["created_at"]
            if ca is None:
                day_str = "unknown"
            elif isinstance(ca, str):
                day_str = ca[:10]
            else:
                day_str = ca.strftime("%Y-%m-%d")
            if day_str not in daily_map:
                daily_map[day_str] = {"input_tokens": 0, "output_tokens": 0}
            daily_map[day_str]["input_tokens"] += inp
            daily_map[day_str]["output_tokens"] += out

        summary = CostSummary(
            total_cost_usd=round(total_cost, 6),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            by_model={
                k: CostModelBreakdown(
                    cost=round(v["cost"], 6),
                    input_tokens=v["input_tokens"],
                    output_tokens=v["output_tokens"],
                    call_count=v["call_count"],
                )
                for k, v in by_model.items()
            },
            daily=sorted(
                [CostDailyEntry(date=d, input_tokens=v["input_tokens"], output_tokens=v["output_tokens"]) for d, v in daily_map.items()],
                key=lambda x: x.date,
            ),
        )

        recent = rows[:limit]
        records = [
            CostRecord(
                id=r["id"],
                agent_id=r["agent_id"],
                event_id=r.get("event_id"),
                call_type=r["call_type"],
                model=r["model"],
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
                total_cost_usd=float(r["total_cost_usd"]),
                created_at=r["created_at"].isoformat() if hasattr(r.get("created_at"), "isoformat") else r.get("created_at"),
            )
            for r in recent
        ]

        return CostResponse(
            success=True,
            summary=summary,
            records=records,
            total_count=len(rows),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get costs for agent {agent_id}")
        return CostResponse(success=False, error=str(e))
