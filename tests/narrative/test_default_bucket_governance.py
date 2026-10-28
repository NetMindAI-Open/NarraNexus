"""
@file_name: test_default_bucket_governance.py
@date: 2026-08-16
@description: RED-first tests for the default-bucket governance P0 batch.

The batch is four changes that only make sense together (spec
`2026-08-14-default-bucket-governance-design.md`):

  4  default narratives stop being routing CONTAINERS (out of the BM25 pool,
     out of the judge's candidate menu, no longer seeded). The eight category
     names were initially kept as prompt VOCABULARY; 2026-08-21 live testing
     showed the taxonomy teaching classify-and-dump, so they are gone from
     every prompt (see test_judge_instructions_dropped_the_eight_category_names).
  4' the "no durable topic" verdict lands anchor-first: reuse the session's
     real thread without touching its retrieval surface; create when there is
     no anchor. (A third ephemeral/run-bare branch was removed on review
     2026-08-21 — unreachable in production; see _land_no_topic_turn.)
  5  continuity may never hold a turn on a default bucket.
  C2 the continuity prompt stops deciding by container type.

Every assertion here is about behaviour a user or an audit row can observe,
not about internal call shapes — a rewrite of the impl that keeps these
green is a rewrite we want to allow.
"""
from __future__ import annotations

import pytest

from narranexus.platform.narrative.config import config
from narranexus.platform.narrative._narrative_impl import prompts


# ===================================================================== #
# 4 · the switch                                                        #
# ===================================================================== #


def test_bucket_switch_defaults_to_off():
    """Buckets are OFF by default; the knob exists so a run can flip back.

    Default False (not True) because the whole batch ships enabled — the
    switch is for same-harness before/after comparison and for a one-line
    rollback, not for a staged opt-in.
    """
    assert config.NARRATIVE_DEFAULT_BUCKETS_ENABLED is False


# ===================================================================== #
# 4 · buckets leave the BM25 pool                                       #
# ===================================================================== #


@pytest.mark.asyncio
async def test_pool_excludes_default_buckets(retrieval_with_pool):
    """Buckets must not sit in the pool: BM25 computes IDF/avgdl over the set
    it is handed, so eight semantically-empty documents move real scores
    (measured: 9.7% of top-1 on 452 replayed queries)."""
    retrieval, _ = retrieval_with_pool
    pool = await retrieval.load_pool("agent_x", "user_x")

    assert pool, "pool must still contain the real narratives"
    assert all(not is_default for _, _, is_default in pool)
    assert {nid for nid, _, _ in pool} == {"nar_real"}


@pytest.mark.asyncio
async def test_pool_keeps_buckets_when_switch_is_on(retrieval_with_pool, monkeypatch):
    """The rollback path must actually roll back."""
    monkeypatch.setattr(config, "NARRATIVE_DEFAULT_BUCKETS_ENABLED", True)
    retrieval, _ = retrieval_with_pool
    pool = await retrieval.load_pool("agent_x", "user_x")

    assert any(is_default for _, _, is_default in pool)


# ===================================================================== #
# 4 · seeding stops                                                     #
# ===================================================================== #


@pytest.mark.asyncio
async def test_seeding_is_skipped(retrieval_with_pool):
    """No new (agent,user) pair should acquire eight empty containers.

    Existing rows stay where they are (binding rule #6) — this asserts we
    stop CREATING, not that we delete.
    """
    retrieval, spy = retrieval_with_pool
    await retrieval._ensure_default_narratives("agent_new", "user_new")

    assert spy.seeded == []


# ===================================================================== #
# 4 · the judge menu loses the containers, keeps the vocabulary          #
# ===================================================================== #


