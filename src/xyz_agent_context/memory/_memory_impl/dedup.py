"""
@file_name: dedup.py
@author: NetMind.AI
@date: 2026-06-03
@description: Deterministic dedup funnel + bi-temporal supersession arbitration.

Both are pure, vector-free mechanisms (graphiti范式, design §9.2/§9.3). The
LLM steps they hand off to — the tie-break when fuzzy-ambiguous, and the
contradiction detection — are POLICY supplied by the kind's MemoryKindSpec;
this module owns only the deterministic parts so they can be unit-tested and
reused across kinds.

This is the non-vector replacement for the entity dedup that regressed when
embeddings were removed from Social on 2026-05-27 ("Bob" vs "Robert").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from xyz_agent_context.memory.record import MemoryRecord

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Default funnel thresholds. Fuzzy is deliberately high — when in doubt the
# funnel reports AMBIGUOUS and lets the LLM decide, because a false merge
# (two people collapsed into one) is far costlier than a false split.
EXACT = 1.0
FUZZY_THRESHOLD = 0.85


def normalize(text: str) -> str:
    """Lowercase + collapse non-alphanumerics → canonical key for exact match."""
    return _NON_ALNUM.sub(" ", (text or "").lower()).strip()


def _shingles(text: str) -> set[str]:
    return set(normalize(text).split())


def jaccard(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


@dataclass(frozen=True)
class DedupResult:
    """Funnel verdict. `decision` is one of:
    - "exact"     : unambiguous match → caller updates `match`
    - "ambiguous" : `candidates` need an LLM tie-break (engine uses spec prompt)
    - "none"      : no plausible match → caller creates new
    """
    decision: str
    match: Optional[MemoryRecord] = None
    candidates: Tuple[MemoryRecord, ...] = ()


def funnel(
    key: str,
    existing: Sequence[MemoryRecord],
    *,
    key_of: "callable[[MemoryRecord], str]",
    fuzzy_threshold: float = FUZZY_THRESHOLD,
) -> DedupResult:
    """Deterministic dedup against `existing`.

    `key_of(record)` extracts the comparable key (e.g. entity name) from each
    existing record. Exact-normalized match wins outright; otherwise records
    above the fuzzy threshold are returned for an LLM tie-break; otherwise none.
    """
    norm_key = normalize(key)
    if not norm_key:
        return DedupResult("none")

    exact = [r for r in existing if normalize(key_of(r)) == norm_key]
    if len(exact) == 1:
        return DedupResult("exact", match=exact[0])
    if len(exact) > 1:
        return DedupResult("ambiguous", candidates=tuple(exact))

    fuzzy = sorted(
        ((jaccard(key, key_of(r)), r) for r in existing),
        key=lambda t: t[0],
        reverse=True,
    )
    near = tuple(r for score, r in fuzzy if score >= fuzzy_threshold)
    if near:
        return DedupResult("ambiguous", candidates=near)
    return DedupResult("none")


def arbitrate_supersession(
    new_valid_at: Optional[datetime],
    contradicted: Sequence[MemoryRecord],
) -> List[MemoryRecord]:
    """Given records an LLM judged contradicted by a newer fact, return those
    that should be tombstoned. A record is superseded only if it became true
    no later than the new fact (the new fact genuinely replaces it); a record
    that became true *after* the new fact is the more current truth and is
    left alone (the caller may instead tombstone the new one). When timing is
    unknown, supersede — the LLM already判定 contradiction.
    """
    if new_valid_at is None:
        return list(contradicted)
    out: List[MemoryRecord] = []
    for r in contradicted:
        if r.valid_at is None or r.valid_at <= new_valid_at:
            out.append(r)
    return out
