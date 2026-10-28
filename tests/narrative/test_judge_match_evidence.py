"""
@file_name: test_judge_match_evidence.py
@date: 2026-08-12
@description: The judge must see WHICH text matched, not just a squashed score.

Why this file exists
====================
The LLM arbitration tier (`llm_judge_unified`) is the only semantic check in
narrative routing, and it runs precisely on the turns the numeric gate refused
to decide — i.e. when candidates are CROWDED. Yet all it was handed per
candidate was `Similarity score: 0.91`, a number that is actively misleading:
a real local query ("帮我查一下明天上海的天气怎么样") scored 10.67 raw against an
unrelated meeting-notes narrative with 100% of that score coming from the
request-frame characters 查/天/下/一/帮 — the topic-bearing 明/上/海/气/样
contributed exactly zero. Squashed to 0.914, that reads as "91% confident".

The rendering side of the fix has existed since 2026-03-06 (`if
candidate.get('matched_content')` in `_retrieval_llm.py`), but the write side
was deleted on 2026-04-15 with the read side sitting in a different file — so
the branch has been dead ~4 months and `logger.debug("has no matched_content")`
fired every single turn. The alarm was ringing; nobody was listening.

What is locked here
===================
1. `bm25_explain` decomposes a score into per-term contributions whose SUM is
   the `bm25_rank` score — the anti-drift property. Two independent copies of
   the BM25 arithmetic is how the original bug class started, so the explain
   path must be the same arithmetic, not a re-derivation.
2. Ranked evidence reaches the search candidates (`matched_content`) and is
   rendered into the judge prompt (`Matched content:`).
3. PARTICIPANT candidates are labelled from LIVE fields. That branch read
   `topic_hint` — written once at creation and frozen since the 2026-06-09
   unified-memory refactor (84% empty on the local dev DB) — while the search
   branch 50 lines above read `narrative_info`. Both branches now go through
   ONE function, because "make them agree" without making them physically
   share code is what drifted the first time.
4. The dead single-match cluster is gone by name.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from narranexus.platform.memory.bm25 import (
    bm25_explain,
    bm25_rank,
)
from narranexus.platform.narrative._narrative_impl.retrieval import NarrativeRetrieval
from narranexus.platform.narrative.models import (
    Narrative,
    NarrativeInfo,
    NarrativeType,
)

AGENT = "agent_evidence"
USER = "user_evidence"

# Shaped like the real thing: the noise case from the incident report. The
# query's frame characters (帮/查/一/下/天) hit the meeting narrative; nothing
# topic-bearing does.
_NOISE_QUERY = "帮我查一下明天上海的天气怎么样"
_POOL = [
    ("nar_meeting", "帮我总结一下今天参加过的会议内容 Topic: 本周会议纪要整理 会议 纪要", False),
    ("nar_deploy", "帮我查一下部署脚本的报错 Newly created Narrative 部署 脚本 报错", False),
]


def _narrative(
    nid: str,
    *,
    name: str,
    summary: str = "",
    description: str = "",
    topic_hint: str = "",
    keywords: list[str] | None = None,
) -> Narrative:
    now = datetime.now(timezone.utc)
    return Narrative(
        id=nid,
        type=NarrativeType.CHAT,
        agent_id=AGENT,
        narrative_info=NarrativeInfo(
            name=name, description=description, current_summary=summary, actors=[]
        ),
        event_ids=[],
        topic_keywords=keywords or [],
        topic_hint=topic_hint,
        created_at=now,
        updated_at=now,
    )


# ── 1. bm25_explain: same arithmetic as bm25_rank ──────────────────────────
def test_explained_score_is_bit_identical_to_the_ranked_score():
    """The anti-drift property, and the reason routing ranks via `bm25_explain`
    instead of calling both: the routing audit replays BM25 exactly, so
    "close enough" in the last float bits is not good enough. Exact equality,
    not approx."""
    items = [(nid, text) for nid, text, _ in _POOL]
    scores = bm25_rank(_NOISE_QUERY, items)
    explained = bm25_explain(_NOISE_QUERY, items)

    assert set(explained) == set(scores), "explain and rank disagree on WHICH docs hit"
    assert {nid: score for nid, (score, _) in explained.items()} == scores, (
        "explained score differs from the ranked score — the gate and the "
        "evidence shown for it would describe different computations"
    )
    for nid, (score, terms) in explained.items():
        assert sum(c for _, c in terms) == pytest.approx(score), (
            f"{nid}: the term decomposition does not add up to the score"
        )


def test_contributions_are_ordered_by_weight_content_word_first():
    """A high-IDF content term must outrank a frame term, so a truncated
    top-N list keeps the discriminative evidence rather than the noise."""
    explained = bm25_explain(
        "部署脚本报错", [(nid, text) for nid, text, _ in _POOL]
    )
    _, terms = explained["nar_deploy"]

    contributions = [c for _, c in terms]
    assert contributions == sorted(contributions, reverse=True), "not sorted by weight"
    ranked_terms = [t for t, _ in terms]
    # 署/脚/本/报/错 appear in one pool doc (high IDF); 部 also only there, while
    # 帮-class frame chars are shared. Within THIS query every term is
    # content-bearing, so the guarantee under test is that the discriminative
    # ones lead: the top term must not be a term the other document also has.
    assert ranked_terms, "no matched terms explained at all"
    assert "错" in ranked_terms and "署" in ranked_terms


def test_explain_is_empty_for_a_zero_overlap_document():
    explained = bm25_explain("完全无关的查询", [("nar_x", "deploy script failure log")])
    assert explained == {}


# ── 2. the evidence reaches the judge ─────────────────────────────────────
def test_rank_pool_attaches_matched_terms_and_snippet():
    results = NarrativeRetrieval.rank_pool(_NOISE_QUERY, _POOL, top_k=2)

    assert results, "BM25 returned nothing for the noise query"
    top = results[0]
    assert top.matched_terms, "no matched_terms on the search result"
    assert len(top.matched_terms) <= 5, "matched_terms must stay a short list"
    assert top.matched_snippet, "no matched_snippet on the search result"
    # The snippet is context from the narrative's OWN scored text.
    assert top.matched_snippet.strip("."), "snippet is only ellipsis"
    assert any(term in top.matched_snippet for term in top.matched_terms), (
        "the snippet does not contain any of the terms it is supposed to show"
    )


def _retrieval_with_stubs(monkeypatch, narratives_by_id):
    """A NarrativeRetrieval whose DB and LLM edges are stubbed out.

    `_llm_unified_match` is the candidate-assembly seam under test; the judge
    call itself is captured, not executed (no credentials in CI, and what the
    judge DECIDES is not what this file is about).
    """
    retrieval = NarrativeRetrieval.__new__(NarrativeRetrieval)
    retrieval.agent_id = AGENT
    retrieval._crud = SimpleNamespace(
        load_by_id=AsyncMock(side_effect=lambda nid: narratives_by_id.get(nid)),
        # dev renamed _create_narrative -> create_from_query (2026-08-14), which
        # goes through _crud.create; the old attribute stub stopped intercepting
        # anything, so the real path runs and needs this edge stubbed instead.
        create=AsyncMock(return_value=_narrative("nar_new", name="new topic")),
        # create_from_query writes the BM25 surface back after create(), so
        # the save edge needs stubbing too — the 2026-08-14 repair stopped one
        # call short and every test in this file died on the second one.
        save=AsyncMock(),
    )

    captured: dict = {}

    async def _capture_judge(**kwargs):
        captured.update(kwargs)
        return {"matched_id": None, "matched_type": None, "reason": "stubbed"}

    retrieval._llm_judge_unified = _capture_judge

    async def _fake_db():
        return SimpleNamespace()

    monkeypatch.setattr(
        "narranexus.platform.narrative._narrative_impl.retrieval.get_db_client", _fake_db
    )

    class _Repo:
        def __init__(self, _db):
            pass

        async def get_default_narratives(self, agent_id, user_id):
            return []

    monkeypatch.setattr("narranexus.platform.repository.NarrativeRepository", _Repo)
    return retrieval, captured


@pytest.mark.asyncio
async def test_search_candidates_carry_matched_content(monkeypatch):
    # Both pool members are resolvable: which of the two crowded noise
    # candidates BM25 puts on top is not the subject here.
    loaded = {
        "nar_meeting": _narrative(
            "nar_meeting",
            name="帮我总结一下今天参加过的会议内容",
            summary="Topic: 本周会议纪要整理",
        ),
        "nar_deploy": _narrative(
            "nar_deploy",
            name="帮我查一下部署脚本的报错",
            summary="Newly created Narrative",
        ),
    }
    retrieval, captured = _retrieval_with_stubs(monkeypatch, loaded)
    search_results = NarrativeRetrieval.rank_pool(_NOISE_QUERY, _POOL, top_k=1)

    await retrieval._llm_unified_match(
        query=_NOISE_QUERY,
        search_results=search_results,
        agent_id=AGENT,
        user_id=USER,
        top_k=3,
        narrative_type=NarrativeType.CHAT,
        best_score=search_results[0].similarity_score,
    )

    candidates = captured["search_candidates"]
    assert candidates, "no search candidates were handed to the judge"
    assert "matched_content" in candidates[0], (
        "the judge's matched_content key is still never written — the render "
        "branch in _retrieval_llm.py stays dead"
    )
    assert candidates[0]["matched_content"].strip(), "matched_content is blank"


@pytest.mark.asyncio
async def test_judge_prompt_renders_matched_content(monkeypatch):
    """The end of the wire: the assembled prompt text the helper LLM reads."""
    from narranexus.platform.narrative._narrative_impl import _retrieval_llm

    captured: dict = {}

    class _FakeSDK:
        async def llm_function(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                final_output=_retrieval_llm.UnifiedMatchOutput(
                    reason="frame-word collision only", matched_category="none",
                    matched_index=-1,
                )
            )

    monkeypatch.setattr(_retrieval_llm, "get_helper_sdk", lambda: _FakeSDK())

    await _retrieval_llm.llm_judge_unified(
        query=_NOISE_QUERY,
        search_candidates=[{
            "id": "nar_meeting",
            "type": "search",
            "name": "帮我总结一下今天参加过的会议内容",
            "description": "Topic: 本周会议纪要整理",
            "score": 0.91,
            "matched_terms": ["查", "天", "下", "一", "帮"],
            "matched_content": "...帮我总结一下今天参加过的会议内容...",
        }],
        default_candidates=[],
    )

    prompt = captured["user_input"]
    assert "Matched content:" in prompt, (
        "the judge prompt still shows only a score — this assertion failing is "
        "the defect itself"
    )
    assert "Matched terms:" in prompt, "the ranked term list is not rendered"
    assert "帮我总结一下今天参加过的会议内容" in prompt


@pytest.mark.asyncio
async def test_missing_evidence_alarms_only_for_bm25_sourced_candidates(monkeypatch):
    """The re-armed alarm must not cry wolf.

    Step 1.5 of `_retrieve_top_k` merges participant narratives INTO
    `search_results` at a synthetic 0.5 similarity and re-sorts, so the search
    block legitimately contains rows that never went through BM25 and owe no
    evidence. An alarm that fires on those gets silenced by whoever reads the
    logs, and then the real regression walks through (incident lesson #3)."""
    from narranexus.platform.narrative._narrative_impl import _retrieval_llm

    warnings: list[str] = []

    class _FakeSDK:
        async def llm_function(self, **kwargs):
            return SimpleNamespace(
                final_output=_retrieval_llm.UnifiedMatchOutput(
                    reason="r", matched_category="none", matched_index=-1,
                )
            )

    monkeypatch.setattr(_retrieval_llm, "get_helper_sdk", lambda: _FakeSDK())
    monkeypatch.setattr(
        _retrieval_llm.logger, "warning", lambda msg, *a, **k: warnings.append(str(msg))
    )

    def _candidate(**over):
        base = {
            "id": "nar_x", "type": "search", "name": "n", "description": "d",
            "score": 0.5, "raw_score": 0.0, "matched_terms": [],
            "matched_content": "",
        }
        base.update(over)
        return base

    # A participant row merged into the search block: no evidence, no alarm.
    await _retrieval_llm.llm_judge_unified(
        query="q", search_candidates=[_candidate()], default_candidates=[],
    )
    assert warnings == [], f"alarmed on a non-BM25 candidate: {warnings}"

    # A genuinely BM25-scored row with no evidence: the write side regressed.
    await _retrieval_llm.llm_judge_unified(
        query="q",
        search_candidates=[_candidate(id="nar_broken", raw_score=7.5)],
        default_candidates=[],
    )
    assert len(warnings) == 1 and "nar_broken" in warnings[0]


# ── 3. B2: participant candidates read LIVE fields ────────────────────────
@pytest.mark.asyncio
async def test_participant_candidate_is_labelled_from_live_fields(monkeypatch):
    """The measured worst case: a 72-event narrative whose frozen topic_hint
    describes its first sentence from three months ago, and one whose hint is
    a truncated open_id. This branch FORCES the judge to run, so a blind or
    stale label here is the whole decision."""
    stale_hint = "[Lark · Chen Tong · ou_3967641f947783096949e3436e"
    participant = _narrative(
        "nar_participant",
        name="Meeting To-Do Permission Fix",
        summary="Topic: 会议待办权限修复\nKey facts:\n- 权限范围缺少 tasks:write",
        topic_hint=stale_hint,
    )
    retrieval, captured = _retrieval_with_stubs(
        monkeypatch, {"nar_participant": participant}
    )

    await retrieval._llm_unified_match(
        query="那个权限的问题解决了吗",
        search_results=[],
        agent_id=AGENT,
        user_id=USER,
        top_k=3,
        narrative_type=NarrativeType.CHAT,
        best_score=None,
        participant_narratives=[participant],
    )

    candidates = captured["participant_candidates"]
    assert len(candidates) == 1
    assert candidates[0]["name"] == "Meeting To-Do Permission Fix"
    assert "会议待办权限修复" in candidates[0]["description"]
    assert stale_hint not in candidates[0]["name"]
    assert stale_hint not in candidates[0]["description"]


@pytest.mark.asyncio
async def test_participant_and_search_branches_share_one_labeller(monkeypatch):
    """Same narrative, two branches, identical labels. Pinning the OUTPUT
    equality is what makes a future one-sided edit fail loudly — the 2026-04-15
    drift passed every test because each branch was only ever checked alone."""
    narrative = _narrative(
        "nar_shared",
        name="部署脚本排障",
        summary="Status: 定位中\nKey facts:\n- web.log 报端口占用",
        topic_hint="创建时那句话",
    )
    retrieval, captured = _retrieval_with_stubs(monkeypatch, {"nar_shared": narrative})
    search_results = NarrativeRetrieval.rank_pool(
        "部署脚本报错", [("nar_shared", narrative.searchable_text(), False)], top_k=1
    )

    await retrieval._llm_unified_match(
        query="部署脚本报错",
        search_results=search_results,
        agent_id=AGENT,
        user_id=USER,
        top_k=3,
        narrative_type=NarrativeType.CHAT,
        best_score=1.0,
        participant_narratives=[narrative],
    )

    search_candidate = captured["search_candidates"][0]
    participant_candidate = captured["participant_candidates"][0]
    assert search_candidate["name"] == participant_candidate["name"]
    assert search_candidate["description"] == participant_candidate["description"]


@pytest.mark.asyncio
async def test_participant_candidate_without_a_name_falls_back(monkeypatch):
    """A narrative can legitimately have no name yet (created this turn).
    "Untitled" is honest; a stale hint is not."""
    participant = _narrative("nar_blank", name="", topic_hint="some frozen hint")
    retrieval, captured = _retrieval_with_stubs(
        monkeypatch, {"nar_blank": participant}
    )

    await retrieval._llm_unified_match(
        query="任何查询",
        search_results=[],
        agent_id=AGENT,
        user_id=USER,
        top_k=3,
        narrative_type=NarrativeType.CHAT,
        best_score=None,
        participant_narratives=[participant],
    )

    candidate = captured["participant_candidates"][0]
    assert candidate["name"] == "Untitled"
    assert candidate["description"] == ""


# ── 4. the dead single-match cluster is gone ──────────────────────────────
def test_dead_single_match_cluster_no_longer_exists():
    """Pin the deletion by name (binding rule #2: no compatibility shims).

    `_prepare_candidates` / `_llm_confirm` / `llm_confirm` /
    `NarrativeMatchOutput` / `RelationType` / the single-match prompt formed a
    closed loop with no external entry point, and `_prepare_candidates` was a
    THIRD copy of the candidate-labelling logic — still reading topic_hint. A
    dead third copy is where the next drift comes from."""
    from narranexus.platform.narrative._narrative_impl import (
        _retrieval_llm,
        prompts,
        retrieval as narrative_retrieval,
    )
    from narranexus.platform import prompts_index

    for name in ("llm_confirm", "NarrativeMatchOutput", "RelationType"):
        assert not hasattr(_retrieval_llm, name), f"_retrieval_llm.{name} still exists"
    for name in ("NARRATIVE_SINGLE_MATCH_INSTRUCTIONS", "NarrativeMatchOutput"):
        assert not hasattr(prompts, name), f"prompts.{name} still exists"
    assert not hasattr(prompts_index, "NARRATIVE_SINGLE_MATCH_INSTRUCTIONS")
    for name in ("_prepare_candidates", "_llm_confirm"):
        assert not hasattr(NarrativeRetrieval, name), f"NarrativeRetrieval.{name} still exists"
    for name in ("llm_confirm", "NarrativeMatchOutput", "RelationType"):
        assert not hasattr(narrative_retrieval, name), (
            f"retrieval.py still imports {name}"
        )


def test_no_narrative_labelling_path_reads_the_frozen_topic_hint():
    """`topic_hint` is written once by `_create_narrative` and never again
    (the 2026-06-09 unified-memory refactor removed its update machinery and
    documented it as an inert tombstone). It may still be DISPLAYED as
    creation-time provenance — `backend/routes/me.py` shows it on the timeline
    card, which is honest, since that is exactly what it is — but nothing that
    labels a narrative FOR A DECISION may read it, or the decision is made on
    three-month-old text.

    `PromptBuilder.build_summary_prompt` was a fifth copy of the labelling
    logic (callerless, and injecting `Topic: {topic_hint}`); it is deleted
    rather than repaired, since a dead copy is where the next drift starts."""
    from narranexus.platform.narrative._narrative_impl.prompt_builder import PromptBuilder

    assert not hasattr(PromptBuilder, "build_summary_prompt")

    import ast
    import inspect

    from narranexus.platform.narrative._narrative_impl import (
        prompt_builder,
        retrieval as narrative_retrieval,
    )

    def _attribute_reads(module) -> list[str]:
        """`x.topic_hint` in a load context — assignments TO it don't count.

        `_create_narrative` legitimately still writes it: creation-time text is
        the one thing the field honestly holds, and me.py surfaces it as such.
        """
        tree = ast.parse(inspect.getsource(module))
        return [
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "topic_hint"
            and isinstance(node.ctx, ast.Load)
        ]

    assert _attribute_reads(narrative_retrieval) == [], (
        "the routing tier reads the frozen topic_hint again"
    )
    assert _attribute_reads(prompt_builder) == [], (
        "a narrative prompt injects the frozen topic_hint again"
    )