def test_judge_instructions_dropped_the_eight_category_names():
    """Reversal of the earlier keep-the-vocabulary pin, on live evidence.

    The vocabulary was kept in P0 as recognition scaffolding ("DESCRIPTIONS,
    not destinations"). 2026-08-21 hand-testing showed the judge reasoning
    "根据分类规则…归为 GeneralOneShotQuestion" — it still classifies into the
    taxonomy and maps category-hit to no_topic (3/7 turns dumped, and one
    dump seeded the identity-wash hijack in
    todo/2026-08-21-frozen-anchor-identity-wash-hijack.md). The list was
    teaching the disease, so it goes — from BOTH judge prompts AND the
    continuity prompt (agent_846942113533 turn 3: continuity saw the agent's
    own answer being followed up and still switched, steered by the
    taxonomy's "switch once specific content is involved").
    """
    for prompt_name in (
        "NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS",
        "NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS",
        "CONTINUITY_DETECTION_INSTRUCTIONS",
    ):
        text = getattr(prompts, prompt_name)
        for name in (
            "GreetingAndCourtesy",
            "CasualChatOrEmotion",
            "JokeAndEntertainment",
            "AgentHelpAndCapability",
            "AgentPersonaConfiguration",
            "TaskLookup",
            "GeneralOneShotQuestion",
            "UnclassifiedOrGarbage",
        ):
            assert name not in text, (
                f"{prompt_name} still carries taxonomy word {name}"
            )


def test_judge_instructions_carry_the_p1_no_topic_narrowing():
    """P1 calibration (2026-08-19): the two rules that bound NO_TOPIC.

    The after-run measured M6 = 20.8% (11/53) — the judge was dumping
    substantive turns into `no_durable_topic` once the buckets stopped being
    available as a residual category (cluster-first "none" went 21.4% -> 94.7%).
    These two sentences are the whole calibration; if either is edited away the
    M6 number stops meaning what it meant when it was measured.

    Pre-registration: `data/replay_runs/2026-08-19/P1_CALIBRATION_PREREGISTRATION.md`

    2026-08-25 (PR #361 round 2, I2): asserted on BOTH judge prompt variants —
    the participant variant had silently missed the whole calibration (third
    fork between the pair), so the rubric is now one shared constant and
    these anchors loop.
    """
    for text in (prompts.NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS,
                 prompts.NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS):
        flat = " ".join(text.split())
        assert "is NEW, never NO_TOPIC" in flat
        assert "Never prefer NO_TOPIC merely to avoid creating a topic" in flat


def test_judge_instructions_carry_the_three_trap_counterexamples():
    """The three shapes the 08-19 survey found the judge losing turns on:
    a polite opener wrapping a request, a bare imperative, and a rule set for
    the future. Each is stated as a counter-example because the abstract rule
    above it demonstrably did not transfer on its own. Looped over both
    variants since the rubric became a shared constant (round 2, I2)."""
    for text in (prompts.NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS,
                 prompts.NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS):
        assert "polite opener" in text.lower()
        assert "bare imperative" in text.lower()
        assert "from now on" in text.lower()


def test_no_topic_boundary_is_request_based_not_taxonomy_based():
    """The boundary clause, rewritten with the taxonomy removed (2026-08-21).

    The old clause exempted agent-questions, persona play and one-shot trivia
    by pointing back at shapes 4/5/7 of the vocabulary — and live testing
    showed exactly those exemptions swallowing a capability question and a
    news-search request (m6_live_specimens.md, turns 5/6). The new boundary
    is request-based: no_topic is ONLY for messages that request nothing and
    refer to nothing; any nameable request carries a topic. Ties break toward
    NEW, because a thin new thread is recoverable while a frozen misfiled
    turn feeds the identity-wash hijack.
    """
    for text in (prompts.NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS,
                 prompts.NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS):
        flat = " ".join(text.split())
        assert "requests nothing and refers to nothing" in flat
        assert "prefer NEW over NO_TOPIC" in flat
        assert "USER'S OWN work" not in flat  # the old exemption frame is gone


def test_judge_instructions_no_longer_offer_buckets_as_a_target():
    """The verdict is a label, not a destination: the prompt must not tell
    the model it can return an index into a list of default topics."""
    text = prompts.NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS
    assert "no_durable_topic" in text
    assert 'matched_category = "default"' not in text


