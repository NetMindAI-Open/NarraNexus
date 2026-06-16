"""
@file_name: test_recall_relevance_gating.py
@author: NetMind.AI
@date: 2026-06-09
@description: rank_recall must gate on keyword relevance for non-blank queries.

Regression for the cross-topic leak (unified-memory E2E PROBE 2, 2026-06-08):
a record with ZERO query-term overlap was surfacing purely on its recency boost
when its kind held few candidates — so an outdoor query pulled back finance
records. The fix: a non-blank query restricts candidates to positive-BM25 hits;
recency/proof/salience only reorder WITHIN the relevant set. A blank query keeps
the documented recency fallback.
"""
from datetime import datetime, timezone, timedelta

from xyz_agent_context.memory.record import MemoryRecord
from xyz_agent_context.memory._memory_impl.retrieval import rank_recall

NOW = datetime.now(timezone.utc)


def _rec(rid: str, text: str, *, age_days: float = 0.0, salience: float = 0.0) -> MemoryRecord:
    ts = NOW - timedelta(days=age_days)
    return MemoryRecord(record_id=rid, agent_id="a", kind="x", content_text=text,
                        created_at=ts, last_used_at=ts, salience=salience)


def test_zero_overlap_record_excluded_even_if_most_recent():
    """The leak: a brand-new, zero-overlap record must NOT outrank (or even
    appear alongside) a relevant-but-older one for a non-blank query."""
    relevant = _rec("r_match", "雨崩 徒步 装备 冲锋衣 高反", age_days=30.0)
    # Newest record, high salience, but ZERO shared terms with an outdoor query.
    leak = _rec("r_finance", "Apex 并购 对账 差额 银行流水", age_days=0.0, salience=1.0)

    out = rank_recall([leak, relevant], "雨崩徒步要带什么装备防高反", limit=10)
    ids = [r.record_id for r in out]

    assert "r_match" in ids, "relevant record must be recalled"
    assert "r_finance" not in ids, "zero-overlap record must be gated out"


def test_blank_query_keeps_recency_fallback():
    """Documented graceful degradation: a blank query still returns records in
    recency order (newest first), since there is no relevance signal to gate on."""
    older = _rec("r_old", "anything", age_days=10.0)
    newer = _rec("r_new", "whatever", age_days=1.0)

    out = rank_recall([older, newer], "   ", limit=10)
    ids = [r.record_id for r in out]

    assert ids == ["r_new", "r_old"], "blank query → recency order over all records"


def test_nonblank_zero_match_returns_empty():
    """A non-blank query that matches nothing must return empty — NOT a
    recency-ordered dump of irrelevant records."""
    a = _rec("r_a", "登山 露营", age_days=1.0)
    b = _rec("r_b", "对账 财务", age_days=0.0)

    out = rank_recall([a, b], "量子纠缠 黑洞 霍金辐射", limit=10)

    assert out == [], "no keyword hit → empty, not a recency dump"


def test_relevant_set_still_reordered_by_recency():
    """Within the relevant set, recency/salience still reorder: two equally
    keyword-matching records should order newest-first."""
    old_hit = _rec("r_old", "雨崩 徒步 装备", age_days=60.0)
    new_hit = _rec("r_new", "雨崩 徒步 装备", age_days=0.0)

    out = rank_recall([old_hit, new_hit], "雨崩 徒步 装备", limit=10)
    ids = [r.record_id for r in out]

    assert ids[0] == "r_new", "among relevant hits, recency still breaks ties"
    assert set(ids) == {"r_old", "r_new"}


# ── CJK function-char noise gate ──────────────────────────────────────────────
# The per-character CJK tokenizer made high-frequency function characters
# (的/了/这/个/么/我/是…) act as BM25 terms, so records sharing only those with a
# query leaked through the relevance gate (E2E follow-up, 2026-06-08: an asyncio
# record surfaced for an outdoor query via shared 的/记/录). Stopword-filtering
# those chars removes the spurious overlap while content chars still match.
from xyz_agent_context.memory._memory_impl.retrieval import tokenize  # noqa: E402


def test_cjk_function_chars_are_stopworded():
    toks = set(tokenize("我的这个是不是装备"))
    for fc in ("的", "这", "个", "是", "我"):
        assert fc not in toks, f"function char {fc!r} must be stopworded"
    # content chars survive
    assert "装" in toks and "备" in toks


def test_content_words_still_tokenize():
    # ASCII + Chinese content terms must be untouched.
    toks = tokenize("asyncio 死锁 雨崩 对账")
    for c in ("asyncio", "死", "锁", "雨", "崩", "对", "账"):
        assert c in toks


def test_function_char_only_overlap_is_gated():
    """A record sharing ONLY function chars with the query must not be recalled."""
    relevant = _rec("r_match", "雨崩 徒步 装备", age_days=20.0)
    # Shares 的/这/个/记/录-style function noise but no outdoor content.
    noise = _rec("r_noise", "这个 asyncio 的记录是怎么定位的", age_days=0.0, salience=1.0)
    out = rank_recall([noise, relevant], "雨崩徒步装备这个怎么带", limit=10)
    ids = [r.record_id for r in out]
    assert "r_match" in ids
    assert "r_noise" not in ids, "function-char-only overlap must be gated out"
