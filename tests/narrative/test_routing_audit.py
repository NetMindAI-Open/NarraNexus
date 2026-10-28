"""
@file_name: test_routing_audit.py
@date: 2026-08-07
@description: The routing audit must be sufficient for EXACT offline replay.

Why this file exists
====================
Narrative routing decisions were previously written only to a ProgressMessage
and loguru — nothing reached the database (incident lesson #5: docker logs
rotate, a grep only finds what you thought to search for). With no persisted
decision there is no denominator, so "we improved routing accuracy by X%" had
nothing to measure against.

The trap this pins down is subtler than "we forgot to log it". A naive audit
that stores only narrative IDs and scores CANNOT be replayed, because
``bm25_rank`` computes IDF *and* avgdl over the candidate set it is handed —
so the score of a candidate depends on every other document in the pool, and
on the exact ``current_summary`` / ``topic_keywords`` text each carried AT
DECISION TIME. Those fields are rewritten wholesale by the async LLM updater
on (almost) every turn and keep no history, so re-reading `narratives` later
reconstructs a pool that never existed.

Hence: the audit stores the WHOLE pool as content-addressed text snapshots.
`test_audit_replays_bm25_exactly` is the property that makes the table worth
its bytes — it fails if anyone trims the pool to top-K or drops the text.
"""

from __future__ import annotations

import pytest

from narranexus.platform.memory.bm25 import bm25_rank
from narranexus.platform.narrative.models import RoutingAudit, RoutingCandidate
from narranexus.platform.repository.narrative_routing_audit_repository import (
    NarrativeRoutingAuditRepository,
    text_hash,
)

pytestmark = pytest.mark.asyncio


# A pool shaped like the real thing: a handful of Chinese narratives plus the
# eight English default-narrative descriptions that sit in every agent's pool
# (measured on dev 2026-08-07: 89.3% of all narrative rows are defaults).
# They matter here precisely because they are semantically irrelevant yet
# still move IDF/avgdl — a replay that omits them produces different numbers.
_POOL = [
    ("nar_a", "飞书 OAuth 授权与消息测试\nTopic: 授权回调调试\n飞书 oauth 授权 回调", False),
    ("nar_b", "会议纪要整理\nTopic: 今天参加过的会议内容\n会议 纪要 整理", False),
    ("nar_c", "MathFRAYT bug 排查\nStatus: 定位中\nbug 排查 前端", False),
    ("nar_d0", "GreetingAndCourtesy Greetings, small talk, thanks, farewells, "
               "ending chat or explicitly terminating current conversation", True),
    ("nar_d1", "CasualChatOrEmotion Casual chat or emotional expression that "
               "clearly doesn't point to any specific object, event, or issue", True),
]
_QUERY = "帮我看下飞书授权回调"


def _audit_from_pool(pool, query):
    """Build the audit payload the production writer will build."""
    scores = bm25_rank(query, [(nid, text) for nid, text, _ in pool])
    return (
        RoutingAudit(
            agent_id="agent_test",
            user_id="user_test",
            query_text=query,
            trigger="chat",
            is_user_chat=True,
            candidates=[
                RoutingCandidate(
                    narrative_id=nid,
                    text_hash=text_hash(text),
                    raw_score=scores.get(nid, 0.0),
                    is_default=is_default,
                )
                for nid, text, is_default in pool
            ],
            selection_method="high_confidence",
            retrieval_method="keyword",
            chosen_narrative_id="nar_a",
        ),
        {text_hash(text): text for _, text, _ in pool},
    )


async def test_audit_replays_bm25_exactly(db_client):
    """The stored pool must reproduce the original scores bit-for-bit.

    This is the whole point of the table. It fails if the pool is truncated
    to top-K (IDF/avgdl change) or if only IDs are stored (text is gone).
    """
    # Arrange — score a pool and persist the decision
    audit, snapshots = _audit_from_pool(_POOL, _QUERY)
    original = {c.narrative_id: c.raw_score for c in audit.candidates}
    repo = NarrativeRoutingAuditRepository(db_client)
    await repo.record(audit, snapshots)

    # Act — reconstruct the pool from the audit alone, then re-rank
    row = (await repo.recent(agent_id="agent_test", limit=1))[0]
    stored = [RoutingCandidate(**c) for c in row["candidates"]]
    texts = await repo.load_snapshots([c.text_hash for c in stored])
    replayed = bm25_rank(
        row["query_text"], [(c.narrative_id, texts[c.text_hash]) for c in stored]
    )

    # Assert — identical, not merely close
    for nid, score in original.items():
        if score > 0:
            assert replayed[nid] == pytest.approx(score, abs=1e-12), (
                f"{nid} replayed to {replayed.get(nid)} but was scored {score} "
                f"at decision time — the audit is not replay-sufficient"
            )


