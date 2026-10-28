"""
@file_name: test_merged_routing.py
@date: 2026-08-26
@description: One routing decision per turn instead of two — the flow, the
zero-LLM shutter, the landings, and the failure floor.

WHAT THIS BATCH CHANGES (specs/2026-08-25-merged-routing-design.md §3)

Today a turn can pay for TWO serial helper calls: continuity ("does this
continue the thread?") and, if that says no, the judge ("then where does it
go?"). Measured on prod (7 days, n=189 real user turns): 43 turns pay both, at
a serial p50 of 8,924ms. The non-LLM half of routing is 47.6ms mean — there is
nothing else in there to fix.

Merged routing runs BM25 FIRST on every turn, then either
  * opens a zero-LLM shutter (top1 IS the anchor, floor+margin clear) — this is
    `evaluate_bypass`'s existing `anchor_match` verdict, moved ahead of
    continuity rather than re-invented, or
  * asks ONE question with four answers: continue_anchor / match(i) / new /
    no_topic (plus `participant`, which stays exactly as structural as it is
    today).

THE DECIDER CHANGES; THE EXECUTORS DO NOT

Every landing below is an existing code path: the continuity landing, the
judge's search landing, `create_from_query`, `_land_no_topic_turn`. That is the
property that makes this reviewable — and `test_downstream_cannot_tell_who_
decided` is the assertion that step_1 / step_4 never learn there was a change.

THE FAILURE FLOOR (rule 6)

A merged call that raises, times out, or answers off-contract must NOT be read
as "new topic". Two production incidents (D19) were exactly this shape: a
failure in the deciding tier fell through to creation, the created thread became
the anchor, and the updater then rewrote it until the lexical evidence agreed.
So: anchor present → stay on it, flagged `merged_fallback_anchor`; no anchor →
creation is allowed but is flagged too. Never silent, never a switch.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from narranexus.platform.narrative._narrative_impl import merged_router
from narranexus.platform.narrative._narrative_impl.routing_gate import (
    GateDecision,
    evaluate_bypass,
    shutter_opens,
)
from narranexus.platform.narrative.config import config
from narranexus.platform.narrative.models import ConversationSession
from narranexus.platform.narrative.narrative_service import NarrativeService
from narranexus.platform.repository.narrative_routing_audit_repository import (
    NarrativeRoutingAuditRepository,
)

pytestmark = pytest.mark.asyncio

AGENT = "agent_merged_flow"
USER = "user_merged_flow"

# Scores the anchor decisively and nothing else (the others are restaurant
# threads) — a lone scoring candidate, i.e. the shutter's happy path.
ANCHOR_QUERY = "部署脚本报错的日志怎么看"
# Scores the restaurant thread and not the anchor: the merged call has a real
# menu row to be tempted by, which is the whole risk §3.2 is about.
FOREIGN_QUERY = "餐厅推荐一下"


@pytest.fixture
def merged_on(monkeypatch):
    monkeypatch.setattr(config, "NARRATIVE_MERGED_ROUTING_ENABLED", True)


@pytest.fixture
def service(db_client, monkeypatch):
    svc = NarrativeService(agent_id=AGENT, database_client=db_client)
    svc._crud.set_database_client(db_client)
    svc._retrieval.set_database_client(db_client)

    async def _get():
        return db_client

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", _get)

    # The two tiers merged routing replaces. Either one running is a bug, not a
    # fallback, so both are land mines rather than doubles.
    def _no_continuity():
        raise AssertionError("the continuity tier must not run on a merged turn")

    async def _no_judge(**kw):
        raise AssertionError("the judge must not run on a merged turn")

    monkeypatch.setattr(svc, "_get_continuity_detector", _no_continuity)
    monkeypatch.setattr(svc._retrieval, "_llm_judge_unified", _no_judge)
    return svc


class _Sdk:
    """The helper-LLM edge — the only thing stubbed. Records every call."""

    def __init__(self, *, verdict: str, index: int = -1, boom: Exception | None = None):
        self.verdict = verdict
        self.index = index
        self.boom = boom
        self.calls: list[dict] = []

    async def llm_function(self, *, instructions, user_input, output_type, **kw):
        self.calls.append({"instructions": instructions, "user_input": user_input})
        if self.boom is not None:
            raise self.boom
        return SimpleNamespace(
            final_output=output_type(
                reason="stubbed model answer",
                verdict=self.verdict,
                match_index=self.index,
            )
        )


def _sdk(monkeypatch, **kwargs) -> _Sdk:
    sdk = _Sdk(**kwargs)
    monkeypatch.setattr(merged_router, "get_helper_sdk", lambda: sdk)
    return sdk


def _no_sdk(monkeypatch) -> None:
    """No helper LLM may be reached at all — the shutter's contract."""

    def _boom():
        raise AssertionError("the shutter must not reach a helper LLM")

    monkeypatch.setattr(merged_router, "get_helper_sdk", _boom)


