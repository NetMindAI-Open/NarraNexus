"""
@file_name: cost_tracker.py
@author: Bin Liang
@date: 2026-03-12
@description: LLM API cost calculation and recording utility

Provides centralized cost tracking for all LLM API calls:
- Claude (agent_loop)
- OpenAI GPT (llm_function)
- Gemini (llm_function)
- OpenAI Embedding

Architecture:
    Pure functions + async recorder + global cost context.
    AgentRuntime sets the cost context once at the start of run(),
    and all subsequent LLM calls automatically record costs without
    needing explicit agent_id/db parameters.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Dict, Optional, Tuple

from loguru import logger


# =============================================================================
# Price Table (per million tokens, USD)
# =============================================================================
#
# Only contains models whose name strings are controlled by our code:
#   - OpenAI: hardcoded in openai_agents_sdk.py (MODEL_NAME)
#   - Gemini: hardcoded in gemini_api_sdk.py (self.model)
#   - Embedding: from settings (openai_embedding_model)
# Claude costs use sdk_cost_usd directly (see record_cost), so no entry needed here.
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # OpenAI
    "gpt-5.1-2025-11-13": {"input": 2.0, "output": 8.0},
    # Gemini
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    # Embedding
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
    "text-embedding-3-large": {"input": 0.13, "output": 0.0},
}


# =============================================================================
# Global Cost Context (asyncio-safe via contextvars)
# =============================================================================
# Stores (agent_id, db_client) so LLM calls don't need explicit parameters.
# Set by AgentRuntime.run() at the start, cleared in finally block.

_cost_context: ContextVar[Optional[Tuple[str, object]]] = ContextVar(
    "_cost_context", default=None
)


def set_cost_context(agent_id: str, db) -> None:
    """
    Set global cost tracking context for the current async task.
    Called once by AgentRuntime.run() — all subsequent LLM calls
    in this task automatically use this context.
    """
    _cost_context.set((agent_id, db))


def clear_cost_context() -> None:
    """Clear the cost tracking context (called in AgentRuntime.run() finally block)."""
    _cost_context.set(None)


def get_cost_context() -> Optional[Tuple[str, object]]:
    """Get the current cost context (agent_id, db), or None if not set."""
    return _cost_context.get()


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> dict:
    """
    Calculate cost for a single API call based on the price table.

    Only works for models whose name strings we control (OpenAI, Gemini, Embedding).
    Claude costs should use sdk_cost_usd directly — this function returns zeros for
    unknown models, which is expected behavior, not an error.

    Args:
        model: Model identifier (must be an exact key in MODEL_PRICING)
        input_tokens: Number of input tokens consumed
        output_tokens: Number of output tokens consumed

    Returns:
        {"input_cost": float, "output_cost": float, "total_cost": float}
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        # Not a warning — Claude calls intentionally skip the price table
        logger.debug(f"Model not in price table (using sdk_cost if available): {model}")
        return {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}

    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return {
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": input_cost + output_cost,
    }


async def record_cost(
    db,
    agent_id: str,
    event_id: Optional[str],
    call_type: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    sdk_cost_usd: Optional[float] = None,
) -> None:
    """
    Calculate cost and persist a record to the database.

    Args:
        db: AsyncDatabaseClient instance
        agent_id: Agent that incurred the cost
        event_id: Associated event (None for standalone llm_function / embedding calls)
        call_type: "agent_loop" | "llm_function" | "embedding"
        model: Model identifier
        input_tokens: Input token count
        output_tokens: Output token count
        sdk_cost_usd: SDK-calculated cost (used as fallback when model is unknown)
    """
    cost = calculate_cost(model, input_tokens, output_tokens)
    # Prefer SDK-provided cost (most accurate, e.g. Claude SDK considers caching discounts).
    # Fall back to price-table calculation, then to $0 as last resort.
    if sdk_cost_usd and sdk_cost_usd > 0:
        final_cost = sdk_cost_usd
    elif cost["total_cost"] > 0:
        final_cost = cost["total_cost"]
    else:
        final_cost = 0.0
    try:
        await db.insert("cost_records", {
            "agent_id": agent_id,
            "event_id": event_id,
            "call_type": call_type,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_cost_usd": final_cost,
        })
        logger.debug(
            f"Cost recorded: agent={agent_id} model={model} "
            f"tokens={input_tokens}+{output_tokens} cost=${final_cost:.6f}"
            f"{' (sdk)' if cost['total_cost'] == 0 and sdk_cost_usd else ''}"
        )
    except Exception as e:
        # Cost tracking failure should never block the main flow
        logger.exception(f"Failed to record cost: {e}")

    # --- System-default quota deduct hook ---
    # Fires only when the request was routed through the system free-tier
    # branch (ProviderResolver tagged provider_source="system") AND
    # auth_middleware tagged current_user_id. Any failure here is logged
    # and swallowed — cost_tracker is observability, not flow control.
    try:
        from xyz_agent_context.agent_framework.api_config import (
            get_current_user_id,
            get_provider_source,
        )
        if get_provider_source() == "system":
            uid = get_current_user_id()
            if uid:
                from xyz_agent_context.agent_framework.quota_service import (
                    QuotaService,
                )
                try:
                    svc = QuotaService.default()
                except RuntimeError:
                    svc = None
                if svc is not None:
                    try:
                        await svc.deduct(uid, input_tokens, output_tokens)
                    except Exception as e:
                        logger.exception(f"quota deduct hook failed for {uid}: {e}")
    except Exception:
        # Defensive: imports/ContextVar reads must never break cost_tracker.
        pass
