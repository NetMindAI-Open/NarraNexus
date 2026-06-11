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

from dataclasses import dataclass
from typing import List, Optional, Sequence

from xyz_agent_context.memory.engine import MemoryEngine
from xyz_agent_context.memory.record import MemoryRecord
from xyz_agent_context.memory.spec import all_kinds
from xyz_agent_context.memory._memory_impl import retrieval as _retrieval


@dataclass(frozen=True)
class MemoryHit:
    """A recalled record tagged with the kind it came from (provenance for the
    agent: it knows whether a hit is a chat line, an entity, an observation…)."""
    record: MemoryRecord
    kind: str


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
    ) -> List[MemoryHit]:
        """Cross-kind exact/regex search over content_text — the literal-match
        complement to `remember`."""
        kinds = list(kinds or all_kinds())
        out: List[MemoryHit] = []
        for kind in kinds:
            for rec in await self.engine.grep(kind, pattern, regex=regex):
                out.append(MemoryHit(record=rec, kind=kind))
                if len(out) >= limit:
                    return out
        return out
