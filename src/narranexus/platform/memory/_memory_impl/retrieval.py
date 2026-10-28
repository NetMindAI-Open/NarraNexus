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
import time
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import regex as _regex  # aliased: `regex` is also a grep_filter parameter name
from loguru import logger

from narranexus.platform.memory.record import MemoryRecord
from narranexus.platform.utils.timezone import utc_now

# ReDoS guards for the regex grep path (the pattern is agent/LLM-supplied and
# untrusted). Two bounds:
#   - a per-record match timeout, so any single catastrophic-backtracking
#     evaluation is interrupted;
#   - a per-REQUEST wall-clock deadline, computed ONCE in coordinator.grep_memory
#     and threaded down through engine.grep into grep_filter, so a whole
#     grep_memory request (which scans several memory kinds) shares ONE budget
#     rather than budget × num_kinds.
# Additionally, engine.grep runs the regex scan via run_in_executor so this
# CPU-bound work does NOT block the shared API event loop (the `regex` package
# releases the GIL while matching, so the offload is real). These are safety
# bounds on a SEARCH primitive, NOT an agent-loop ceiling (铁律 #14/#15 govern
# agent_loop, not a bounded search over a fixed candidate set).
_GREP_PER_MATCH_TIMEOUT_S = 0.25
_GREP_REQUEST_BUDGET_S = 2.0

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
def _bm25_term_contributions(
    query: str,
    items: "Sequence[Tuple[str, str]]",
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> Dict[str, Dict[str, float]]:
    """id → {query term: its addend in the BM25 sum}, in token order.

    The ONE definition of the arithmetic. `bm25_rank` sums these and
    `bm25_explain` sorts them, so a score and the evidence shown for that
    score can never describe different computations. That is not a stylistic
    preference: narrative routing already shipped a four-month-dead branch
    (`matched_content`) because a read side and a write side for the same fact
    lived in two files, and this module's own audit replay
    (`tests/narrative/test_routing_audit.py`) asserts score reproduction
    bit-for-bit — a second, "equivalent" copy of the formula is exactly how
    that guarantee dies quietly.

    Terms are omitted, not zero-filled: only query terms actually present in
    the document appear. A document with no hit is absent from the result,
    matching `bm25_rank`'s contract (every addend is strictly positive, so
    "has terms" and "scores above zero" are the same condition).
    """
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

    per_doc: Dict[str, Dict[str, float]] = {}
    for rid, toks in docs:
        if not toks:
            continue
        dl = len(toks)
        tf: Dict[str, int] = {}
        for t in toks:
            if t in q_terms:
                tf[t] = tf.get(t, 0) + 1
        contributions = {
            t: idf[t] * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
            for t, f in tf.items()
        }
        if contributions:
            per_doc[rid] = contributions
    return per_doc


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
    return {
        rid: sum(contributions.values())
        for rid, contributions in _bm25_term_contributions(
            query, items, k1=k1, b=b
        ).items()
    }


def bm25_explain(
    query: str,
    items: "Sequence[Tuple[str, str]]",
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> Dict[str, Tuple[float, List[Tuple[str, float]]]]:
    """Same ranking, decomposed: id → (score, [(term, contribution)] descending).

    A BM25 score is not self-describing. On real traffic a query like
    「帮我查一下明天上海的天气怎么样」 scored 10.67 against an unrelated
    meeting-notes narrative with 100% of the score coming from the
    request-frame characters 查/天/下/一/帮, while every topic-bearing
    character contributed zero — and the squashed form of that score reads as
    `0.91`. Ordering by contribution makes the difference between "matched on
    substance" and "matched on politeness" legible to whatever reads it next,
    and it means a truncated top-N keeps the discriminative terms.

    The score is returned alongside the terms, summed in the same order
    `bm25_rank` sums it, so it is bit-identical rather than merely close — a
    caller that wants both (narrative routing does: the gate reads the score,
    the judge reads the terms) must not have to choose between a second pass
    over the pool and a score that drifts in the last bits from the one the
    audit replays.

    Deliberately NOT folded into `bm25_rank`: memory recall calls that on
    every kind of every request and only wants the number. Separate entry
    point, shared arithmetic (`_bm25_term_contributions`).
    """
    return {
        rid: (
            sum(contributions.values()),
            sorted(contributions.items(), key=lambda kv: kv[1], reverse=True),
        )
        for rid, contributions in _bm25_term_contributions(
            query, items, k1=k1, b=b
        ).items()
    }


def bm25_snippet(
    text: str,
    terms: "Sequence[str]",
    *,
    window: int = 60,
    max_terms: int = 3,
    max_chars: int = 200,
) -> str:
    """Context windows around where `terms` first occur in `text`.

    Terms and score alone still leave the reader guessing WHERE a term landed
    — 「部署」 inside a topic name and 「部署」 buried in a frozen channel-
    wrapper prompt are very different evidence for the same term. Windows are
    merged when they overlap, truncated windows get an ellipsis, and the
    content budget is capped because this text is about to be spent as prompt
    tokens on the crowded-candidate path.

    Matching is case-insensitive (the tokenizer lowercases; the snippet quotes
    the original text).
    """
    if not text or not terms:
        return ""

    lowered = text.lower()
    spans: List[List[int]] = []
    for term in list(terms)[:max_terms]:
        pos = lowered.find(term.lower())
        if pos < 0:
            continue
        spans.append([max(0, pos - window), min(len(text), pos + len(term) + window)])
    if not spans:
        return ""

    spans.sort()
    merged: List[List[int]] = [spans[0]]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    parts: List[str] = []
    budget = max_chars
    for start, end in merged:
        if budget <= 0:
            break
        chunk = text[start:min(end, start + budget)]
        budget -= len(chunk)
        clipped_tail = (start + len(chunk)) < len(text)
        parts.append(
            ("…" if start > 0 else "") + chunk.strip() + ("…" if clipped_tail else "")
        )
    return " ".join(p for p in parts if p.strip("…"))


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
    deadline: Optional[float] = None,
) -> Tuple[List[MemoryRecord], bool]:
    """Exact substring (default) or regex match over `content_text`.
    Complements BM25: finds the literal token (an id, URL, exact phrase) that
    tokenized ranking can miss. Invalid regex falls back to substring.

    Returns ``(hits, truncated)``. ``truncated`` is True when the scan gave up
    early — the deadline passed or a record's match timed out — so the caller can
    tell "these are all the matches" apart from "we stopped looking" (a memory
    recall primitive must not let the LLM read a bounded-scan giveup as "I don't
    remember"). It is a CPU-bound sync function; engine.grep offloads the regex
    path via run_in_executor so it never blocks the shared event loop.

    The regex path uses the `regex` package with a per-match ``timeout=``, NOT
    stdlib ``re``: the pattern is agent/LLM-supplied and untrusted, and stdlib
    re is UNINTERRUPTIBLE — a catastrophic-backtracking pattern (`(a|aa)+$`-class)
    would pin a CPU core and, once grep is served over HTTP, wedge the shared API
    loop for every user. A record that times out is skipped (and flags
    truncation). ``deadline`` (a ``time.monotonic()`` value) is the shared
    per-REQUEST budget threaded from coordinator.grep_memory; if None a default
    per-call budget is used. Invalid pattern → substring fallback (unchanged)."""
    if regex:
        rflags = _regex.IGNORECASE if ignore_case else 0
        try:
            rx = _regex.compile(pattern, rflags)
        except _regex.error:
            pass  # fall through to substring
        else:
            out: List[MemoryRecord] = []
            truncated = False
            dl = deadline if deadline is not None else time.monotonic() + _GREP_REQUEST_BUDGET_S
            for r in records:
                if time.monotonic() > dl:
                    logger.warning("grep_filter: regex scan hit the budget; results truncated")
                    truncated = True
                    break
                try:
                    if rx.search(r.content_text or "", timeout=_GREP_PER_MATCH_TIMEOUT_S):
                        out.append(r)
                except TimeoutError:
                    # A timed-out record is a POTENTIAL miss, not a known non-match.
                    logger.warning("grep_filter: regex timed out on one record; treated as truncation")
                    truncated = True
                    continue
            return out, truncated
    needle = pattern.lower() if ignore_case else pattern
    hits = [r for r in records if needle in ((r.content_text or "").lower() if ignore_case else (r.content_text or ""))]
    return hits, False


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