def _session(anchor: str | None):
    now = datetime.now(timezone.utc)
    return ConversationSession(
        session_id="sess_merged", user_id=USER, agent_id=AGENT,
        created_at=now, last_query_time=now,
        last_query="部署脚本报错怎么修", last_response="先看日志的最后 20 行",
        current_narrative_id=anchor,
    )


async def _seed(service, *, twin: bool = False):
    """An anchor thread plus foreign competition (and optionally a near-twin)."""
    anchor = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="部署脚本报错排查", description="",
    )
    foreign = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="纽约餐厅推荐", description="",
    )
    if twin:
        await service.create_narrative(
            agent_id=AGENT, user_id=USER, title="部署脚本报错复盘", description="",
        )
    return anchor, foreign


async def _row(db_client):
    rows = await NarrativeRoutingAuditRepository(db_client).recent(agent_id=AGENT)
    assert rows, "no audit row was written"
    return rows[0]


# ===================================================================== #
# The switch                                                            #
# ===================================================================== #


def test_the_switch_ships_off():
    assert config.NARRATIVE_MERGED_ROUTING_ENABLED is False


async def test_with_the_switch_off_the_two_call_path_is_untouched(
    service, db_client, monkeypatch
):
    """A flag whose OFF state is not pinned is a flag that has already changed
    behaviour. Nothing on the merged path may even be constructed."""
    anchor, _ = await _seed(service)

    def _boom(*a, **kw):
        raise AssertionError("merged routing ran with its switch off")

    monkeypatch.setattr(merged_router, "build_merged_prompt", _boom)

    class _Detector:
        async def detect(self, **kw):
            from narranexus.platform.narrative.models import ContinuityResult

            return ContinuityResult(is_continuous=True, confidence=0.9, reason="stub")

    monkeypatch.setattr(service, "_get_continuity_detector", lambda: _Detector())

    result = await service.select(
        AGENT, USER, ANCHOR_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "continuous"
    row = await _row(db_client)
    assert row["continuity_ran"] == 1
    assert not row["merged_call"], "the merged columns must stay empty"
    assert row["merged_verdict"] in (None, "")
    assert row["merged_ms"] is None


def test_both_switches_on_is_an_untested_world_and_refuses_to_boot():
    """Bucket governance OFF is what makes a default bucket un-continuable, and
    the merged prompt's anchor slot is built on that. Both flags on is a
    combination nothing has ever measured, so it fails at startup rather than
    in a routing decision (`config` raises at import)."""
    probe = subprocess.run(
        [sys.executable, "-c", "import narranexus.platform.narrative.config"],
        capture_output=True, text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": ":".join(sys.path),
            "NARRATIVE_DEFAULT_BUCKETS_ENABLED": "1",
            "NARRATIVE_MERGED_ROUTING_ENABLED": "1",
        },
    )

    assert probe.returncode != 0, "both switches on must not boot"
    assert "NARRATIVE_MERGED_ROUTING_ENABLED" in probe.stderr


# ===================================================================== #
# Rule 3 · the shutter reads floor/margin + identity, never the total   #
# ===================================================================== #


def test_rule3_a_huge_but_crowded_score_does_not_open_the_shutter():
    """Real prod row (audit id=1): top1 86.54, top2 71.71, margin 1.21. Every
    DIVERTED misconfirmation in the four-arm dry run sat at margin 1.06-1.27 —
    an enormous absolute score that means the anchor merely EDGED OUT a rival."""
    gate = GateDecision(
        short_circuit=False, reason="crowded", top1_raw=86.54, top2_raw=71.71,
        margin=1.21,
    )

    bypass = evaluate_bypass(
        gate, top1_narrative_id="nar_anchor", anchor_narrative_id="nar_anchor",
        is_user_chat=True, has_participant_narratives=False,
    )

    assert not shutter_opens(bypass)
    assert bypass.reason == "score_gate"


