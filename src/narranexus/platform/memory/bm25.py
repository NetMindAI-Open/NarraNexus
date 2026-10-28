"""
@file_name: bm25.py
@author: NetMind.AI
@date: 2026-08-25
@description: Public seam for the shared BM25 ranking primitives.

The ONE BM25 implementation lives in `_memory_impl.retrieval` (memory recall
was its first consumer), but it long ago grew out-of-domain consumers —
narrative routing and the job repository rank with it, and the routing audit
replay asserts score reproduction bit-for-bit. Those consumers were importing
the private `_memory_impl` path directly, so nothing signalled to a `memory/`
refactor that these functions are a cross-domain CONTRACT (PR #361 review,
M2). This module is that signal: out-of-domain code imports from here, and a
change to these signatures is a change to the narrative bit-replay guarantee,
not a private refactor.

Nothing here is a second implementation — pure re-export.
"""

from narranexus.platform.memory._memory_impl.retrieval import (
    bm25_explain,
    bm25_rank,
    bm25_snippet,
    tokenize,
)

__all__ = ["bm25_explain", "bm25_rank", "bm25_snippet", "tokenize"]
