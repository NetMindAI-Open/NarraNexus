"""
@file_name: retrieval.py
@author: NetMind.AI
@date: 2026-06-03
@description: Vector-free retrieval primitives for the memory system.

Pure, composable functions — no embeddings, no DB. Operate over an already
scope-filtered candidate list of MemoryRecord (per-(agent,scope) memory is
bounded, so ranking in Python is cheap and dialect-agnostic).

The stack (design §6):
  candidates → BM25-lite (ranked fuzzy) | grep (exact/regex)
            → RRF fusion across rankers
            → recency / proof_count / salience boosts
            → token-budget trim

"Semantic" understanding is intentionally NOT here — it moves up to the LLM
reading the top candidates (the recall caller). BM25 + grep cast the net.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Sequence, Tuple

from xyz_agent_context.memory.record import MemoryRecord
from xyz_agent_context.utils.timezone import utc_now

# Tokenizer: ASCII alphanumeric runs (words) PLUS individual CJK characters.
# NarraNexus content is heavily Chinese, where there are no spaces between
# words — splitting CJK into per-character unigrams lets BM25 match Chinese
# queries against Chinese content (an ASCII-only `[a-z0-9]+` would drop all
# Chinese and silently return nothing). CJK range covers common Han + ext-A +
# compatibility + Japanese kana.
_WORD = re.compile(
    r"[a-z0-9]+|[぀-ヿ㐀-䶿一-鿿豈-﫿]"
)
# CJK function-char stopwords. Per-character unigram tokenization turns
# high-frequency particles / pronouns / conjunctions into BM25 terms, so two
# unrelated records sharing only these (的/这/个/是…) score a spurious overlap
# that survives the relevance gate. Filtering them sharpens both recall and
# narrative routing (shared tokenizer). DELIBERATELY CONSERVATIVE: only clearly
# non-discriminative function chars — content-bearing borderliners (对/在/有/为/
# 中/上/下/里…) are intentionally left IN so a term like 对账 keeps full weight.
_CJK_STOPWORDS = frozenset(
    "的了着过地得之们我你他她它个这那此其谁么什怎"
    "是和与或跟把被让给也都又还就而且并但却则"
    "吗呢吧啊呀嘛哦噢呐啦"
)
# Rough token estimate (chars/4) — good enough for budgeting, avoids pulling
# in a tokenizer just to trim a recall set.
_CHARS_PER_TOKEN = 4


def tokenize(text: str) -> List[str]:
    return [t for t in _WORD.findall((text or "").lower()) if t not in _CJK_STOPWORDS]


def est_tokens(text: str) -> int:
    return max(1, len(text or "") // _CHARS_PER_TOKEN)


# ── BM25-lite ──────────────────────────────────────────────────────────────
def bm25_rank(
    query: str,
    items: "Sequence[Tuple[str, str]]",
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> Dict[str, float]:
    """Generic Okapi BM25 over (id, text) pairs. IDF is computed on the
    candidate set itself (no global index). Returns id → score; ids with no
    query-term hit are omitted. Reused for both memory recall and narrative
    routing (so both share one ranking implementation)."""
    q_terms = set(tokenize(query))
    if not q_terms or not items:
        return {}

    docs = [(rid, tokenize(text)) for rid, text in items]
    n = len(docs)
    avgdl = sum(len(toks) for _, toks in docs) / n or 1.0

    df: Dict[str, int] = {t: 0 for t in q_terms}
    for _, toks in docs:
        for t in q_terms & set(toks):
            df[t] += 1
    idf = {t: math.log(1 + (n - df_t + 0.5) / (df_t + 0.5)) for t, df_t in df.items()}

    scores: Dict[str, float] = {}
    for rid, toks in docs:
        if not toks:
            continue
        dl = len(toks)
        tf: Dict[str, int] = {}
        for t in toks:
            if t in q_terms:
                tf[t] = tf.get(t, 0) + 1
        s = sum(idf[t] * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl)) for t, f in tf.items())
        if s > 0:
            scores[rid] = s
    return scores


def bm25_scores(
    query: str,
    records: Sequence[MemoryRecord],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> Dict[str, float]:
    """BM25 over MemoryRecords' content_text — thin wrapper over bm25_rank."""
    return bm25_rank(query, [(r.record_id, r.content_text) for r in records], k1=k1, b=b)