async def test_dropping_the_defaults_changes_the_scores(db_client):
    """Guards the reason the full pool is stored, not just the interesting rows.

    Measured on 452 real local queries: removing the eight default narratives
    flips top-1 on 9.7% of turns. If this test ever passes trivially (scores
    unchanged), the justification for storing the whole pool is gone and the
    docstring above is wrong.
    """
    full = bm25_rank(_QUERY, [(nid, t) for nid, t, _ in _POOL])
    trimmed = bm25_rank(_QUERY, [(nid, t) for nid, t, d in _POOL if not d])
    shared = [nid for nid in full if nid in trimmed]
    assert shared, "fixture must have candidates surviving both rankings"
    assert any(full[nid] != trimmed[nid] for nid in shared), (
        "removing the default narratives did not move any score — "
        "the pool-completeness requirement this table is built around no "
        "longer holds; re-derive it before trimming the audit"
    )


async def test_snapshots_are_content_addressed_and_deduped(db_client):
    """Two turns over an unchanged pool must not double-store the texts."""
    repo = NarrativeRoutingAuditRepository(db_client)
    audit, snapshots = _audit_from_pool(_POOL, _QUERY)

    async def snapshot_count() -> int:
        # Counted here rather than on the repository: production never needs a
        # COUNT, and keeping it out leaves the repository with zero hand-written
        # SQL (every real access goes through the dialect-safe client helpers).
        rows = await db_client.execute("SELECT COUNT(*) AS n FROM narrative_text_snapshots")
        return int(rows[0]["n"]) if rows else 0

    await repo.record(audit, snapshots)
    after_first = await snapshot_count()
    await repo.record(audit, snapshots)
    after_second = await snapshot_count()

    assert after_first == len(_POOL)
    assert after_second == after_first, (
        "re-recording an unchanged pool grew the snapshot table; the "
        "content-addressed key is not deduplicating"
    )


def test_record_pool_captures_participants_not_in_the_bm25_pool():
    """P0-4 candidates must appear in the audit, flagged, with raw_score 0.

    Participant narratives are appended to the result list AFTER ranking, with
    a synthetic neutral similarity — they never went through bm25_rank. An
    audit taken before that merge (the first cut of this code did exactly
    that) silently drops them and leaves `is_participant` permanently false,
    losing precisely the candidates the participant-priority rule is about.
    """
    from datetime import datetime, timezone

    from narranexus.platform.narrative.models import (
        Narrative, NarrativeInfo, NarrativeSearchResult, NarrativeType,
    )
    from narranexus.platform.narrative._narrative_impl.retrieval import NarrativeRetrieval

    now = datetime.now(timezone.utc)
    invited = Narrative(
        id="nar_invited", type=NarrativeType.CHAT, agent_id="agent_test",
        narrative_info=NarrativeInfo(
            name="别人邀请我进来的任务", description="", current_summary="", actors=[]
        ),
        event_ids=[], created_at=now, updated_at=now,
    )
    pool = [(nid, text, is_d) for nid, text, is_d in _POOL]
    audit = RoutingAudit(agent_id="agent_test", user_id="user_test", query_text=_QUERY)
    snapshots: dict = {}

    # `_build_pool_record` returns instead of mutating (2026-08-26 review #5):
    # the caller commits candidates and snapshots together, so a failure
    # part-way through cannot leave a half-written row or an orphan snapshot.
    candidates, built = NarrativeRetrieval._build_pool_record(
        pool,
        [NarrativeSearchResult(narrative_id="nar_a", similarity_score=0.9, rank=1, raw_score=9.0)],
        [invited],
    )
    audit.candidates.extend(candidates)
    snapshots.update(built)

    by_id = {c.narrative_id: c for c in audit.candidates}
    assert "nar_invited" in by_id, "participant candidate missing from the audit"
    assert by_id["nar_invited"].is_participant
    assert by_id["nar_invited"].raw_score == 0.0, (
        "a participant carries a synthetic neutral similarity, never a BM25 "
        "score — recording one would make a replay read it as a keyword hit"
    )
    assert by_id["nar_invited"].text_hash in snapshots
    assert not by_id["nar_a"].is_participant


