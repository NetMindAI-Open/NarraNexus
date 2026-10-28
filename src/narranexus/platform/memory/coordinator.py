"""
@file_name: coordinator.py
@author: NetMind.AI
@date: 2026-06-03
@description: MemoryCoordinator — the cross-kind "回忆" facade (design §6.3).

`remember()` fans a query out across every memory kind and fuses the results,
so recall draws from all channels at once instead of one aspect in isolation —
this is the abstraction behind the agent-facing `remember` / `grep_memory`
tools. A thin Facade over MemoryEngine; carries no logic the engine doesn't,
only orchestration + RRF fusion + a shared token budget.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from narranexus.platform.memory.engine import MemoryEngine
from narranexus.platform.memory.record import MemoryRecord
from narranexus.platform.memory.spec import all_kinds
from narranexus.platform.memory._memory_impl import retrieval as _retrieval


@dataclass(frozen=True)
class MemoryHit:
    """A recalled record tagged with the kind it came from (provenance for the
    agent: it knows whether a hit is a chat line, an entity, an observation…)."""
    record: MemoryRecord
    kind: str


def format_memory_hits(hits: Sequence[MemoryHit]) -> List[dict]:
    """Render recalled hits for the agent — text + provenance (kind, when, tags).

    The ONE canonical rendering of a MemoryHit. Every surface that returns
    recalled memory to an agent imports this so all paths agree by
    construction, not by convention: the `remember` / `grep_memory` MCP tools,
    the AgentDataStore DirectStore, and the backend `/memory/*` routes (the
    HttpStore path). Do not fork a copy — a divergence here silently hands the
    local and cloud agent different dict keys for the same underlying hit.

    Projection kinds (job/event/narrative/bus) carry a `source` pointer back to
    the live original so the agent can two-step Search→Fetch (e.g. a job hit's
    {"kind":"job","id":...} → job_retrieval_by_id). Self-contained kinds
    (observation/entity) omit it — the snippet is the whole thing.
    """
    out: List[dict] = []
    for h in hits:
        r = h.record
        item: dict = {
            "kind": h.kind,
            "memory": r.content_text,
            "when": (r.created_at.isoformat() if r.created_at else None),
            "tags": r.tags,
        }
        if r.source_ref:
            item["source"] = r.source_ref
        out.append(item)
    return out


class MemoryCoordinator:
    def __init__(self, engine: MemoryEngine):
        self.engine = engine

    async def remember(
        self,
        query: str,
        *,
        kinds: Optional[Sequence[str]] = None,
        per_kind_limit: int = 12,
        limit: int = 20,
        token_budget: int = 2000,
    ) -> List[MemoryHit]:
        """Cross-kind ranked recall. Each kind ranks its own candidates
        (BM25 + boosts), then RRF fuses across kinds and a shared token budget
        trims the final set."""
        kinds = list(kinds or all_kinds())
        pool: dict[str, MemoryHit] = {}
        rank_lists: List[List[str]] = []
        for kind in kinds:
            hits = await self.engine.recall(kind, query, limit=per_kind_limit)
            rank_lists.append([h.record_id for h in hits])
            for h in hits:
                pool[h.record_id] = MemoryHit(record=h, kind=kind)

        fused = _retrieval.rrf(rank_lists)
        ordered = sorted(fused, key=fused.get, reverse=True)  # type: ignore[arg-type]

        out: List[MemoryHit] = []
        spent = 0
        for rid in ordered:
            hit = pool[rid]
            cost = _retrieval.est_tokens(hit.record.content_text)
            if out and spent + cost > token_budget:
                break
            out.append(hit)
            spent += cost
            if len(out) >= limit:
                break
        return out

    async def grep_memory(
        self,
        pattern: str,
        *,
        kinds: Optional[Sequence[str]] = None,
        regex: bool = False,
        limit: int = 50,
    ) -> Tuple[List[MemoryHit], bool]:
        """Cross-kind exact/regex search over content_text — the literal-match
        complement to `remember`. Returns ``(hits, truncated)``; ``truncated`` is
        True when the regex scan gave up early (budget spent or a record timed
        out) so the caller doesn't present a partial result as complete."""
        kinds = list(kinds or all_kinds())
        # ONE wall-clock budget for the whole request, shared across kinds — so a
        # multi-kind regex grep can't accumulate budget × num_kinds of CPU. Only
        # the regex path is bounded/offloaded (substring is cheap).
        deadline = time.monotonic() + _retrieval._GREP_REQUEST_BUDGET_S if regex else None
        out: List[MemoryHit] = []
        truncated = False
        for kind in kinds:
            recs, kind_truncated = await self.engine.grep(
                kind, pattern, regex=regex, deadline=deadline,
            )
            truncated = truncated or kind_truncated
            for rec in recs:
                out.append(MemoryHit(record=rec, kind=kind))
                if len(out) >= limit:
                    return out, truncated
            if deadline is not None and time.monotonic() > deadline:
                # Budget spent — the remaining kinds go unscanned, so this is partial.
                truncated = True
                break
        return out, truncated
