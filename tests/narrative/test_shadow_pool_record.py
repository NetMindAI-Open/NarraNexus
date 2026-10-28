"""
@file_name: test_shadow_pool_record.py
@date: 2026-08-25
@description: A continuity turn must record the BM25 pool it never consulted —
and must decide exactly what it decided before.

WHY (reference/self_notebook/specs/2026-08-25-merged-routing-design.md §2)

The zero-LLM shutter's releasable population is bounded at 6% (lower) to 39%
(upper) of continuity turns — a 3x band that is almost entirely reconstruction
slack, not signal. The band exists for one reason: when continuity says yes,
`NarrativeService.select` returns before the retrieval tier runs, so BM25 never
scores and the audit row carries no pool. Every estimate of "what would the
shutter have said here" is therefore inferred from a pool that was never built.

Slice 0 closes that by running the pool build on continuity turns too, purely
to RECORD it. Nothing about the verdict changes: the turn still lands on
`session.current_narrative_id`, still reports `selection_method="continuous"`.

THE INVARIANT THIS FILE EXISTS FOR

An instrument that changes the thing it measures is worse than no instrument.
`test_the_verdict_is_byte_identical_with_and_without_the_recorder` pins the
decision fields against the recorder being disabled, so a future edit that lets
the shadow pool leak into the outcome turns red here rather than in an arm.

COLUMN SEMANTICS (binding rule #6 — no silent meaning changes)

`gate_short_circuit` means "this turn skipped the judge because the gate said
so". On a shadow row NOTHING was decided by the gate, so it stays NULL exactly
as it is today. The hypothetical verdict goes into `bypass_score_gate` /
`bypass_reason`, which are this batch's own columns and have no legacy readers,
and `pool_is_shadow` marks the row so no aggregate mixes the two populations.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from narranexus.platform.narrative.models import ConversationSession
from narranexus.platform.narrative.narrative_service import NarrativeService
from narranexus.platform.repository.narrative_routing_audit_repository import (
    NarrativeRoutingAuditRepository,
)

pytestmark = pytest.mark.asyncio

AGENT = "agent_shadow"
USER = "user_shadow"


@pytest.fixture
def service(db_client, monkeypatch):
    svc = NarrativeService(agent_id=AGENT, database_client=db_client)
    svc._crud.set_database_client(db_client)
    svc._retrieval.set_database_client(db_client)

    async def _get():
        return db_client

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", _get)

    async def _stub_judge(**kw):
        raise AssertionError("the judge must never run on a continuity turn")

    monkeypatch.setattr(svc._retrieval, "_llm_judge_unified", _stub_judge)
    return svc


def _continuous(svc, monkeypatch, verdict: bool = True):
    class _Detector:
        async def detect(self, **kw):
            from narranexus.platform.narrative.models import ContinuityResult

            return ContinuityResult(
                is_continuous=verdict, confidence=0.93, reason="stub"
            )

    monkeypatch.setattr(svc, "_get_continuity_detector", lambda: _Detector())


def _session(anchor: str):
    now = datetime.now(timezone.utc)
    return ConversationSession(
        session_id="sess_shadow", user_id=USER, agent_id=AGENT,
        created_at=now, last_query_time=now,
        last_query="上一句", last_response="上一条回复",
        current_narrative_id=anchor,
    )


async def _seed(service):
    """An anchor thread plus competition, so the pool is worth recording."""
    anchor = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="部署脚本报错排查", description="",
    )
    others = [
        await service.create_narrative(
            agent_id=AGENT, user_id=USER, title=f"纽约餐厅推荐 第{i}轮", description="",
        )
        for i in range(3)
    ]
    return anchor, others


async def _row(db_client):
    rows = await NarrativeRoutingAuditRepository(db_client).recent(agent_id=AGENT)
    assert rows, "no audit row was written"
    return rows[0]


# ---------------- the instrument ---------------------------------------------


async def test_a_continuity_turn_now_records_the_pool_it_never_consulted(
    service, db_client, monkeypatch
):
    anchor, others = await _seed(service)
    _continuous(service, monkeypatch)

    await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert row["selection_method"] == "continuous"
    assert row["candidates"], "the shadow pool was not recorded"
    ids = {c["narrative_id"] for c in row["candidates"]}
    assert anchor.id in ids
    assert {n.id for n in others} <= ids, (
        "a partial pool cannot be replayed — IDF and avgdl are computed over "
        "the whole candidate set"
    )
    assert row["gate_top1_raw"] is not None, "the pool was recorded but not scored"


async def test_the_shadow_row_is_marked_as_such(service, db_client, monkeypatch):
    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch)

    await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert row["pool_is_shadow"], (
        "an unmarked shadow row makes every gate aggregate mix two populations"
    )
    assert row["bypass_reason"], "the hypothetical shutter verdict was not recorded"


async def test_the_gate_verdict_column_stays_null_on_a_shadow_row(
    service, db_client, monkeypatch
):
    """`gate_short_circuit` means "the GATE skipped the judge" — binding rule #6.

    On a continuity turn the gate decided nothing, so filling that column would
    silently redefine it for every existing reader. The hypothetical verdict
    lives in this batch's own columns instead.
    """
    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch)

    await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert row["gate_short_circuit"] is None
    assert row["bypass_score_gate"] is not None


# ---------------- the invariant ---------------------------------------------


async def test_the_verdict_is_byte_identical_with_and_without_the_recorder(
    service, db_client, monkeypatch
):
    """The whole point: an instrument that moves the needle is not an instrument."""
    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch)

    decided = ("selection_method", "chosen_narrative_id", "is_new",
               "retrieval_method", "continuity_ran", "continuity_is_continuous")

    result_on = await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )
    row_on = await _row(db_client)

    # Same turn with the recorder removed — the branch as it was before slice 0.
    monkeypatch.setattr(
        service._retrieval, "record_pool_only",
        AsyncMock(side_effect=AssertionError("recorder should be off")),
        raising=False,
    )
    monkeypatch.setattr(service, "_record_shadow_pool", AsyncMock(return_value=None))

    result_off = await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )
    row_off = await _row(db_client)

    assert result_on.selection_method == result_off.selection_method == "continuous"
    assert [n.id for n in result_on.narratives] == [n.id for n in result_off.narratives]
    for field in decided:
        assert row_on[field] == row_off[field], (
            f"the recorder changed `{field}`: {row_on[field]!r} vs {row_off[field]!r}"
        )
    # ...and only the recorder's own columns differ
    assert row_off["candidates"] == []
    assert row_on["candidates"]


async def test_a_recorder_failure_never_breaks_the_turn(
    service, db_client, monkeypatch
):
    """The observer must not break the observed (audit repo's stated rule).

    Deliberately narrow: the guard wraps ONLY the instrument call. A failure in
    the decision path must still propagate.
    """
    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch)

    async def _boom(*a, **kw):
        raise RuntimeError("pool read exploded")

    monkeypatch.setattr(service._retrieval, "record_pool_only", _boom, raising=False)

    result = await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )
    assert result.selection_method == "continuous"
    assert result.narratives and result.narratives[0].id == anchor.id
    row = await _row(db_client)
    assert row["selection_method"] == "continuous"
    assert not row["candidates"], "a failed recorder must not half-fill the pool"


async def test_the_recorder_is_awaited_not_fired_and_forgotten(
    service, db_client, monkeypatch
):
    """Incident lesson #2: a bare `create_task` swallows its own exceptions and
    races the audit write. By the time `select` returns, the pool must be ON
    the audit object — not scheduled to arrive later."""
    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch)

    seen: dict = {}
    original = service._retrieval.record_pool_only

    async def _spy(*a, audit, **kw):
        # `audit` is keyword-only on `record_pool_only`, so name it here rather
        # than reaching for a positional slot. The first cut used
        # `kw.get("audit") or a[-2]`, whose fallback could never run and would
        # have silently indexed the wrong argument if the signature ever
        # changed — a dead branch masking an assumption (2026-08-26 review).
        await original(*a, audit=audit, **kw)
        seen["candidates_at_return"] = len(audit.candidates)

    monkeypatch.setattr(service._retrieval, "record_pool_only", _spy, raising=False)

    await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )
    assert seen.get("candidates_at_return", 0) > 0


async def test_a_non_continuity_turn_is_not_marked_shadow(
    service, db_client, monkeypatch
):
    """The flag must separate the two populations in BOTH directions."""
    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch, verdict=False)
    monkeypatch.setattr(
        service._retrieval, "_llm_judge_unified",
        AsyncMock(return_value={"matched_type": "none", "matched_id": None,
                                "reason": "stub"}),
    )

    await service.select(
        AGENT, USER, "完全不相干的新话题:帮我订机票", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert row["selection_method"] != "continuous"
    assert not row["pool_is_shadow"]
    assert row["candidates"]


async def test_the_shadow_column_is_registered_on_both_dialects() -> None:
    from narranexus.platform.utils.db.schema_registry import TABLES

    col = {c.name: c for c in TABLES["narrative_routing_audit"].columns}.get(
        "pool_is_shadow"
    )
    assert col is not None, "narrative_routing_audit.pool_is_shadow not registered"
    assert col.sqlite_type and col.mysql_type
    assert col.nullable, (
        "prod rows predate this column; NOT NULL would fail ALTER TABLE on a "
        "live table (binding rule #6)"
    )


# ---------------- review round 1 · the nails the refactor must hold ---------
#
# Finding #2 was "the instrument re-implements the real path instead of sharing
# code with it, and has already drifted in three places". The fix is a shared
# `_score_pool`. These tests pin the three drifts SHUT, so the next edit
# to the scoring section cannot silently re-open them — a review memory is not a
# mechanism.


async def _wide_pool(service):
    """Eight threads that ALL share tokens with the probe query.

    A four-narrative pool cannot tell a slice of 3 from a slice of 6 — the
    first version of this test passed for exactly that reason. The scoring
    slice only bites when more than six candidates score above zero.
    """
    anchor = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="部署脚本报错排查 第一步", description="",
    )
    for i in range(7):
        await service.create_narrative(
            agent_id=AGENT, user_id=USER,
            title=f"部署脚本报错排查 第{i}步 回归验证", description="",
        )
    return anchor


async def _shadow_and_decision_rows(service, db_client, monkeypatch):
    """One continuity turn and one routed turn against the SAME pool."""
    anchor = await _wide_pool(service)
    _continuous(service, monkeypatch)
    probe = "部署脚本报错排查第二步回归验证"
    await service.select(AGENT, USER, probe, session=_session(anchor.id),
                         trigger="chat", is_user_chat=True)
    shadow = await _row(db_client)

    _continuous(service, monkeypatch, verdict=False)
    monkeypatch.setattr(
        service._retrieval, "_llm_judge_unified",
        AsyncMock(return_value={"matched_type": "none", "matched_id": None,
                                "reason": "stub"}),
    )
    await service.select(AGENT, USER, probe, session=_session(anchor.id),
                         trigger="chat", is_user_chat=True)
    decision = await _row(db_client)
    return shadow, decision


async def test_both_populations_score_the_same_candidate_slice(
    service, db_client, monkeypatch
):
    """Review #3: the shadow slice was 3 and the decision slice was 6.

    `_build_pool_record` gives every candidate OUTSIDE the slice `raw_score =
    0.0`, so ranks 4-6 read as "scored nothing" on a shadow row and as their
    real score on a decision row. That is the same column meaning two different
    things in the two populations — in the one table whose entire purpose is
    that the two are comparable.
    """
    shadow, decision = await _shadow_and_decision_rows(service, db_client, monkeypatch)

    def scored(row):
        return sorted(c["raw_score"] for c in row["candidates"] if c["raw_score"] > 0)

    assert len(scored(decision)) > 3, (
        f"fixture does not exercise the defect: only {len(scored(decision))} "
        f"candidates scored, so a slice of 3 and a slice of 6 are the same"
    )
    assert len(scored(shadow)) == len(scored(decision)), (
        f"scoring slice differs: shadow kept {len(scored(shadow))} non-zero "
        f"scores, decision kept {len(scored(decision))}"
    )
    assert scored(shadow) == pytest.approx(scored(decision))


async def test_both_paths_score_through_the_same_helper(
    service, db_client, monkeypatch
):
    """Review #2, the mechanism rather than the symptom.

    `keyword_ms` drifted (shadow timed only the ranking), the scoring slice
    drifted (3 vs 6), and the bucket precondition drifted — three symptoms of
    one cause: the instrument re-implemented the real path instead of sharing
    it. Asserting each symptom separately would leave the cause alive, so this
    asserts the cause is gone: BOTH paths go through one helper, with the same
    `top_k`. A future edit to the scoring section now cannot reach only one of them.
    """
    calls: list[dict] = []
    original = service._retrieval._score_pool

    async def _spy(*a, **kw):
        calls.append(dict(kw))
        return await original(*a, **kw)

    monkeypatch.setattr(service._retrieval, "_score_pool", _spy)
    await _shadow_and_decision_rows(service, db_client, monkeypatch)

    assert len(calls) == 2, (
        f"expected the shadow turn and the decision turn to share the helper, "
        f"got {len(calls)} call(s)"
    )
    assert calls[0]["top_k"] == calls[1]["top_k"], (
        f"the two populations ask for different slices: "
        f"{calls[0]['top_k']} vs {calls[1]['top_k']}"
    )


async def test_the_instrument_reports_its_own_cost(service, db_client, monkeypatch):
    """Review #4: slice 0 adds two DB reads to the synchronous path of every
    continuity turn, and nothing measured them. `retrieve_ms` is empty on
    shadow rows today and its meaning — "how long did the retrieval tier take"
    — fits exactly, so the instrument becomes self-observable without a new
    column (which would double the mirror-sync obligation all over again)."""
    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch)
    await service.select(AGENT, USER, "那第二步呢", session=_session(anchor.id),
                         trigger="chat", is_user_chat=True)
    row = await _row(db_client)
    assert row["retrieve_ms"] is not None, (
        "the instrument's own cost is not in any column — verifying '~13.5ms' "
        "would mean measuring it by hand again"
    )


async def test_a_failed_recorder_leaves_no_orphan_snapshot(
    service, db_client, monkeypatch
):
    """Review #5 + #9: the rollback was a hand-copied list of "which columns did
    the recorder write", and it was already missing `gate_reason` in the commit
    that introduced it. Snapshots were not rolled back at all, so a failure left
    text rows in `narrative_text_snapshots` that no audit row referenced.

    The fix is not a longer list — it is all-or-nothing, so there is no list to
    drift.
    """
    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch)

    before = await db_client.get("narrative_text_snapshots", {})

    # Fail LATE — after the pool has been read and the candidate records
    # built, which is the only point at which an orphan snapshot could exist.
    # Failing earlier is why the first version of this test passed without
    # proving anything.
    def _boom(*a, **kw):
        raise RuntimeError("scoring exploded after the pool was recorded")

    monkeypatch.setattr(
        "narranexus.platform.narrative._narrative_impl.retrieval.evaluate_bypass",
        _boom,
    )

    result = await service.select(AGENT, USER, "那第二步呢",
                                  session=_session(anchor.id),
                                  trigger="chat", is_user_chat=True)
    assert result.selection_method == "continuous"

    row = await _row(db_client)
    assert not row["candidates"]
    assert not row["pool_is_shadow"]
    after = await db_client.get("narrative_text_snapshots", {})
    assert len(after) == len(before), (
        f"{len(after) - len(before)} orphan snapshot(s) survived a failed "
        f"recording"
    )


async def test_the_instrument_has_an_env_switch(service, db_client, monkeypatch):
    """Review #10: every comparable governance switch in this batch is env-gated
    with a written rollback path (`NARRATIVE_DEFAULT_BUCKETS_ENABLED`). Without
    one, turning the instrument off means a code change plus re-publishing both
    run modes (binding rule #7)."""
    from narranexus.platform.narrative.config import config as narrative_config

    anchor, _ = await _seed(service)
    _continuous(service, monkeypatch)
    monkeypatch.setattr(
        narrative_config, "NARRATIVE_SHADOW_POOL_RECORD", False, raising=False
    )

    result = await service.select(AGENT, USER, "那第二步呢",
                                  session=_session(anchor.id),
                                  trigger="chat", is_user_chat=True)
    row = await _row(db_client)
    assert result.selection_method == "continuous"
    assert result.narratives[0].id == anchor.id
    assert not row["candidates"], "the switch did not stop the recording"
    assert not row["pool_is_shadow"]


async def test_a_background_continuation_turn_is_not_recorded(
    service, db_client, monkeypatch
):
    """PR #365 review M4: background triggers have no session anchor by
    design, so their shadow rows cannot answer the shutter's releasable-
    population question (bypass_reason would be background_scope on every
    one) — they would only pay the recording cost, and message_bus alone is
    ~30% of dev turns. The scope is user chat; this pin keeps that a
    mechanism instead of a default."""
    anchor, _others = await _seed(service)
    _continuous(service, monkeypatch)

    result = await service.select(
        AGENT, USER, "那第二步呢", session=_session(anchor.id),
        trigger="job", is_user_chat=False,
    )

    assert result.narratives, "the decision path itself is untouched"
    row = await _row(db_client)
    assert row["selection_method"] == "continuous", (
        "precondition: this must be a background CONTINUATION turn — "
        "otherwise the no-recording assertion below tests nothing"
    )
    assert not row["candidates"], "a background turn must not pay the recorder"
    assert row["pool_is_shadow"] == 0  # write side int()-coerces; never NULL
    assert row["is_user_chat"] == 0, (
        "the second discriminator column must be honest on background rows — "
        "it is assigned in select()'s common tail, and losing that (an early "
        "return, a moved _write_audit) would silently count background "
        "continuation turns into the decision population"
    )