async def test_existence_check_does_not_fetch_the_snapshot_text(db_client):
    """The write path must ask for keys, not payload.

    `_store_snapshots` only needs "which hashes exist". Resolving that through
    `load_snapshots` (SELECT *) would ship every pool member's MEDIUMTEXT back
    on each non-continuous turn just to discard it — on `select()`'s synchronous
    path, so billed to every user message, and re-shipping the text `load_pool`
    read from `narratives` in the same turn.
    """
    repo = NarrativeRoutingAuditRepository(db_client)
    audit, snapshots = _audit_from_pool(_POOL, _QUERY)
    seen: list = []

    real = db_client.get_by_ids

    async def spy(table, id_field, ids, **kw):
        seen.append((table, kw.get("fields")))
        return await real(table, id_field, ids, **kw)

    db_client.get_by_ids = spy
    try:
        await repo.record(audit, snapshots)
    finally:
        db_client.get_by_ids = real

    snapshot_reads = [f for t, f in seen if t == "narrative_text_snapshots"]
    assert snapshot_reads, "the write path never checked which hashes exist"
    assert all(f == ["text_hash"] for f in snapshot_reads), (
        f"existence check pulled a wide projection {snapshot_reads} — that is "
        f"the whole pool's text over the wire, per turn, to be thrown away"
    )


async def test_recent_pushes_limit_and_ordering_into_sql(db_client):
    """Never read the whole table and slice in Python.

    One row per turn, never deleted, each carrying `candidates_json` — an agent
    with tens of thousands of turns would pull hundreds of MB to return 50 rows.
    """
    repo = NarrativeRoutingAuditRepository(db_client)
    audit, snapshots = _audit_from_pool(_POOL, _QUERY)
    for _ in range(3):
        await repo.record(audit, snapshots)

    captured: dict = {}
    real = db_client.get

    async def spy(table, filters=None, **kw):
        captured.update(table=table, **kw)
        return await real(table, filters, **kw)

    db_client.get = spy
    try:
        rows = await repo.recent(agent_id="agent_test", limit=2)
    finally:
        db_client.get = real

    assert captured.get("limit") == 2, f"limit not pushed into SQL: {captured}"
    assert (captured.get("order_by") or "").lower().startswith("id desc"), (
        f"ordering not pushed into SQL: {captured}"
    )
    assert len(rows) == 2
    assert rows[0]["id"] > rows[1]["id"], "rows are not newest-first"


async def test_unknown_existence_skips_the_writes_instead_of_hammering_the_index(db_client):
    """A failed existence check must not be read as "nothing is stored".

    Treating the two as the same makes the writer insert the WHOLE pool, every
    row colliding with the unique index and every failure swallowed one level
    down: one warning plus ~100 doomed writes, on `select()`'s synchronous path,
    once per turn, indefinitely. Losing one turn's snapshots costs a replay one
    turn's text; the alternative taxes every conversation.
    """
    repo = NarrativeRoutingAuditRepository(db_client)
    audit, snapshots = _audit_from_pool(_POOL, _QUERY)

    async def _blind(*a, **k):
        raise RuntimeError("existence check is down")

    inserted: list = []
    real_insert = db_client.insert

    async def spy_insert(table, data, *a, **k):
        inserted.append(table)
        return await real_insert(table, data, *a, **k)

    db_client.get_by_ids = _blind
    db_client.insert = spy_insert
    try:
        await repo.record(audit, snapshots)
    finally:
        db_client.insert = real_insert

    snapshot_writes = [t for t in inserted if t == "narrative_text_snapshots"]
    assert not snapshot_writes, (
        f"{len(snapshot_writes)} snapshot inserts were attempted after the "
        f"existence check failed — every one of them collides with the unique "
        f"index, and this runs on the per-turn synchronous path"
    )
    # The decision itself must still be recorded; only the text is skipped.
    assert "narrative_routing_audit" in inserted


async def test_record_never_raises(db_client):
    """The observer must not break the observed (audit is advisory)."""
    repo = NarrativeRoutingAuditRepository(db_client)
    audit, snapshots = _audit_from_pool(_POOL, _QUERY)

    class _Broken:
        async def insert(self, *a, **k):
            raise RuntimeError("db is down")

        async def execute(self, *a, **k):
            raise RuntimeError("db is down")

        async def get(self, *a, **k):
            raise RuntimeError("db is down")

        async def get_by_ids(self, *a, **k):
            raise RuntimeError("db is down")

    repo._db = _Broken()
    await repo.record(audit, snapshots)  # must not raise
