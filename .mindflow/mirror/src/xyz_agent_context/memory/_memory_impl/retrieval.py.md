---
code_file: src/xyz_agent_context/memory/_memory_impl/retrieval.py
last_verified: 2026-06-08
stub: false
---

## 2026-06-08 — recall relevance gate + CJK stopwords

`rank_recall` now gates on keyword relevance: for a NON-blank query it ranks ONLY the BM25 hits (recency/proof/salience reorder WITHIN that set), so a zero-overlap record can no longer ride its recency boost into results — that was the cross-topic leak (an outdoor query pulling back finance records when a kind held few candidates). A blank query keeps the documented recency fallback; a non-blank query that matches nothing returns empty (no recency-dump). `tokenize()` also stopwords high-frequency CJK function chars (的/这/个/是/我…) that the per-character unigram tokenizer otherwise turned into BM25 terms; content-bearing borderliners (对/在/有/为/中/上/下/里…) are deliberately kept so a term like 对账 keeps full weight. Shared by narrative routing too — routing verified still 3/3 with unchanged scores. Tests: `tests/memory/test_recall_relevance_gating.py` (7 cases).

# retrieval.py — unified Agent Memory

Vector-free retrieval primitives: BM25-lite, grep, RRF fusion, recency/proof/salience boosts, token-budget trim. Pure functions over a bounded candidate set.

Part of the unified memory system (`refactor/agent-memory`). The unified design covers data model, retrieval stack and migration
(author-local; the § numbers below cite its sections). Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