def test_rule3_a_modest_but_clear_score_does_open_it():
    gate = GateDecision(
        short_circuit=True, reason="clear", top1_raw=3.5, top2_raw=1.0, margin=3.5,
    )

    bypass = evaluate_bypass(
        gate, top1_narrative_id="nar_anchor", anchor_narrative_id="nar_anchor",
        is_user_chat=True, has_participant_narratives=False,
    )

    assert shutter_opens(bypass)
    assert bypass.reason == "anchor_match"


def test_rule3_the_shutter_is_exactly_the_anchor_match_verdict():
    """Not a new rule: the shutter IS `evaluate_bypass` moved ahead of
    continuity. Every other verdict — including the ones that GRANT a bypass on
    the two-call path — leaves the shutter shut, because only `anchor_match`
    carries both halves (floor+margin cleared AND the turn stays put)."""
    gate = GateDecision(
        short_circuit=True, reason="clear", top1_raw=9.0, top2_raw=1.0, margin=9.0,
    )

    background = evaluate_bypass(
        gate, top1_narrative_id="nar_other", anchor_narrative_id=None,
        is_user_chat=False, has_participant_narratives=False,
    )
    assert background.granted and background.reason == "background_scope"
    assert not shutter_opens(background)

    switch = evaluate_bypass(
        gate, top1_narrative_id="nar_other", anchor_narrative_id="nar_anchor",
        is_user_chat=True, has_participant_narratives=False,
    )
    assert not shutter_opens(switch) and switch.reason == "anchor_miss"

    participant = evaluate_bypass(
        gate, top1_narrative_id="nar_anchor", anchor_narrative_id="nar_anchor",
        is_user_chat=True, has_participant_narratives=True,
    )
    assert not shutter_opens(participant)
    assert participant.reason == "participant_present"