def test_participant_judge_prompt_does_not_offer_buckets_either():
    """The participant variant must not sell a destination that is gone.

    P0 removed the eight-bucket menu from NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS
    but left the participant variant untouched, so the IM-group-chat path was
    still telling the model it could return `matched_category = "default"` with
    an index into a list that is now always empty. The only symptom would have
    been an `out of range` warning and a silent fall-through to "no match" —
    and the replay corpus never exercises it, because PARTICIPANT is unused
    there (see the module docstring of the todo).
    """
    text = prompts.NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS
    assert 'matched_category = "default"' not in text
    assert "Default topic types:" not in text
    assert "Match a default topic type" not in text


def test_participant_judge_prompt_has_the_no_durable_topic_verdict():
    """Deleting the menu without giving the variant the verdict would push
    every greeting to "none" -> a brand new thread, which is the fragmentation
    the bucket removal is supposed to avoid. Same exit as the main variant."""
    text = prompts.NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS
    assert "no_durable_topic" in text


def test_both_judge_prompts_agree_on_the_bucket_question():
    """One question, one answer, whichever variant is chosen at
    `_retrieval_llm.py:89-91`. This is the test the P0 change should have had:
    it fails if EITHER prompt drifts back to offering buckets, so the next
    person cannot fix one and forget the other."""
    for name in (
        "NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS",
        "NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS",
    ):
        text = getattr(prompts, name)
        assert 'matched_category = "default"' not in text, f"{name} still offers buckets"
        assert "no_durable_topic" in text, f"{name} lacks the verdict"
        # 2026-08-21: the eight names are gone from BOTH prompts — the
        # taxonomy was teaching classify-and-dump (see
        # test_judge_instructions_dropped_the_eight_category_names).
        assert "GreetingAndCourtesy" not in text, f"{name} kept taxonomy words"


@pytest.mark.asyncio
async def test_judge_receives_no_default_candidates(judge_spy):
    """Menu shape: real threads only. Eight fixed entries with worked
    examples against at most three dynamic ones is a menu that answers
    itself."""
    retrieval, spy = judge_spy
    await retrieval._llm_unified_match(
        query="how do I use you?",
        search_results=[],
        agent_id="agent_x",
        user_id="user_x",
        top_k=3,
        narrative_type=None,
        best_score=None,
        audit=spy.audit,
    )
    assert spy.default_candidates == []


# ===================================================================== #
# 4' · the landing rule (anchor-first)                                  #
# ===================================================================== #


@pytest.mark.asyncio
async def test_no_topic_with_anchor_reuses_the_thread_without_touching_it(
    service_no_topic,
):
    """A greeting mid-task belongs to the task (annotation protocol R1) and
    must not cost that thread a surface rewrite.

    NARRATIVE_LLM_UPDATE_INTERVAL is 1, so letting the updater run here
    would spend one helper call per "你好" AND rewrite name/summary/keywords
    wholesale — a greeting could rename the work thread.
    """
    svc, spy = service_no_topic
    spy.anchor = "nar_real"

    result = await svc.select(
        agent_id="agent_x", user_id="user_x", input_content="你好",
        session=spy.session,
    )

    assert [n.id for n in result.narratives] == ["nar_real"]
    assert result.selection_method == "no_topic_anchored"
    assert result.no_durable_topic is True


@pytest.mark.asyncio
async def test_no_topic_without_anchor_on_durable_surface_creates(service_no_topic):
    """A first-contact turn must still land somewhere retrievable.

    Chat history endpoints are narrative-scoped and the ChatModule instance
    hangs off the narrative, so "run bare" on a durable surface would make
    the turn vanish from the user's own history. The created thread is not
    junk: it becomes the anchor and the updater renames it as the topic
    emerges.
    """
    svc, spy = service_no_topic
    spy.anchor = None

    result = await svc.select(
        agent_id="agent_x", user_id="user_x", input_content="你好",
        session=spy.session,
    )

    assert result.is_new is True
    assert result.narratives, "a durable surface must never run bare"


# ===================================================================== #
# 5 · continuity may not hold a turn on a bucket                        #
# ===================================================================== #


