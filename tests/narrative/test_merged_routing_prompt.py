"""
@file_name: test_merged_routing_prompt.py
@date: 2026-08-26
@description: The merged routing prompt's input contract — the iron rules that
are decided at PROMPT-CONSTRUCTION time, plus the read-side input budget.

WHY THIS FILE EXISTS (specs/2026-08-25-merged-routing-design.md §3.2)

Today a continuity turn returns BEFORE the retrieval tier, so the BM25 menu is
never built and a foreign thread has no way into any prompt. Merging the two
calls opens that door on every turn, and the measurement behind §3.2 says what
walks through it: on continuity turns the anchor thread is **not in the BM25
top-3 in 26.2%-71.6% of turns**, and the menu's first row is a foreign thread in
26.4%-93.8%. The p07 hijack specimen was caught, turn after turn, by exactly the
defence merging removes (`pool=0`, BM25 never ran).

So the anchor's presence in the prompt cannot be a consequence of its score.
It is rendered unconditionally, in its own section, deduplicated against the
menu — a data-forced structural constraint, not a style choice. Each rule below
is one of those constraints, asserted at the level where it is decided.

THE INPUT BUDGET (read-side only)

Every cap here clamps what the PROMPT shows, never what is stored: the same
`Narrative` row, the same `Session`, the same message. Head-preserving on
purpose — the referent of a follow-up ("讲第一个" / "the first one") lives at the
START of the agent's previous reply, so a tail-preserving clamp would drop the
very thing the merged call needs the previous turn for. Every clamp reports
itself into the audit row; a silently shortened prompt is one nobody can explain
later.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from narranexus.platform.narrative._narrative_impl import (
    merged_router,
    prompts,
    routing_blocks,
)
from narranexus.platform.narrative._narrative_impl import prompts_merged
from narranexus.platform.narrative._narrative_impl import routing_gate
from narranexus.platform.narrative._narrative_impl.merged_router import (
    MergedRoutingInput,
    build_merged_prompt,
)
from narranexus.platform.narrative.config import config
from narranexus.platform.narrative.models import (
    Narrative,
    NarrativeInfo,
    NarrativeSearchResult,
    NarrativeType,
)

AGENT = "agent_merged"


def _narrative(
    nid: str,
    *,
    name: str,
    summary: str = "",
    description: str = "",
    keywords: list[str] | None = None,
    is_special: str = "other",
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
        is_special=is_special,
        created_at=now,
        updated_at=now,
    )


def _candidate(nid: str, *, name: str, summary: str = "") -> dict:
    return {
        "id": nid,
        "type": "search",
        "name": name,
        "description": summary,
        "score": 0.87,
        "raw_score": 6.5,
        "matched_terms": ["部署", "脚本"],
        "matched_content": "…部署脚本报错…",
    }


def _input(**overrides) -> MergedRoutingInput:
    base = dict(
        query="那第二步呢",
        anchor=_narrative(
            "nar_anchor", name="部署脚本报错排查", summary="在排查 CI 部署脚本的报错"
        ),
        anchor_is_continuable=True,
        previous_query="部署脚本报错怎么修",
        previous_response="先看日志的最后 20 行",
        minutes_since_previous=1.5,
        menu=[_candidate("nar_other", name="纽约餐厅推荐")],
        participants=[],
        awareness="You are a devops assistant.",
    )
    base.update(overrides)
    return MergedRoutingInput(**base)


# ===================================================================== #
# Rule 1 · the anchor section exists unconditionally                    #
# ===================================================================== #


def test_rule1_anchor_section_is_rendered_when_the_menu_is_empty():
    """An empty pool is the case where the anchor block matters most: there is
    no menu to infer the thread from, so omitting it would leave the model with
    nothing but the previous turn."""
    prompt = build_merged_prompt(_input(menu=[]))

    assert "部署脚本报错排查" in prompt.user_input
    assert "在排查 CI 部署脚本的报错" in prompt.user_input


def test_rule1_anchor_section_survives_a_zero_bm25_score():
    """§3.2: the anchor scores ZERO on 8.2%-49.3% of continuity turns (zero
    lexical overlap between consecutive turns of one thread is normal). Its
    place in the prompt must not be a function of its score — here the menu is
    nothing but a foreign thread and the anchor is still rendered."""
    prompt = build_merged_prompt(
        _input(menu=[_candidate("nar_far", name="明天上海的天气")])
    )

    assert "部署脚本报错排查" in prompt.user_input
    assert "明天上海的天气" in prompt.user_input


def test_rule1_anchorless_turn_says_so_instead_of_omitting_the_section():
    """A missing section reads to the model as "not mentioned"; an explicit
    "nothing anchored" reads as a fact. The judge prompt already learned this
    with its empty-menu header (2026-08-12)."""
    prompt = build_merged_prompt(_input(anchor=None, anchor_is_continuable=False))

    assert merged_router.ANCHOR_ABSENT_NOTE in prompt.user_input


def test_rule1_a_non_continuable_anchor_is_shown_but_not_offered():
    """C-1 slice 5: a legacy default bucket is a VERDICT about an earlier turn,
    not a thread — `is_reusable_anchor` says so at all three decision points.
    It is still SHOWN (the model has to know where the last turn was filed),
    but staying on it is not among the answers."""
    prompt = build_merged_prompt(
        _input(
            anchor=_narrative(
                "nar_bucket", name="GreetingAndCourtesy", is_special="default"
            ),
            anchor_is_continuable=False,
        )
    )

    assert merged_router.ANCHOR_NOT_CONTINUABLE_NOTE in prompt.user_input
    # "Not offered" must hold in the INSTRUCTIONS too, not just the note: an
    # answer table that still lists continue_anchor as "the default" invites
    # the one verdict _contract_violation is guaranteed to refuse, and the
    # refusal lands as merged_fallback_new — the D19 shape (review Critical 1).
    assert "continue_anchor" not in prompt.instructions


def test_rule1_an_anchorless_turn_does_not_offer_continue_either():
    """Same contract as the bucket case: with nothing anchored there is
    nothing to continue, so the verdict must not be on the menu of answers."""
    prompt = build_merged_prompt(_input(anchor=None, anchor_is_continuable=False))

    assert "continue_anchor" not in prompt.instructions


def test_rule1_no_variant_invites_continuing_a_legacy_container():
    """The core prompt used to carry "if the user is plainly carrying on from
    the previous turn, the thread continues even there" — an instruction whose
    only reachable outcome was an off-contract answer. routing_blocks deleted
    _LEGACY_BUCKET_NOTE for exactly this contradiction; the merged core must
    not reintroduce it."""
    for continuable in (True, False):
        for with_participants in (True, False):
            text = prompts_merged.build_merged_instructions(
                anchor_is_continuable=continuable,
                with_participants=with_participants,
            )
            assert "continues even there" not in text
            assert ("continue_anchor" in text) == continuable


# ===================================================================== #
# Rule 2 · the previous turn is present, and it comes FIRST             #
# ===================================================================== #


def test_rule2_previous_turn_is_the_first_thing_in_the_prompt():
    """The continuity tier's exclusive input, and the reason a zero-overlap
    follow-up ("那第二步呢") can be routed at all. Order is priority: it leads."""
    prompt = build_merged_prompt(_input())
    body = prompt.user_input

    assert "部署脚本报错怎么修" in body
    assert "先看日志的最后 20 行" in body
    first = body.index("部署脚本报错怎么修")
    assert first == min(
        first,
        body.index("部署脚本报错排查"),   # anchor section
        body.index("纽约餐厅推荐"),        # menu
        body.index("那第二步呢"),          # this turn
    ), "the previous turn must lead the prompt"


def test_rule2_time_elapsed_rides_with_the_previous_turn():
    prompt = build_merged_prompt(_input(minutes_since_previous=42.0))
    assert "42.0 minutes" in prompt.user_input


def test_rule2_a_proactive_previous_turn_uses_the_agent_message_variant():
    """A scheduled job messaged the user unprompted: there is no previous user
    query, and a bare "好" is almost certainly answering THAT. The continuity
    prompt already carries this variant; the merged prompt inherits it rather
    than growing a fourth copy."""
    prompt = build_merged_prompt(
        _input(previous_query="", previous_response="任务跑完了,要看结果吗?")
    )

    assert "proactively" in prompt.user_input
    assert "任务跑完了" in prompt.user_input


def test_rule2_a_genuinely_first_turn_says_so():
    prompt = build_merged_prompt(
        _input(previous_query="", previous_response="", minutes_since_previous=None)
    )
    assert merged_router.NO_PREVIOUS_TURN_NOTE in prompt.user_input


# ===================================================================== #
# Rule 4 · participant priority stays structural, never fused           #
# ===================================================================== #


def test_rule4_participants_get_their_own_section_ahead_of_the_menu():
    prompt = build_merged_prompt(
        _input(
            participants=[
                {"id": "nar_task", "type": "participant",
                 "name": "客户 A 的报价单", "description": "由销售发起"}
            ]
        )
    )
    body = prompt.user_input

    assert body.index("客户 A 的报价单") < body.index("纽约餐厅推荐"), (
        "a task the user was INVITED into must not be ranked below a keyword "
        "hit on their own thread (P0-4)"
    )


def test_rule4_the_participant_variant_is_the_one_that_states_the_priority():
    with_participant = build_merged_prompt(
        _input(participants=[{"id": "p", "type": "participant",
                              "name": "n", "description": "d"}])
    )
    without = build_merged_prompt(_input())

    assert with_participant.instructions == prompts_merged.build_merged_instructions(
        anchor_is_continuable=True, with_participants=True
    )
    assert without.instructions == prompts_merged.build_merged_instructions(
        anchor_is_continuable=True, with_participants=False
    )
    assert "PARTICIPANT" in with_participant.instructions
    assert "participant" not in without.instructions


def test_rule4_the_prompt_renders_exactly_what_the_input_carries():
    """The cut lives at ONE place — merged_select's entry (round 6, minor 2):
    `inp.participants` is both the render source and the contract's index
    bound, so the renderer must NOT re-cap. What enters is what shows, in
    priority order (P0-4 forbids reordering). The entry cut itself is pinned
    end-to-end by test_rule6_an_index_into_the_unrendered_participant_tail_
    is_refused."""
    many = [
        {"id": f"nar_p{i}", "type": "participant", "name": f"任务 {i}",
         "description": ""}
        for i in range(config.MERGED_PARTICIPANT_MAX_CANDIDATES + 3)
    ]
    prompt = build_merged_prompt(_input(participants=many))

    for i in range(config.MERGED_PARTICIPANT_MAX_CANDIDATES + 3):
        assert f"任务 {i}" in prompt.user_input, (
            "the renderer re-capped — the ballot/render split (round 2, I1) "
            "reopens for any constructor that skips the entry cut"
        )
    first = prompt.user_input.index("任务 0")
    second = prompt.user_input.index("任务 1")
    assert first < second  # order is the priority rule


# ===================================================================== #
# Rule 5 · the anchor is deduplicated against the menu                  #
# ===================================================================== #


def test_rule5_the_anchor_is_removed_from_the_menu():
    """Rendered twice, the anchor reads as two candidates and the asymmetry
    ("staying is the default, the menu is evidence for LEAVING") collapses."""
    ranked = [
        NarrativeSearchResult(narrative_id="nar_anchor", similarity_score=0.9,
                              rank=1, raw_score=9.0),
        NarrativeSearchResult(narrative_id="nar_other", similarity_score=0.5,
                              rank=2, raw_score=1.0),
    ]

    menu = routing_gate.pick_menu(ranked, exclude_ids={"nar_anchor"}, limit=3)

    assert [r.narrative_id for r in menu] == ["nar_other"]


def test_rule5_a_participant_thread_is_excluded_from_the_menu_too():
    """It already has its own section with its own priority rule; a second
    appearance as a keyword row is the fusion rule 4 forbids."""
    ranked = [
        NarrativeSearchResult(narrative_id="nar_task", similarity_score=0.9,
                              rank=1, raw_score=9.0),
        NarrativeSearchResult(narrative_id="nar_other", similarity_score=0.5,
                              rank=2, raw_score=1.0),
    ]

    menu = routing_gate.pick_menu(ranked, exclude_ids={"nar_task"}, limit=3)

    assert [r.narrative_id for r in menu] == ["nar_other"]


def test_rule5_a_zero_scoring_candidate_never_reaches_the_menu():
    """Zero BM25 score means zero term overlap: the row carries no evidence for
    switching, which is the only thing the menu is for."""
    ranked = [
        NarrativeSearchResult(narrative_id="nar_a", similarity_score=0.5,
                              rank=1, raw_score=2.0),
        NarrativeSearchResult(narrative_id="nar_zero", similarity_score=0.0,
                              rank=2, raw_score=0.0),
    ]

    menu = routing_gate.pick_menu(ranked, exclude_ids=set(), limit=3)

    assert [r.narrative_id for r in menu] == ["nar_a"]


def test_rule5_the_menu_is_capped_at_the_configured_size():
    ranked = [
        NarrativeSearchResult(narrative_id=f"nar_{i}", similarity_score=0.5,
                              rank=i + 1, raw_score=9.0 - i)
        for i in range(6)
    ]

    menu = routing_gate.pick_menu(ranked, exclude_ids=set(), limit=3)

    assert [r.narrative_id for r in menu] == ["nar_0", "nar_1", "nar_2"]


# ===================================================================== #
# The input budget · one anchor test per line of the table              #
# ===================================================================== #


def test_budget_previous_response_is_head_clamped_and_reported():
    long_reply = "开头很重要:先看第一个" + ("x" * 9000)
    prompt = build_merged_prompt(_input(previous_response=long_reply))

    assert "开头很重要:先看第一个" in prompt.user_input, (
        "head-preserving: the referent of a follow-up lives at the START"
    )
    assert "x" * 9000 not in prompt.user_input
    assert "prev_response" in prompt.truncated


def test_budget_previous_query_inherits_the_current_message_cap():
    long_query = "上一轮的问题开头" + ("y" * 9000)
    prompt = build_merged_prompt(_input(previous_query=long_query))

    assert "上一轮的问题开头" in prompt.user_input
    assert "y" * 9000 not in prompt.user_input
    assert "prev_query" in prompt.truncated


def test_budget_anchor_summary_is_clamped_even_though_it_should_be_short():
    """`current_summary` has a soft upper bound in the updater prompt and no
    hard one anywhere — a verbose model walks straight through it, and the
    anchor block is rendered on EVERY merged turn."""
    prompt = build_merged_prompt(
        _input(
            anchor=_narrative(
                "nar_anchor", name="部署脚本报错排查",
                summary="摘要开头" + ("z" * 9000),
            )
        )
    )

    assert "摘要开头" in prompt.user_input
    assert "z" * 9000 not in prompt.user_input
    assert "anchor_summary" in prompt.truncated


def test_budget_awareness_is_clamped():
    prompt = build_merged_prompt(_input(awareness="人设开头" + ("w" * 9000)))

    assert "人设开头" in prompt.user_input
    assert "w" * 9000 not in prompt.user_input
    assert "awareness" in prompt.truncated


def test_budget_current_message_is_clamped_loosely():
    prompt = build_merged_prompt(_input(query="本轮开头" + ("q" * 9000)))

    assert "本轮开头" in prompt.user_input
    assert "q" * 9000 not in prompt.user_input
    assert "query" in prompt.truncated


def test_budget_nothing_is_reported_truncated_when_nothing_was():
    prompt = build_merged_prompt(_input())
    assert prompt.truncated == ()


def test_budget_caps_are_config_knobs_not_literals():
    for name in (
        "MERGED_PREV_RESPONSE_MAX_CHARS",
        "MERGED_QUERY_MAX_CHARS",
        "MERGED_ANCHOR_SUMMARY_MAX_CHARS",
        "MERGED_AWARENESS_MAX_CHARS",
        "MERGED_PARTICIPANT_MAX_CANDIDATES",
    ):
        assert getattr(config, name) > 0, f"{name} missing from NarrativeConfig"


def test_the_prompt_reports_its_own_size():
    """The x-axis of the latency model. It must count EVERYTHING the call
    sends: the instructions vary by variant, and the variant correlates with
    the turn shape being measured — a user_input-only count would bias the
    slope, not just offset it (review round 2, I2)."""
    prompt = build_merged_prompt(_input())
    assert prompt.input_chars == len(prompt.instructions) + len(prompt.user_input)


def test_the_menu_shows_evidence_not_a_cross_pool_score():
    """`similarity_score` is `raw/(raw+1)` over a per-pool IDF table, so 0.87
    means nothing outside its own pool — and the discipline that keeps the
    shutter off the total score keeps it out of the merged menu too. WHICH
    terms matched is the part that transfers."""
    prompt = build_merged_prompt(_input())

    assert "Matched terms" in prompt.user_input
    assert "0.87" not in prompt.user_input


# ===================================================================== #
# Prompt pairing discipline — the fork that already happened 3 times     #
# ===================================================================== #

#: All four composed variants (continuable × participant), by builder args —
#: the review's trap note: variants multiply, literals fork; parametrize the
#: BUILDER, never a list of module constants.
_MERGED_VARIANTS = tuple(
    dict(anchor_is_continuable=cont, with_participants=part, with_menu=menu)
    for cont in (True, False)
    for part in (True, False)
    for menu in (True, False)
)


def _variant_id(kwargs):
    return (
        f"cont={kwargs['anchor_is_continuable']}"
        f"-part={kwargs['with_participants']}"
        f"-menu={kwargs['with_menu']}"
    )


@pytest.mark.parametrize("kwargs", _MERGED_VARIANTS, ids=_variant_id)
def test_every_variant_shares_the_no_durable_topic_rubric(kwargs):
    """One constant, spliced into all — the fix that ended the third silent
    fork (PR #361 round 2, I2). The merged prompts join that arrangement on day
    one instead of earning their own fork first."""
    assert prompts._NO_DURABLE_TOPIC_RUBRIC in prompts_merged.build_merged_instructions(**kwargs)


@pytest.mark.parametrize("kwargs", _MERGED_VARIANTS, ids=_variant_id)
def test_every_variant_shares_the_routing_core(kwargs):
    assert prompts_merged._MERGED_ROUTING_CORE in prompts_merged.build_merged_instructions(**kwargs)


@pytest.mark.parametrize("kwargs", _MERGED_VARIANTS, ids=_variant_id)
def test_the_asymmetry_rule_exists_exactly_when_there_is_an_anchor(kwargs):
    """§3.2 in prompt form: BM25 can CONFIRM a continuation, never veto one —
    but only a turn WITH a continuable anchor may be told "staying is the
    default" (review round 2, C1: the sentence used to be unconditional, and
    on anchorless/bucket turns it invited the one verdict the contract
    refuses). "Necessary but not sufficient" is anchor-independent and stays
    in every variant."""
    flat = " ".join(prompts_merged.build_merged_instructions(**kwargs).split())
    continuable = kwargs["anchor_is_continuable"]
    assert ("Staying on that thread is the DEFAULT" in flat) == continuable
    assert ("evidence for LEAVING" in flat) == continuable
    assert ("No thread to stay on" in flat) == (not continuable)
    assert "necessary but not sufficient" in flat


@pytest.mark.parametrize("kwargs", _MERGED_VARIANTS, ids=_variant_id)
def test_every_variant_carries_the_continuity_only_criteria(kwargs):
    """The continuity tier is being replaced as a DECIDER, so the things only
    its prompt said have to survive somewhere: business-intent granularity and
    a follow-up to the agent's own answer live in the shared core. The legacy
    container rule moved OUT of the core (review Critical 1: its "the thread
    continues even there" invited the one verdict the contract refuses) and
    lives in ANCHOR_NOT_CONTINUABLE_NOTE, rendered exactly when it applies."""
    flat = " ".join(prompts_merged.build_merged_instructions(**kwargs).split())
    assert "business intent" in flat
    # The follow-up-to-the-agent's-reply rule presupposes a thread to continue;
    # the anchorless fragment carries its own elliptical-reading line instead.
    if kwargs["anchor_is_continuable"]:
        assert "the Agent's own reply" in flat
    else:
        assert "elliptical message" in flat
    assert "legacy container" in merged_router.ANCHOR_NOT_CONTINUABLE_NOTE


@pytest.mark.parametrize("kwargs", _MERGED_VARIANTS, ids=_variant_id)
def test_every_variant_dropped_the_eight_category_names(kwargs):
    """2026-08-21: the taxonomy was teaching classify-and-dump. A new prompt is
    exactly where it would come back."""
    text = prompts_merged.build_merged_instructions(**kwargs)
    for category in (
        "GreetingAndCourtesy", "CasualChatOrEmotion", "JokeAndEntertainment",
        "AgentHelpAndCapability", "AgentPersonaConfiguration", "TaskLookup",
        "GeneralOneShotQuestion", "UnclassifiedOrGarbage",
    ):
        assert category not in text, f"variant {kwargs} still carries {category}"


@pytest.mark.parametrize("kwargs", _MERGED_VARIANTS, ids=_variant_id)
def test_every_variant_offers_exactly_what_the_contract_accepts(kwargs):
    """The answer table and the priority list are derived from the same
    selection `_contract_violation` enforces — a verdict is in the prose iff
    it is landable on that turn."""
    text = prompts_merged.build_merged_instructions(**kwargs)
    assert (merged_router.VERDICT_CONTINUE_ANCHOR in text) == kwargs[
        "anchor_is_continuable"
    ]
    assert (merged_router.VERDICT_PARTICIPANT in text) == kwargs[
        "with_participants"
    ]
    # `match` is conditional too (round 6, I3): an empty menu offers no index
    # to give, so listing it invited a guaranteed contract refusal. The bare
    # word "match" occurs in unrelated prose ("terms matched", "match_index"),
    # so the pin reads the answer-table bullet and the output-format verdict
    # list — the two places an OFFER actually lives.
    assert ("- match —" in text) == kwargs["with_menu"]
    assert ('"match"' in text) == kwargs["with_menu"]
    for always in (
        merged_router.VERDICT_NEW,
        merged_router.VERDICT_NO_TOPIC,
    ):
        assert always in text


def test_only_the_participant_variant_offers_the_participant_verdict():
    assert merged_router.VERDICT_PARTICIPANT in prompts_merged.build_merged_instructions(
        anchor_is_continuable=True, with_participants=True
    )
    assert merged_router.VERDICT_PARTICIPANT not in prompts_merged.build_merged_instructions(
        anchor_is_continuable=True, with_participants=False
    ), "offering a verdict with no candidates behind it invites an invalid index"


# ===================================================================== #
# The merged path's own deviations from the shared defaults              #
# (the defaults themselves are pinned in test_routing_blocks.py)         #
# ===================================================================== #


def test_the_merged_anchor_block_drops_the_bucket_note():
    """Under merged routing the bucket flag is asserted OFF, so a bucket can
    never occupy the anchor slot — carrying continuity's note about it would
    leave an inert instruction in a prompt that contradicts the continue rule
    a few lines above it."""
    prompt = build_merged_prompt(_input())
    assert "its boundaries are very strict" not in prompt.user_input
