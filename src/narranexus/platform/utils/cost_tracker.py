"""
@file_name: cost_tracker.py
@author: Bin Liang
@date: 2026-03-12
@description: LLM API cost calculation and recording utility

Provides centralized cost tracking for all LLM API calls:
- Claude (agent_loop)
- OpenAI GPT (llm_function)
- Gemini (llm_function)

Architecture:
    Pure functions + async recorder + global cost context.
    AgentRuntime sets the cost context once at the start of run(),
    and all subsequent LLM calls automatically record costs without
    needing explicit agent_id/db parameters.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional, Tuple

from loguru import logger


# =============================================================================
# Pricing
# =============================================================================
#
# The two-entry hand-written MODEL_PRICING that used to live here is gone; see
# utils/model_pricing.py for what it was and why it failed (short version: on
# 2026-07-30 neither entry matched any model actually running, so every
# llm_function / llm_stream / embedding row in the ledger was booked at $0,
# and the one entry that DID exist was priced at half the real rate).


# =============================================================================
# Global Cost Context (asyncio-safe via contextvars)
# =============================================================================
# Stores (agent_id, db_client) so LLM calls don't need explicit parameters.
# Set by AgentRuntime.run() at the start, cleared in finally block.

_cost_context: ContextVar[Optional[Tuple[str, object]]] = ContextVar(
    "_cost_context", default=None
)

# Which turn the current work belongs to. Deliberately a SEPARATE var rather
# than a third slot in the tuple above: every helper call site destructures
# `_agent_id, _db = ctx`, and widening that tuple would break each of them for
# no gain — the two have different lifecycles anyway (agent+db span a worker
# pass, the event spans one turn inside it).
_cost_event_id: ContextVar[Optional[str]] = ContextVar(
    "_cost_event_id", default=None
)


def set_cost_context(agent_id: str, db) -> None:
    """
    Set global cost tracking context for the current async task.
    Called once by AgentRuntime.run() — all subsequent LLM calls
    in this task automatically use this context.

    Does NOT touch the ambient event id: the two have different lifetimes on
    purpose, and clearing the event on a mid-turn re-set would drop those
    tokens off the turn they belong to.

    Prefer ``cost_context_scope`` for any nested / mid-turn use — see its
    docstring for the failure this unscoped setter has already caused.
    """
    _cost_context.set((agent_id, db))


def clear_cost_context() -> None:
    """Clear the cost tracking context, event id included.

    OUTERMOST use only — a background worker finishing its pass, or
    ``AgentRuntime``'s post-turn task wrapping up. Calling this partway
    through a turn empties the turn's own context: everything after it
    records nothing, and any task spawned later copies the emptied context.
    Mid-turn callers want ``cost_context_scope`` instead.
    """
    _cost_context.set(None)
    _cost_event_id.set(None)


def get_cost_context() -> Optional[Tuple[str, object]]:
    """Get the current cost context (agent_id, db), or None if not set."""
    return _cost_context.get()


def get_cost_event_id() -> Optional[str]:
    """Get the turn this context is currently attributed to, or None."""
    return _cost_event_id.get()


def _unwind(var: ContextVar, token) -> None:
    """Restore ``var`` to its pre-scope value, degrading to a clear.

    ``ContextVar.reset`` raises ``ValueError: Token was created in a different
    Context`` when the block is unwound somewhere other than where it was
    entered. An async generator abandoned mid-iteration does exactly that: its
    ``finally`` runs when the generator is closed or collected, under whatever
    context happens to be current. Cost tracking is observability, not flow
    control (iron rule of this module) — so a cross-context unwind clears
    rather than raising into an unrelated caller's stack.
    """
    try:
        var.reset(token)
    except ValueError:
        var.set(None)


@contextmanager
def cost_event_scope(event_id: Optional[str]) -> Iterator[None]:
    """Attribute every cost recorded inside the block to ``event_id``.

    Entered by ``AgentRuntime.run()`` as soon as Step 0 has run, and unwound on
    exit — including on the error path, so a failed turn cannot leak its id
    onto whatever runs next in the same context. Entered unconditionally: a run
    with no Event row scopes to None, which is how a nested run avoids booking
    its spend onto the parent turn's id.

    Background Steps 5-6 are spawned from inside this block: ``create_task``
    copies the context at spawn time, so post-turn hook spend keeps the turn's
    id even though the task outlives the block.
    """
    token = _cost_event_id.set(event_id)
    try:
        yield
    finally:
        _unwind(_cost_event_id, token)


@contextmanager
def cost_context_scope(agent_id: str, db) -> Iterator[None]:
    """Set (agent_id, db) for the block, then RESTORE what was there before.

    The restore is the whole point. ``step_3``'s fallback-reply helper used to
    ``set_cost_context`` and then ``clear_cost_context`` in a finally — inside
    an async generator, which (PEP 568 never landed) does NOT get its own
    context copy. So that clear wiped the TURN's own (agent_id, db), not a
    private copy: every ``record_cost`` after it in that turn found no context
    and returned early, and Steps 5-6 — spawned later, copying the emptied
    context — booked nothing at all. Only the fallback paths (``no_reply`` /
    ``after_error``) were affected, so the account was wrong exactly on the
    turns that failed, with nothing on screen to show it.
    """
    token = _cost_context.set((agent_id, db))
    try:
        yield
    finally:
        _unwind(_cost_context, token)


def warn_missing_usage(source: str, model: str, call_type: str) -> None:
    """De-silence a zero/absent usage report.

    Every LLM call site resolves a cost context and records tokens only when
    ``input+output > 0``. When a live context (agent_id + db) is present but the
    provider returned no usage, the tokens go UNRECORDED — historically silent,
    which is exactly how the consolidation-worker hole hid for weeks. Emitting a
    warning here turns a silent miss into an auditable L2 signal.

    Observability only — never raises (iron rule: cost_tracker is not flow
    control), so even a logging-layer failure can't break the LLM call path.
    """
    try:
        logger.warning(
            f"[cost] {source}: provider returned no token usage for "
            f"model={model} call_type={call_type} — usage UNRECORDED"
        )
    except Exception:
        pass


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> dict:
    """
    Calculate cost for a single API call.

    ``input_tokens`` is the tokens billed at the FULL input rate. Cache reads
    and cache writes are separate buckets priced at their own rates — this
    mirrors Anthropic's three mutually-exclusive usage columns, where treating
    the three as one number overstates a cache-warm turn by roughly 10x.

    Unknown model → zeros, which is a real answer ("we do not know the rate"),
    not an error. Callers must still record the tokens: usage stays visible
    even when its price does not. ``model_pricing`` warns once per unknown id.

    Args:
        model: Model identifier as reported by the provider
        input_tokens: Full-rate input tokens
        output_tokens: Output tokens
        cache_read_tokens: Prompt-cache read tokens (typically 0.1x input)
        cache_creation_tokens: Prompt-cache write tokens (typically 1.25x input)

    Returns:
        {"input_cost", "output_cost", "cache_cost", "total_cost"}
    """
    from narranexus.platform.utils.model_pricing import price_for

    price = price_for(model)
    if price is None:
        return {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "cache_cost": 0.0,
            "total_cost": 0.0,
        }

    input_cost = input_tokens * price.input_per_token
    output_cost = output_tokens * price.output_per_token

    # A model with no published cache rate is not a model with free caching —
    # it is one whose cache tiers upstream does not record. Falling back to the
    # normal input rate keeps an unpriced cache read visible as spend; booking
    # it at 0 would make enabling caching look like it cut cost to nothing.
    write_rate = (
        price.cache_write_per_token
        if price.cache_write_per_token is not None
        else price.input_per_token
    )
    read_rate = (
        price.cache_read_per_token
        if price.cache_read_per_token is not None
        else price.input_per_token
    )
    cache_cost = cache_creation_tokens * write_rate + cache_read_tokens * read_rate

    return {
        "input_cost": input_cost,
        "output_cost": output_cost,
        "cache_cost": cache_cost,
        "total_cost": input_cost + output_cost + cache_cost,
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
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    num_turns: Optional[int] = None,
) -> None:
    """
    Calculate cost and persist a record to the database.

    Args:
        db: AsyncDatabaseClient instance
        agent_id: Agent that incurred the cost
        event_id: Associated event. None means "ask the ambient context" —
            helper call sites (llm_function / llm_stream) never know their
            turn, and hard-coding None there is what kept per-turn token
            figures counting only the main loop. An explicit id always wins;
            outside a turn the fallback is None too and the column stays NULL.
        call_type: "agent_loop" | "llm_function"
        model: Model identifier
        input_tokens: Input token count
        output_tokens: Output token count
        sdk_cost_usd: SDK-calculated cost (used as fallback when model is unknown)
        cache_read_tokens: Prompt-cache read tokens (defaults keep existing
            callers untouched; only agent_loop reports these today)
        cache_creation_tokens: Prompt-cache write tokens
        num_turns: Model calls within this run (None = framework didn't report)
    """
    # Fall back to the turn this context is attributed to. The var carries a
    # None default, so "nobody set one" reads back as None rather than raising
    # — no guard needed, and an unattributed row is the same NULL as before.
    if event_id is None:
        event_id = _cost_event_id.get()

    cost = calculate_cost(
        model, input_tokens, output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
    )
    # Prefer SDK-provided cost (most accurate, e.g. Claude SDK considers caching discounts).
    # Fall back to price-table calculation, then to $0 as last resort.
    if sdk_cost_usd and sdk_cost_usd > 0:
        final_cost = sdk_cost_usd
    elif cost["total_cost"] > 0:
        final_cost = cost["total_cost"]
    else:
        final_cost = 0.0

    # Resolve the owner attribution ONCE, up front, so it can be persisted onto
    # cost_records (making the row user-attributable without a fragile join to
    # agents.created_by). Reading the ContextVars must never break cost
    # tracking, so default to None on any failure — non-user / background calls
    # legitimately have neither.
    user_id: Optional[str] = None
    provider_source: Optional[str] = None
    provider_card_source: Optional[str] = None
    try:
        from narranexus.platform.agent_framework.api_config import (
            get_current_user_id,
            get_provider_card_source,
            get_provider_source,
        )
        user_id = get_current_user_id()
        provider_source = get_provider_source()
        provider_card_source = get_provider_card_source(call_type)
    except Exception:
        pass

    try:
        await db.insert("cost_records", {
            "agent_id": agent_id,
            "event_id": event_id,
            "call_type": call_type,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_cost_usd": final_cost,
            "user_id": user_id,
            "provider_source": provider_source,
            "provider_card_source": provider_card_source,
            "cache_read_input_tokens": cache_read_tokens or 0,
            "cache_creation_input_tokens": cache_creation_tokens or 0,
            "num_turns": num_turns,
        })
        logger.debug(
            f"Cost recorded: agent={agent_id} model={model} "
            f"tokens={input_tokens}+{output_tokens} cost=${final_cost:.6f}"
            f" cache={cache_read_tokens}r/{cache_creation_tokens}w"
            f"{f' turns={num_turns}' if num_turns is not None else ''}"
            f"{' (sdk)' if cost['total_cost'] == 0 and sdk_cost_usd else ''}"
        )
    except Exception as e:
        # Cost tracking failure should never block the main flow
        logger.exception(f"Failed to record cost: {e}")