@pytest.mark.asyncio
async def test_continuity_never_locks_onto_a_bucket(service_continuity):
    """Measured: 59 of 155 bucket-resident turns in the replay were held
    there by continuity, chains up to 11 turns long. A bucket is a verdict,
    not a thread — there is nothing to continue."""
    svc, spy = service_continuity
    spy.anchor_narrative = spy.bucket

    result = await svc.select(
        agent_id="agent_x", user_id="user_x", input_content="接着刚才那个",
        session=spy.session,
    )

    assert result.selection_method != "continuous"
    assert spy.continuity_detector_called is False, (
        "with a bucket anchor there is nothing to continue — skip the "
        "helper round trip entirely"
    )


@pytest.mark.asyncio
async def test_continuity_still_works_on_a_real_thread(service_continuity):
    """5 must not become 'continuity is off'."""
    svc, spy = service_continuity
    spy.anchor_narrative = spy.real
    spy.continuity_verdict = True

    result = await svc.select(
        agent_id="agent_x", user_id="user_x", input_content="接着刚才那个",
        session=spy.session,
    )

    assert result.selection_method == "continuous"
    assert [n.id for n in result.narratives] == ["nar_real"]


# ===================================================================== #
# C-2 · the continuity prompt stops deciding by container type           #
# ===================================================================== #


def test_continuity_prompt_no_longer_forces_false_on_bucket_anchors():
    """`prompts.py:125` used to say: switching out of a default narrative
    MUST be judged as not belonging. Measured consequence: 21 of 34
    continuity misses sat on bucket anchors, five of them with the model's
    own reasoning conceding the topic was continuing.

    Together with 5 this closes the loop `bucket -> forced False -> re-route
    -> frozen surface scores 0 -> bucket again`.
    """
    text = prompts.CONTINUITY_DETECTION_INSTRUCTIONS
    assert "must be judged as not belonging" not in text.lower()
    assert "must judge as not belonging" not in text.lower()


def test_continuity_prompt_still_judges_topic_continuation():
    """Removing the container rule must leave the actual question intact."""
    text = prompts.CONTINUITY_DETECTION_INSTRUCTIONS
    assert "business intent" in text.lower()


# ===================================================================== #
# M6 · the verdict has to be recomputable from the DB                   #
# ===================================================================== #


@pytest.mark.asyncio
async def test_no_topic_verdict_reaches_the_audit_row(judge_spy):
    """M6 asks: of the turns the judge called "no durable topic", how many
    actually carried one? That is a golden-set recompute, and it can only be
    run if the verdict itself is on the row — the label is the join key.

    Pre-registered threshold is 10%; this test does not check the rate, it
    checks that the rate is *measurable at all*. Without it the batch ships
    with its one unvalidated assumption unobservable.
    """
    retrieval, spy = judge_spy
    await retrieval._llm_unified_match(
        query="早呀",
        search_results=[],
        agent_id="agent_x",
        user_id="user_x",
        top_k=3,
        narrative_type=None,
        best_score=None,
        audit=spy.audit,
    )

    assert spy.audit.judge_ran is True
    assert spy.audit.judge_category == "no_topic"
    assert spy.audit.judge_matched_id is None, (
        "a verdict names no destination — a non-null id here would mean the "
        "label silently became a container again"
    )
    assert spy.audit.judge_reason, "the reasoning is the sampling material"


@pytest.mark.asyncio
async def test_landing_choice_is_visible_in_the_audit_row(service_no_topic):
    """The three landings must be distinguishable in the DB, or the batch's
    biggest risk (fragmentation) cannot be read off prod: `new_created` on a
    no-topic turn is the one that costs a thread."""
    svc, spy = service_no_topic

    spy.anchor = "nar_real"
    anchored = await svc.select(
        agent_id="agent_x", user_id="user_x", input_content="你好",
        session=spy.session,
    )

    spy.anchor = None
    created = await svc.select(
        agent_id="agent_x", user_id="user_x", input_content="你好",
        session=spy.session,
    )

    assert anchored.selection_method == "no_topic_anchored"
    assert created.selection_method == "new_created"
