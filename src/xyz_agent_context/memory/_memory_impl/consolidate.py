"""
@file_name: consolidate.py
@author: NetMind.AI
@date: 2026-06-03
@description: Consolidation mechanism — distil raw memory units into evolving,
             deduplicated, evidence-bearing higher-level records (observations
             / summaries). This is the "learning, not just remembering" core
             (design §7), adapted from Hindsight — and it is fully LLM + SQL,
             with NO vectors.

The 9 processing rules below are the SOTA secret: prefer UPDATE over CREATE,
one facet per observation, state-changes update concisely, NO arithmetic
inference, preserve history, reconcile contradictions by enriching the
evolution text rather than overwriting. The prompt is the default; a
MemoryKindSpec may override it via `consolidate_prompt`.

Resilience: a failing LLM batch is bisected down to size 1; a single unit that
still fails is skipped (never blocks the scope) — mirrors the long-running
service discipline in CLAUDE.md.

EXCEPT for systemic errors (auth / quota / connection / server): those are
independent of fact content, so bisecting cannot help and dropping facts on
them destroys memory — the 2026-06-11 P0 (cloud worker had no credentials;
every batch 401'd, bisected to single facts, and dropped 4599 of them).
Systemic errors now RAISE so the worker isolates the scope with its facts
intact.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, List, Optional, Sequence

from loguru import logger
from pydantic import BaseModel, Field

import openai

from xyz_agent_context.agent_framework.helper_sdk import get_helper_sdk
from xyz_agent_context.memory.record import MemoryRecord
from xyz_agent_context.utils.timezone import utc_now


class SystemicLLMError(RuntimeError):
    """An LLM failure independent of fact content (auth / quota /
    connection / server). Bisecting cannot help; the caller must fail the
    whole scope and PRESERVE the facts."""


_SYSTEMIC_STATUS = {401, 403, 407, 429}


@lru_cache(maxsize=1)
def _systemic_exc_types() -> tuple[type[BaseException], ...]:
    """Client exception classes meaning "smaller batches can't help".

    Covers BOTH provider SDKs because the helper SDK is openai-shaped OR
    anthropic-shaped depending on the user's helper slot protocol
    (``get_helper_sdk`` → AnthropicHelperSDK for the anthropic leg). The
    connection-class errors (``APIConnectionError``) carry NO
    ``status_code`` — a request that died before any HTTP response — so the
    status check below cannot catch them; only this isinstance can. Missing
    the anthropic variants is exactly the gap that re-opens the 2026-06-11
    bisect-drop fact loss for anthropic-helper users.

    anthropic is lazy-imported: an openai-only deploy may not ship it.
    Cached because the class set is fixed for the process lifetime.
    """
    types: list[type[BaseException]] = [
        openai.AuthenticationError, openai.PermissionDeniedError,
        openai.RateLimitError, openai.APIConnectionError,
        openai.InternalServerError,
    ]
    try:
        import anthropic
        types += [
            anthropic.AuthenticationError, anthropic.PermissionDeniedError,
            anthropic.RateLimitError, anthropic.APIConnectionError,
            anthropic.InternalServerError,
        ]
    except ImportError:
        pass
    return tuple(types)


def _is_systemic_llm_error(e: BaseException) -> bool:
    """True for errors where retrying smaller batches cannot succeed.
    Walks the cause/context chain because the Agents SDK path may wrap the
    underlying client error."""
    systemic_types = _systemic_exc_types()
    seen: set[int] = set()
    cur: Optional[BaseException] = e
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, systemic_types):
            return True
        status = getattr(cur, "status_code", None)
        if isinstance(status, int) and (status in _SYSTEMIC_STATUS or status >= 500):
            return True
        cur = cur.__cause__ or cur.__context__
    return False

DEFAULT_CONSOLIDATION_PROMPT = """\
You maintain an agent's long-term memory as a set of OBSERVATIONS — concise,
deduplicated beliefs distilled from raw facts. Given the EXISTING observations
(numbered) and a batch of NEW facts, return a plan of creates/updates/deletes.