async def test_the_shutter_confirms_the_thread_with_zero_llm_calls(
    service, db_client, merged_on, monkeypatch
):
    anchor, _ = await _seed(service)
    _no_sdk(monkeypatch)

    result = await service.select(
        AGENT, USER, ANCHOR_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "anchor_confirmed"
    assert [n.id for n in result.narratives] == [anchor.id]
    row = await _row(db_client)
    assert row["merged_call"] == 1
    assert row["merged_verdict"] in (None, ""), "no model was asked"
    assert row["merged_ms"] is None, "a tier that did not run costs NULL, not 0"
    assert row["bypass_reason"] == "anchor_match"
    assert row["gate_short_circuit"] == 1
    assert row["continuity_ms"] is None and row["judge_ms"] is None


async def test_a_crowded_pool_pays_for_exactly_one_call(
    service, db_client, merged_on, monkeypatch
):
    """The whole point: the turn that used to cost continuity + judge now costs
    one call. `service` makes both old tiers land mines, so reaching this row at
    all proves neither ran."""
    anchor, _ = await _seed(service, twin=True)
    sdk = _sdk(monkeypatch, verdict=merged_router.VERDICT_CONTINUE_ANCHOR)

    result = await service.select(
        AGENT, USER, "部署脚本报错怎么修", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert len(sdk.calls) == 1
    assert result.selection_method == "merged_continue"
    assert [n.id for n in result.narratives] == [anchor.id]


# ===================================================================== #
# Rule 1 (service level) · the anchor reaches the prompt whatever BM25   #
# thinks of it                                                          #
# ===================================================================== #


async def test_rule1_the_anchor_reaches_the_prompt_even_scoring_zero(
    service, db_client, merged_on, monkeypatch
):
    """The p07 shape: the menu's top row is a foreign thread and the anchor is
    nowhere in it. On the two-call path this turn never built a menu at all."""
    anchor, foreign = await _seed(service)
    sdk = _sdk(monkeypatch, verdict=merged_router.VERDICT_CONTINUE_ANCHOR)

    await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    body = sdk.calls[0]["user_input"]
    assert "部署脚本报错排查" in body, "the anchor was dropped for scoring zero"
    assert "纽约餐厅推荐" in body
    row = await _row(db_client)
    assert row["anchor_in_menu"] == 0, (
        "the production-side instrument for §3.2 — how often the unconditional "
        "injection is the only reason the anchor is there at all"
    )
    assert row["anchor_bm25_rank"] is None, "zero score has no rank"


async def test_the_anchors_bm25_standing_is_recorded_when_it_does_score(
    service, db_client, merged_on, monkeypatch
):
    anchor, _ = await _seed(service, twin=True)
    _sdk(monkeypatch, verdict=merged_router.VERDICT_CONTINUE_ANCHOR)

    await service.select(
        AGENT, USER, "部署脚本报错怎么修", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert row["anchor_bm25_rank"] >= 1
    assert row["anchor_raw_score"] > 0
    assert row["anchor_in_menu"] == 1


# ===================================================================== #
# The four landings — decider changes, executors do not                 #
# ===================================================================== #


async def test_match_lands_on_the_menu_row_the_model_named(
    service, db_client, merged_on, monkeypatch
):
    anchor, foreign = await _seed(service)
    _sdk(monkeypatch, verdict=merged_router.VERDICT_MATCH, index=0)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_match"
    assert result.narratives[0].id == foreign.id
    assert result.is_new is False
    row = await _row(db_client)
    assert row["merged_verdict"] == merged_router.VERDICT_MATCH
    assert row["chosen_narrative_id"] == foreign.id


async def test_new_goes_through_the_existing_creator(
    service, db_client, merged_on, monkeypatch
):
    anchor, _ = await _seed(service)
    _sdk(monkeypatch, verdict=merged_router.VERDICT_NEW)

    result = await service.select(
        AGENT, USER, "帮我订下周去东京的机票", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_new"
    assert result.is_new is True
    created = result.narratives[0]
    assert created.id not in (anchor.id,)
    assert created.topic_keywords, (
        "created through `create_from_query`, so it carries the same BM25 "
        "routing surface as a thread born on the two-call path"
    )


async def test_no_topic_hands_the_turn_to_the_existing_landing_rule(
    service, db_client, merged_on, monkeypatch
):
    """`_land_no_topic_turn` is untouched: anchor-first, and the anchor's
    retrieval surface is NOT rewritten by a contentless turn."""
    anchor, _ = await _seed(service)
    _sdk(monkeypatch, verdict=merged_router.VERDICT_NO_TOPIC)

    result = await service.select(
        AGENT, USER, "哈哈哈", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "no_topic_anchored"
    assert result.no_durable_topic is True
    assert [n.id for n in result.narratives] == [anchor.id]
    row = await _row(db_client)
    assert row["merged_verdict"] == merged_router.VERDICT_NO_TOPIC


async def test_no_topic_with_no_anchor_still_keeps_the_turn_in_history(
    service, db_client, merged_on, monkeypatch
):
    await _seed(service)
    _sdk(monkeypatch, verdict=merged_router.VERDICT_NO_TOPIC)

    result = await service.select(
        AGENT, USER, "哈哈哈", session=_session(None),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "new_created"
    assert result.no_durable_topic is True


async def test_participant_priority_survives_the_merge(
    service, db_client, merged_on, monkeypatch
):
    """P0-4 stays structural on both ends: the shutter cannot open while a
    participant thread exists, and the model picks one through its own verdict
    rather than out of the keyword menu."""
    anchor, _ = await _seed(service)
    invited = await service.create_narrative(
        agent_id=AGENT, user_id="someone_else", title="客户 A 的报价单", description="",
    )
    monkeypatch.setattr(
        service._retrieval, "_get_participant_narratives",
        lambda **kw: _immediately([invited]),
    )
    sdk = _sdk(monkeypatch, verdict=merged_router.VERDICT_PARTICIPANT, index=0)

    result = await service.select(
        AGENT, USER, ANCHOR_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert len(sdk.calls) == 1, "a participant thread must deny the shutter"
    assert result.selection_method == "merged_participant"
    assert result.narratives[0].id == invited.id
    row = await _row(db_client)
    assert row["bypass_reason"] == "participant_present"


async def _immediately(value):
    return value


# ===================================================================== #
# Rule 6 · the failure floor                                            #
# ===================================================================== #


async def test_rule6_a_failed_call_stays_on_the_anchor(
    service, db_client, merged_on, monkeypatch
):
    anchor, _ = await _seed(service)
    _sdk(monkeypatch, verdict=merged_router.VERDICT_NEW,
         boom=TimeoutError("provider timed out"))

    creator_calls: list = []
    original = service._retrieval.create_from_query

    async def _spy(**kw):
        creator_calls.append(kw)
        return await original(**kw)

    monkeypatch.setattr(service._retrieval, "create_from_query", _spy)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_fallback_anchor"
    assert [n.id for n in result.narratives] == [anchor.id]
    assert result.is_new is False
    assert creator_calls == [], (
        "D19: a failure in the deciding tier must never fall through to "
        "creation while there is a thread to stay on"
    )
    row = await _row(db_client)
    assert row["merged_verdict"] == merged_router.VERDICT_FAILED
    assert row["merged_ms"] is not None, "the failed call still cost time"


@pytest.mark.parametrize(
    "verdict,index",
    [
        ("switch", 0),                          # not in the contract at all
        (merged_router.VERDICT_MATCH, 7),        # index past the menu
        (merged_router.VERDICT_MATCH, -1),       # match with no index
        (merged_router.VERDICT_PARTICIPANT, 0),  # no participants were offered
    ],
)
async def test_rule6_an_off_contract_answer_is_a_failure_not_a_guess(
    service, db_client, merged_on, monkeypatch, verdict, index
):
    """An unusable answer must land exactly where an exception lands. The
    tempting alternative — "fall back to `new`" — is the D19 shape."""
    anchor, _ = await _seed(service)
    _sdk(monkeypatch, verdict=verdict, index=index)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_fallback_anchor"
    assert [n.id for n in result.narratives] == [anchor.id]


async def test_rule6_with_no_anchor_a_failure_may_create_but_must_say_so(
    service, db_client, merged_on, monkeypatch
):
    """Running bare would drop a first-contact turn out of the user's own chat
    history (history endpoints are narrative-scoped), so creating is right —
    what is forbidden is doing it SILENTLY, indistinguishable from a real
    "new topic" verdict."""
    await _seed(service)
    _sdk(monkeypatch, verdict=merged_router.VERDICT_NEW,
         boom=RuntimeError("schema validation exploded"))

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(None),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_fallback_new"
    assert result.is_new is True
    row = await _row(db_client)
    assert row["merged_verdict"] == merged_router.VERDICT_FAILED


async def test_rule6_a_bucket_anchor_is_not_a_thread_to_fall_back_onto(
    service, db_client, merged_on, monkeypatch
):
    """`is_reusable_anchor` is THE definition, and the fallback consumes it too
    — otherwise a failed call would re-pin the turn to a container whose
    retrieval surface never updates (C-1, 26.4% of prod user turns)."""
    bucket = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="GreetingAndCourtesy", description="",
    )
    bucket.is_special = "default"
    await service.save_narrative_to_db(bucket)
    _sdk(monkeypatch, verdict=merged_router.VERDICT_NEW, boom=RuntimeError("boom"))

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(bucket.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_fallback_new"
    assert result.narratives[0].id != bucket.id


async def test_rule6_continue_anchor_on_a_bucket_anchor_is_refused_not_landed(
    service, db_client, merged_on, monkeypatch
):
    """The combination the review found uncovered: the model answers
    continue_anchor while the anchor is a legacy container. The contract must
    refuse it (a bucket is a verdict about an earlier turn, not a thread), and
    the landing is the anchorless fallback — never the bucket itself."""
    bucket = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="GreetingAndCourtesy", description="",
    )
    bucket.is_special = "default"
    await service.save_narrative_to_db(bucket)
    _sdk(monkeypatch, verdict=merged_router.VERDICT_CONTINUE_ANCHOR, index=-1)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(bucket.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_fallback_new"
    assert all(n.id != bucket.id for n in result.narratives)
    row = await _row(db_client)
    assert row["merged_verdict"] == merged_router.VERDICT_FAILED


async def test_the_offered_verdicts_are_derived_from_the_input():
    """One derivation, read by both the prompt fragments and the contract
    check — so "which answers exist on this turn" cannot drift into a fifth
    hand-written copy (review Critical 1, trap note)."""
    base = dict(
        query="q", previous_query="", previous_response="",
        minutes_since_previous=None, menu=[], participants=[], awareness="",
    )
    bare = merged_router.MergedRoutingInput(
        anchor=None, anchor_is_continuable=False, **base
    )
    # An empty menu withdraws `match` too (round 6, I3): no index to give.
    assert merged_router.allowed_verdicts(bare) == frozenset(
        {merged_router.VERDICT_NEW, merged_router.VERDICT_NO_TOPIC}
    )
    with_menu = merged_router.MergedRoutingInput(
        anchor=None, anchor_is_continuable=False,
        **{**base, "menu": [
            {"id": "m", "type": "search", "name": "n", "description": "d"}
        ]},
    )
    assert merged_router.VERDICT_MATCH in merged_router.allowed_verdicts(
        with_menu
    )
    with_part = merged_router.MergedRoutingInput(
        anchor=None, anchor_is_continuable=False,
        **{**base, "participants": [
            {"id": "p", "type": "participant", "name": "n", "description": "d"}
        ]},
    )
    assert merged_router.VERDICT_PARTICIPANT in merged_router.allowed_verdicts(
        with_part
    )


async def test_rule6_an_index_into_the_unrendered_participant_tail_is_refused(
    service, db_client, merged_on, monkeypatch
):
    """Review round 2, I1: the renderer capped participants at 8 while the
    contract accepted [0, N) — a hallucinated index into the unrendered tail
    would land the turn on a thread that was never on the ballot, audited as
    a legitimate merged_participant. What enters MergedRoutingInput is now
    exactly what the prompt shows (prefix slice — the ORDER is the P0-4
    priority), so index 9 with 10 invitations and 8 shown must fail, and the
    cut must surface in merged_truncated."""
    from narranexus.platform.narrative._narrative_impl import merged_prep
    from narranexus.platform.narrative._narrative_impl.retrieval import ScoredPool

    anchor, _ = await _seed(service)
    ten = []
    for i in range(10):
        n = await service.create_narrative(
            agent_id=AGENT, user_id=f"user_owner_{i}",
            title=f"Invited task {i}", description="",
        )
        ten.append(n)

    real_prepare = merged_prep.prepare_merged_routing

    async def _prepare_with_ten(retrieval, query, user_id, agent_id, **kw):
        prep = await real_prepare(retrieval, query, user_id, agent_id, **kw)
        object.__setattr__(prep.scored, "participant_narratives", ten)
        return prep

    monkeypatch.setattr(
        "narranexus.platform.narrative._narrative_impl.merged_select."
        "prepare_merged_routing",
        _prepare_with_ten,
    )
    _sdk(monkeypatch, verdict=merged_router.VERDICT_PARTICIPANT, index=9)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_fallback_anchor", (
        "index 9 points past the 8 rendered rows — refusing it is the "
        "contract; landing it would route to a thread never on the ballot"
    )
    row = await _row(db_client)
    assert row["merged_verdict"] == merged_router.VERDICT_FAILED
    assert "participants" in (row["merged_truncated"] or "")


async def test_the_menu_knob_does_not_shrink_the_match_landing(
    service, db_client, merged_on, monkeypatch
):
    """Review round 3, I2: MERGED_MENU_SIZE is an env knob that caps the
    BALLOT; the landing's trailing context must answer to
    MAX_NARRATIVES_IN_CONTEXT alone. With the menu squeezed to 1, a match
    verdict must still hand the ChatModule the same number of threads the
    two-call path would."""
    anchor, _foreign = await _seed(service)
    # A second thread sharing the foreign query's subject, so the full
    # ranking holds two scored non-anchor rows while the ballot holds one.
    await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="纽约餐厅预订记录", description="",
    )
    monkeypatch.setattr(config, "MERGED_MENU_SIZE", 1)
    _sdk(monkeypatch, verdict=merged_router.VERDICT_MATCH, index=0)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_match"
    assert len(result.narratives) >= 2, (
        "the landing shrank with the menu knob — trailing context must come "
        "from the full ranking, not the ballot"
    )
    assert all(n.id != anchor.id for n in result.narratives)


async def test_rule6_a_prompt_assembly_failure_is_a_failure_not_a_dead_turn(
    service, db_client, merged_on, monkeypatch
):
    """Review round 5, I2: rule 6 must cover assembly, not just the call.
    Un-caught, a bad candidate field kills the user's whole turn on the
    merged path while the two-call path degrades with a warning."""
    anchor, _ = await _seed(service)
    _sdk(monkeypatch, verdict=merged_router.VERDICT_CONTINUE_ANCHOR)

    def _boom(inp):
        raise KeyError("a candidate field went missing")

    monkeypatch.setattr(merged_router, "build_merged_prompt", _boom)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_fallback_anchor"
    assert [n.id for n in result.narratives] == [anchor.id]
    row = await _row(db_client)
    assert row["merged_verdict"] == merged_router.VERDICT_FAILED
    assert row["merged_input_chars"] is None  # no prompt was built


async def test_a_menu_row_from_beyond_the_snippet_head_still_carries_evidence(
    service, db_client
):
    """Review round 6, I2: pick_menu selects AFTER excluding the anchor and
    scoring participants, so a menu row can come from past the snippet head
    window with matched_snippet="". The snippet is the load-bearing half of
    the evidence (terms alone mislead — the CJK frame-word collision), and a
    bare row also trips the wiring-broken alarm. build_menu_candidates must
    backfill it."""
    from narranexus.platform.narrative.models import NarrativeSearchResult
    from narranexus.platform.narrative._narrative_impl.landings import (
        build_menu_candidates,
    )

    thread = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="部署脚本报错排查", description="",
    )
    bare_row = NarrativeSearchResult(
        narrative_id=thread.id, similarity_score=0.9, rank=7,
        raw_score=9.0, matched_terms=["部署"], matched_snippet="",
    )

    candidates = await build_menu_candidates(service._crud, [bare_row])

    assert candidates[0]["matched_content"], (
        "a scored menu row reached the model without its evidence — the "
        "snippet must be backfilled for rows beyond the head window"
    )
    assert "部署" in candidates[0]["matched_content"]


async def test_an_anchor_that_is_also_a_participant_is_not_on_the_ballot_twice(
    service, db_client, merged_on, monkeypatch
):
    """Review round 9, I1: after a participant landing, the landed thread is
    the next turn's anchor AND still in the participant list. Unfiltered, it
    rendered in two sections, two verdicts pointed at it, and a "stay" was
    audited as a "switch" (merged_participant) — polluting the very columns
    the flag exists to read. The participant SECTION must exclude the anchor;
    participant index 0 must resolve to the OTHER invitation."""
    anchor, _ = await _seed(service)
    other = await service.create_narrative(
        agent_id=AGENT, user_id="user_owner_x", title="Invited task", description="",
    )

    async def _participants(self, *, user_id, agent_id):
        return [anchor, other]

    from narranexus.platform.narrative._narrative_impl.retrieval import (
        NarrativeRetrieval,
    )
    monkeypatch.setattr(
        NarrativeRetrieval, "_get_participant_narratives", _participants
    )
    _sdk(monkeypatch, verdict=merged_router.VERDICT_PARTICIPANT, index=0)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_participant"
    assert result.narratives[0].id == other.id, (
        "participant index 0 resolved to the anchor — it was on the ballot "
        "twice, and a stay would be audited as a switch"
    )


# ===================================================================== #
# Instruments and contracts                                             #
# ===================================================================== #


async def test_the_merged_row_records_what_it_cost_and_what_it_read(
    service, db_client, merged_on, monkeypatch
):
    anchor, _ = await _seed(service)
    sdk = _sdk(monkeypatch, verdict=merged_router.VERDICT_CONTINUE_ANCHOR)

    await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert row["merged_ms"] is not None
    assert row["merged_input_chars"] == (
        len(sdk.calls[0]["instructions"]) + len(sdk.calls[0]["user_input"])
    ), (
        "the latency model's x-axis counts EVERYTHING the call sends — "
        "instructions vary by variant and the variant correlates with the "
        "turn shape (review round 2, I2)"
    )
    assert row["candidates"], "the pool that produced the menu is on the row"
    assert row["bypass_score_gate"] is not None, (
        "the floor/margin series must keep accumulating independently — the day "
        "a new rule ships is the day the old distribution stops, unless it is "
        "recorded separately (R3.3)"
    )


async def test_retrieve_ms_on_a_merged_row_is_the_bm25_pass_alone(
    service, db_client, merged_on, monkeypatch
):
    """Third population, same column. On the two-call path `retrieve_ms`
    CONTAINS `judge_ms` — an ambiguity that already sent two readers to the same
    wrong conclusion ("routing spends 6 seconds in the database"). On a merged
    row the LLM has its own column, so retrieve_ms must be the scoring pass
    only: a slow model may not leak into it."""
    anchor, _ = await _seed(service)

    class _SlowSdk(_Sdk):
        async def llm_function(self, **kw):
            import asyncio

            await asyncio.sleep(0.25)
            return await super().llm_function(**kw)

    sdk = _SlowSdk(verdict=merged_router.VERDICT_CONTINUE_ANCHOR)
    monkeypatch.setattr(merged_router, "get_helper_sdk", lambda: sdk)

    await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert row["merged_ms"] >= 250
    assert row["retrieve_ms"] < row["merged_ms"], (
        "the merged call's latency leaked into the retrieval tier's column"
    )


async def test_a_truncated_prompt_section_is_flagged_never_silent(
    service, db_client, merged_on, monkeypatch
):
    anchor, _ = await _seed(service)
    _sdk(monkeypatch, verdict=merged_router.VERDICT_CONTINUE_ANCHOR)
    session = _session(anchor.id)
    session.last_response = "回复开头" + ("x" * 9000)

    await service.select(
        AGENT, USER, FOREIGN_QUERY, session=session,
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert "prev_response" in (row["merged_truncated"] or "")


async def test_downstream_cannot_tell_who_decided(
    service, db_client, merged_on, monkeypatch
):
    """step_1 / step_4 read `narratives` / `is_new` / `no_durable_topic` /
    `retrieval_method` and never branch on `selection_method`. The merged path
    fills the same contract, so nothing downstream needs to know."""
    anchor, _ = await _seed(service)
    _sdk(monkeypatch, verdict=merged_router.VERDICT_CONTINUE_ANCHOR)
    session = _session(anchor.id)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=session,
        trigger="chat", is_user_chat=True,
    )

    assert result.narratives and result.selection_reason
    assert result.retrieval_method
    assert result.is_new is False
    assert result.no_durable_topic is False
    # The observability surface is downstream too (round 8, I2): select()
    # leaves these empty at its own exit, so the merged arm must match —
    # a panel that shows scores on one arm only outs the decider.
    assert result.best_score is None
    assert result.scores == {}
    assert session.current_narrative_id == anchor.id, (
        "the session anchor advances by the same rule on both paths"
    )


async def test_a_background_turn_never_moves_the_anchor_on_the_merged_path_either(
    service, db_client, merged_on, monkeypatch
):
    anchor, foreign = await _seed(service)
    _sdk(monkeypatch, verdict=merged_router.VERDICT_MATCH, index=0)
    session = _session(anchor.id)

    await service.select(
        AGENT, USER, FOREIGN_QUERY, session=session,
        trigger="job", is_user_chat=False,
    )

    assert session.current_narrative_id == anchor.id, (
        "background traffic must leave the next user message's continuity "
        "anchored to the last real exchange"
    )


async def test_the_new_columns_are_registered_on_both_dialects():
    from narranexus.platform.utils.db.schema_registry import TABLES

    columns = {c.name: c for c in TABLES["narrative_routing_audit"].columns}
    for name in (
        "merged_call", "merged_verdict", "merged_ms", "merged_input_chars",
        "merged_truncated", "anchor_bm25_rank", "anchor_raw_score",
        "anchor_in_menu",
    ):
        col = columns.get(name)
        assert col is not None, f"narrative_routing_audit.{name} not registered"
        assert col.sqlite_type and col.mysql_type
        assert col.nullable, (
            "prod rows predate this column; NOT NULL would fail the ALTER on a "
            "live table (binding rule #6)"
        )