# ── grep ─────────────────────────────────────────────────────────────────
def grep_filter(
    records: Iterable[MemoryRecord],
    pattern: str,
    *,
    regex: bool = False,
    ignore_case: bool = True,
) -> List[MemoryRecord]:
    """Exact substring (default) or regex match over `content_text`.
    Complements BM25: finds the literal token (an id, URL, exact phrase) that
    tokenized ranking can miss. Invalid regex falls back to substring."""
    flags = re.IGNORECASE if ignore_case else 0
    if regex:
        try:
            rx = re.compile(pattern, flags)
            return [r for r in records if rx.search(r.content_text or "")]
        except re.error:
            pass  # fall through to substring
    needle = pattern.lower() if ignore_case else pattern
    return [r for r in records if needle in ((r.content_text or "").lower() if ignore_case else (r.content_text or ""))]


# ── RRF fusion ─────────────────────────────────────────────────────────────
def rrf(rank_lists: Sequence[Sequence[str]], *, k: int = 60) -> Dict[str, float]:
    """Reciprocal Rank Fusion over several ranked id-lists. Rank-based (no
    score normalization), so heterogeneous rankers (BM25, recency, …) combine
    robustly."""
    fused: Dict[str, float] = {}
    for ranking in rank_lists:
        for rank, rid in enumerate(ranking):
            fused[rid] = fused.get(rid, 0.0) + 1.0 / (k + rank + 1)
    return fused


# ── boosts ─────────────────────────────────────────────────────────────────
def recency_boost(record: MemoryRecord, now: datetime, *, half_life_days: float = 14.0) -> float:
    """Exponential decay in [≈0,1] on age since last use (or creation)."""
    ref = record.last_used_at or record.created_at
    if ref is None:
        return 0.5
    age_days = max(0.0, (now - ref).total_seconds() / 86400.0)
    return 0.5 ** (age_days / half_life_days)


def proof_boost(record: MemoryRecord) -> float:
    """Diminishing-returns confidence from evidence count → [0,1)."""
    return 1.0 - 1.0 / (1.0 + record.proof_count)


# ── recall orchestrator ─────────────────────────────────────────────────────
def rank_recall(
    records: Sequence[MemoryRecord],
    query: str,
    *,
    limit: int | None = None,
    token_budget: int | None = None,
    w_recency: float = 0.5,
    w_proof: float = 0.3,
    w_salience: float = 0.2,
) -> List[MemoryRecord]:
    """Rank a candidate set for `recall`: BM25 relevance fused with recency /
    proof / salience boosts, then trimmed to a count and/or token budget.

    A blank query degrades gracefully to recency order (the §6.4 fallback:
    "show the most recent" beats "found nothing")."""
    if not records:
        return []
    now = utc_now()

    relevance = bm25_scores(query, records)

    # Relevance gate. bm25_scores omits zero-overlap records, so `relevance`
    # holds exactly the keyword hits. For a non-blank query we must rank ONLY
    # those hits — otherwise a zero-overlap record rides its recency boost into
    # the result (the cross-topic leak: an outdoor query pulling back finance
    # records when a kind held few candidates). recency/proof/salience are for
    # reordering WITHIN the relevant set, never for resurrecting irrelevant rows.
    if not relevance:
        # No keyword hit. Distinguish a blank query (no terms → documented
        # recency fallback) from a non-blank miss (genuinely nothing relevant —
        # return empty rather than recency-dumping irrelevant records).
        if tokenize(query):
            return []
        ordered = sorted(records, key=lambda r: recency_boost(r, now), reverse=True)
        return _trim(ordered, limit, token_budget)

    records = [r for r in records if r.record_id in relevance]
    by_relevance = sorted(relevance, key=relevance.get, reverse=True)  # type: ignore[arg-type]
    by_recency = sorted(records, key=lambda r: recency_boost(r, now), reverse=True)

    fused = rrf([by_relevance, [r.record_id for r in by_recency]])

    def final(r: MemoryRecord) -> float:
        return (
            fused.get(r.record_id, 0.0)
            + w_recency * recency_boost(r, now)
            + w_proof * proof_boost(r)
            + w_salience * min(1.0, r.salience)
        )

    ordered = sorted(records, key=final, reverse=True)
    return _trim(ordered, limit, token_budget)


def _trim(records: List[MemoryRecord], limit: int | None, token_budget: int | None) -> List[MemoryRecord]:
    if limit is not None:
        records = records[:limit]
    if token_budget is None:
        return records
    out, spent = [], 0
    for r in records:
        cost = est_tokens(r.content_text)
        if spent + cost > token_budget and out:
            break
        out.append(r)
        spent += cost
    return out