PROCESSING RULES (follow strictly):
1. PREFER UPDATE OVER CREATE. If a new fact concerns the same canonical
   entity/event/preference as an existing observation, UPDATE it and add the
   evidence — do NOT create a near-duplicate sibling.
2. ONE OBSERVATION PER DISTINCT FACET. Each observation tracks exactly one
   fact/preference/relationship. Split unrelated facets into separate items.
3. STATE CHANGES — UPDATE CONCISELY. "sold X", "moved to Y", "switched to Z"
   → update the relevant observation to reflect the CURRENT state, keeping a
   short trace of the change with its date when known.
4. NO COMPUTATION. Never do arithmetic or logical inference across facts
   (do not turn "I have 2 dogs" + "my dog Rex" into "3 dogs").
5. PRESERVE HISTORY. Observations recording significant events are never
   deleted. Delete ONLY true duplicates or clear errors — be very conservative.
6. RECONCILE CONTRADICTIONS BY ENRICHING. When a new fact contradicts an
   observation, do not blindly overwrite — synthesize an evolution-aware text,
   e.g. "Works at Meta (previously thought to work at Google)".
7. Keep each observation a single clear sentence in third person.
8. Reference existing observations by their given index for updates/deletes.
9. If nothing meaningfully changes, return empty creates/updates/deletes.
"""


class _ObsCreate(BaseModel):
    text: str = Field(description="A single concise observation sentence.")
    source_fact_indices: List[int] = Field(default_factory=list, description="Indices of NEW facts supporting this.")


class _ObsUpdate(BaseModel):
    target_index: int = Field(description="Index of the EXISTING observation to update.")
    text: str = Field(description="The rewritten, evolution-aware observation text.")
    source_fact_indices: List[int] = Field(default_factory=list)


class _ConsolidationPlan(BaseModel):
    creates: List[_ObsCreate] = Field(default_factory=list)
    updates: List[_ObsUpdate] = Field(default_factory=list)
    deletes: List[int] = Field(default_factory=list, description="Indices of EXISTING observations to remove.")


def _render_context(existing: Sequence[MemoryRecord], new_facts: Sequence[MemoryRecord]) -> str:
    ex = "\n".join(f"[{i}] {r.content_text}" for i, r in enumerate(existing)) or "(none)"
    nf = "\n".join(f"<{i}> {r.content_text}" for i, r in enumerate(new_facts)) or "(none)"
    return f"EXISTING observations:\n{ex}\n\nNEW facts:\n{nf}"


async def consolidate(
    repo,
    *,
    agent_id: str,
    scope_type: str,
    scope_id: str,
    kind: str,
    new_facts: Sequence[MemoryRecord],
    existing: Sequence[MemoryRecord],
    prompt: Optional[str] = None,
    sdk: Optional[Any] = None,
    db: Optional[Any] = None,
) -> int:
    """Run one consolidation pass for a scope. `repo` is the MemoryRepository
    for the CONSOLIDATED kind (e.g. observation). Returns the number of records
    written/changed. Bisects on LLM failure; never raises for a content error.

    `db` is threaded to the LLM call for explicit cost accounting so the pass
    records tokens even when no ambient cost context is set (double insurance
    on top of the worker's set_cost_context; module H).
    """
    facts = [f for f in new_facts if f.content_text.strip()]
    if not facts:
        return 0
    sdk = sdk or get_helper_sdk()
    instructions = prompt or DEFAULT_CONSOLIDATION_PROMPT

    plan = await _plan_with_bisect(sdk, instructions, facts, existing, agent_id, db)
    if plan is None:
        return 0

    changed = 0
    now = utc_now()

    # deletes → tombstone (history preserved, never hard-delete)
    for idx in plan.deletes:
        if 0 <= idx < len(existing):
            await repo.tombstone(existing[idx].record_id)
            changed += 1

    # updates → rewrite text, snapshot history, bump evidence
    for upd in plan.updates:
        if not (0 <= upd.target_index < len(existing)):
            continue
        target = existing[upd.target_index]
        target.history.append({"text": target.content_text, "changed_at": now.isoformat()})
        target.content_text = upd.text
        target.source_ids = list(dict.fromkeys(target.source_ids + _facts_to_ids(facts, upd.source_fact_indices)))
        target.proof_count += len(upd.source_fact_indices) or 1
        target.updated_at = now
        await repo.upsert(target)
        changed += 1

    # creates → new consolidated record
    for cr in plan.creates:
        src = _facts_to_ids(facts, cr.source_fact_indices)
        rec = MemoryRecord(
            agent_id=agent_id, scope_type=scope_type, scope_id=scope_id, kind=kind,
            content_text=cr.text, source_ids=src, proof_count=max(1, len(src)),
            tags=_inherit_tags(facts, cr.source_fact_indices),
        )
        await repo.upsert(rec)
        changed += 1

    return changed


def _facts_to_ids(facts: Sequence[MemoryRecord], indices: Sequence[int]) -> List[str]:
    return [facts[i].record_id for i in indices if 0 <= i < len(facts)]


def _inherit_tags(facts: Sequence[MemoryRecord], indices: Sequence[int]) -> List[str]:
    """Tags are inherited from source facts by the algorithm, NOT chosen by the
    LLM (Hindsight safety boundary — keeps tag space controlled)."""
    tags: List[str] = []
    for i in indices:
        if 0 <= i < len(facts):
            tags.extend(facts[i].tags)
    return list(dict.fromkeys(tags))


async def _plan_with_bisect(
    sdk: Any,
    instructions: str,
    facts: Sequence[MemoryRecord],
    existing: Sequence[MemoryRecord],
    agent_id: str,
    db: Optional[Any] = None,
) -> Optional[_ConsolidationPlan]:
    """Call the LLM for a plan; on failure bisect the fact batch and merge the
    sub-plans. A single fact that still fails is dropped (logged), never fatal."""
    try:
        result = await sdk.llm_function(
            instructions=instructions,
            user_input=_render_context(existing, facts),
            output_type=_ConsolidationPlan,
            agent_id=agent_id,
            db=db,  # explicit — record even when no ambient cost ctx
        )
        return result.final_output  # type: ignore[no-any-return]
    except Exception as e:  # noqa: BLE001 — bisect-and-isolate is the policy
        if _is_systemic_llm_error(e):
            # Content-independent failure: bisecting cannot help, and
            # dropping facts on it destroys memory. Fail the scope loudly;
            # the worker isolates it with every fact preserved.
            logger.error(
                f"[memory.consolidate] systemic LLM failure (agent={agent_id}) — "
                f"failing the scope, facts preserved: {e}"
            )
            raise SystemicLLMError(str(e)) from e
        if len(facts) <= 1:
            logger.warning(f"[memory.consolidate] dropping unconsolidatable fact: {e}")
            return None
        mid = len(facts) // 2
        left = await _plan_with_bisect(sdk, instructions, facts[:mid], existing, agent_id, db)
        right = await _plan_with_bisect(sdk, instructions, facts[mid:], existing, agent_id, db)
        return _merge_plans(left, right, mid)


def _merge_plans(left: Optional[_ConsolidationPlan], right: Optional[_ConsolidationPlan], offset: int) -> _ConsolidationPlan:
    merged = left or _ConsolidationPlan()
    if right:
        # right's source_fact_indices are relative to the right half — re-base
        for cr in right.creates:
            cr.source_fact_indices = [i + offset for i in cr.source_fact_indices]
        for up in right.updates:
            up.source_fact_indices = [i + offset for i in up.source_fact_indices]
        merged.creates += right.creates
        merged.updates += right.updates
        merged.deletes += right.deletes
    return merged
