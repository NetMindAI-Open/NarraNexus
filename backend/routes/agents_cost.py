"""
@file_name: agents_cost.py
@author: Bin Liang
@date: 2026-03-12
@description: Agent cost tracking routes

Provides endpoints for:
- GET /{agent_id}/costs - Get cost summary and recent records
"""

from fastapi import APIRouter, Query
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils.cost_tracker import calculate_cost
from xyz_agent_context.schema import (
    CostResponse,
    CostSummary,
    CostModelBreakdown,
    CostDailyEntry,
    CostRecord,
)


router = APIRouter()


@router.get("/{agent_id}/costs", response_model=CostResponse)
async def get_agent_costs(
    agent_id: str,
    days: int = Query(default=7, ge=1, le=90, description="Number of days to look back"),
    limit: int = Query(default=50, ge=1, le=200, description="Max recent records to return"),
):
    """
    Get cost summary and recent records for an Agent.

    Returns aggregated stats (total cost, by-model breakdown, daily trend)
    plus the most recent individual records.
    """
    try:
        db = await get_db_client()

        # Fetch records within the time window
        rows = await db.execute(
            """
            SELECT id, agent_id, event_id, call_type, model,
                   input_tokens, output_tokens, total_cost_usd, created_at
            FROM cost_records
            WHERE agent_id = %s
              AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
            ORDER BY created_at DESC
            """,
            (agent_id, days),
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
        daily_map: dict[str, float] = {}

        for row in rows:
            cost = float(row["total_cost_usd"])
            inp = row["input_tokens"]
            out = row["output_tokens"]
            model = row["model"]

            total_cost += cost
            total_input += inp
            total_output += out

            # Per-model breakdown
            if model not in by_model:
                by_model[model] = {"cost": 0.0, "input_tokens": 0, "output_tokens": 0, "call_count": 0}
            by_model[model]["cost"] += cost
            by_model[model]["input_tokens"] += inp
            by_model[model]["output_tokens"] += out
            by_model[model]["call_count"] += 1

            # Daily aggregation
            day_str = row["created_at"].strftime("%Y-%m-%d") if row["created_at"] else "unknown"
            daily_map[day_str] = daily_map.get(day_str, 0.0) + cost

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
                [CostDailyEntry(date=d, cost=round(c, 6)) for d, c in daily_map.items()],
                key=lambda x: x.date,
            ),
        )

        # Recent records (limited)
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
                created_at=r["created_at"].isoformat() if r["created_at"] else None,
            )
            for r in recent
        ]

        return CostResponse(
            success=True,
            summary=summary,
            records=records,
            total_count=len(rows),
        )

    except Exception as e:
        logger.exception(f"Failed to get costs for agent {agent_id}")
        return CostResponse(success=False, error=str(e))
