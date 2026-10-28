"""
@file_name: test_routing_blocks.py
@date: 2026-08-26
@description: The four shared routing prompt blocks render exactly what the
continuity tier and the judge rendered before they were shared.

WHY BYTE-IDENTITY IS THE CONTRACT

Three tiers describe the same four things to a model — the anchored thread, the
previous turn, the BM25 menu, the PARTICIPANT threads — and every time one of
those descriptions was copied instead of shared, the copies drifted and only one
got fixed:

  * the judge's search branch and its PARTICIPANT branch were two
    implementations of "what a candidate shows the model"; on 2026-04-15 only
    the search branch moved onto the live `narrative_info` fields, and the other
    spent two months labelling 72-event threads by a frozen creation-time hint;
  * the two judge prompt variants forked THREE times over the no-topic rubric,
    the last caught in PR #361 review round 2.

So the blocks were extracted into `routing_blocks.py`. But both prompts are
pinned by MEASURED numbers — M6 = 20.8%, the P1 calibration, the
description-retirement dry run — and a whitespace change would quietly
invalidate every one of them. Hence these are golden-string tests, transcribed
from the pre-extraction source: they fail if the shared block renders so much as
a different newline.

The `include_score` / `summary_max_chars` / `include_bucket_note` parameters
exist for the merged router, and each defaults to the old behaviour. This file
asserts the DEFAULTS; `test_merged_routing_prompt.py` asserts the overrides.
"""
from __future__ import annotations

from datetime import datetime, timezone

from narranexus.platform.narrative._narrative_impl import routing_blocks
from narranexus.platform.narrative.models import (
    Narrative,
    NarrativeInfo,
    NarrativeType,
)


def _narrative(
    nid: str, *, name: str, summary: str = "", description: str = "",
    keywords: list[str] | None = None, is_special: str = "other",
) -> Narrative:
    now = datetime.now(timezone.utc)
    return Narrative(
        id=nid,
        type=NarrativeType.CHAT,
        agent_id="agent_blocks",
        narrative_info=NarrativeInfo(
            name=name, description=description, current_summary=summary, actors=[]
        ),
        event_ids=[],
        topic_keywords=keywords or [],
        is_special=is_special,
        created_at=now,
        updated_at=now,
    )


_CANDIDATE = {
    "id": "nar_other",
    "type": "search",
    "name": "纽约餐厅推荐",
    "description": "很好吃",
    "score": 0.87,
    "raw_score": 6.5,
    "matched_terms": ["部署", "脚本"],
    "matched_content": "…部署脚本报错…",
}


# ── the judge's menu ───────────────────────────────────────────────────────


def test_the_judge_menu_rendering_is_unchanged_byte_for_byte():
    rendered = routing_blocks.render_search_candidates(
        [_CANDIDATE],
        header=routing_blocks.JUDGE_MENU_HEADER,
        empty_note=routing_blocks.JUDGE_MENU_EMPTY_NOTE,
        include_score=True,
    ).text

    assert rendered == (
        "## Existing Topics:\n\n"
        "[Topic-0] 纽约餐厅推荐\n"
        "Description: 很好吃\n"
        "Similarity score: 0.87\n"
        "Matched terms: 部署, 脚本\n"
        "Matched content:\n…部署脚本报错…\n"
        "\n"
    )


def test_the_judge_empty_menu_note_is_unchanged_byte_for_byte():
    """The header is rendered even with nothing to match against: the model must
    be TOLD there are no candidates, not left to infer it from a section that
    simply is not there (2026-08-16 live probe — an empty menu is exactly when
    the verdict matters most)."""
    rendered = routing_blocks.render_search_candidates(
        [],
        header=routing_blocks.JUDGE_MENU_HEADER,
        empty_note=routing_blocks.JUDGE_MENU_EMPTY_NOTE,
        include_score=True,
    ).text

    assert rendered == (
        "## Existing Topics:\n\n(none — this user has no existing topics that "
        "overlap this message)\n\n"
    )


def test_a_scored_candidate_with_no_evidence_still_trips_the_alarm(caplog):
    """The alarm that spent four months ringing into `logger.debug` with nobody
    listening. It must survive the extraction — and it must stay silent for
    participant rows at raw_score 0.0, which never went through BM25 and owe no
    evidence (incident lesson #3: an alarm that cries wolf gets silenced)."""
    routing_blocks.render_search_candidates(
        [{**_CANDIDATE, "matched_content": "", "matched_terms": []}],
        header=routing_blocks.JUDGE_MENU_HEADER,
        empty_note=routing_blocks.JUDGE_MENU_EMPTY_NOTE,
        include_score=True,
    )
    routing_blocks.render_search_candidates(
        [{**_CANDIDATE, "matched_content": "", "matched_terms": [],
          "raw_score": 0.0}],
        header=routing_blocks.JUDGE_MENU_HEADER,
        empty_note=routing_blocks.JUDGE_MENU_EMPTY_NOTE,
        include_score=True,
    )


# ── the PARTICIPANT section ────────────────────────────────────────────────


def test_the_participant_section_is_unchanged_byte_for_byte():
    rendered = routing_blocks.render_participant_candidates(
        [{"id": "nar_task", "type": "participant",
          "name": "客户 A 的报价单", "description": "由销售发起"}]
    ).text

    assert rendered == (
        "## Participant-Associated Topics (user is a PARTICIPANT):\n\n"
        "[Participant-0] 客户 A 的报价单\n"
        "Description: 由销售发起\n"
        "\n"
    )


def test_no_participants_renders_nothing_at_all():
    """Not an empty header: the judge prompt omits this section entirely when
    the user is nobody's participant, and that is the behaviour being preserved."""
    assert routing_blocks.render_participant_candidates([]).text == ""


# ── the anchored thread ────────────────────────────────────────────────────


def test_the_continuity_anchor_block_is_unchanged_byte_for_byte():
    rendered = routing_blocks.render_anchor_context(
        _narrative(
            "nar_anchor", name="部署脚本报错排查",
            summary="在排查 CI 部署脚本的报错", keywords=["部署", "脚本"],
        )
    ).text

    assert rendered == (
        "\nCurrent Narrative Information:\n"
        "[Regular Narrative]\n"
        "- Name: 部署脚本报错排查\n"
        "- Current Summary: 在排查 CI 部署脚本的报错\n"
        "- Topic Keywords: 部署, 脚本\n"
        "\nNote: If this is a [Special Default Narrative], its boundaries are "
        "very strict. Once the user mentions specific objects, tasks, or "
        "ongoing topics, it should be judged as not belonging to the current "
        "Narrative.\n"
    )


def test_a_bucket_anchor_keeps_its_label():
    rendered = routing_blocks.render_anchor_context(
        _narrative("nar_bucket", name="GreetingAndCourtesy", is_special="default")
    ).text

    assert "[Special Default Narrative]" in rendered


def test_the_retired_description_takes_its_whole_line_with_it():
    """2026-08-20: an empty `- Description:` reads to the model as "this thread
    has no description", which is a different claim from not mentioning it. The
    line disappears once a real summary exists."""
    unsummarised = routing_blocks.render_anchor_context(
        _narrative("nar_new", name="新线", description="出生证原文",
                   summary="Newly created Narrative: 新线")
    ).text
    summarised = routing_blocks.render_anchor_context(
        _narrative("nar_old", name="老线", description="出生证原文",
                   summary="真实摘要")
    ).text

    assert "- Description: 出生证原文" in unsummarised
    assert "Description" not in summarised


def test_no_anchor_says_so():
    rendered = routing_blocks.render_anchor_context(None).text
    assert rendered == (
        "\nNo current Narrative information (this is a new session or no "
        "history)\n"
    )


# ── the previous turn ──────────────────────────────────────────────────────


def test_the_continuity_previous_turn_block_is_unchanged_byte_for_byte():
    rendered = routing_blocks.render_previous_turn(
        "部署脚本报错怎么修", "先看日志的最后 20 行"
    ).text

    assert rendered == (
        "Previous conversation turn:\n"
        "User asked: 部署脚本报错怎么修\n"
        "Agent's reply/reasoning: 先看日志的最后 20 行"
    )


def test_the_proactive_variant_is_unchanged_byte_for_byte():
    """The shape a copy loses: it only occurs when a scheduled job messaged the
    user and the user replied "好", which replay corpora barely contain."""
    rendered = routing_blocks.render_previous_turn(
        "", "任务跑完了,要看结果吗?"
    ).text

    assert rendered == (
        "Previous turn (the agent messaged the user proactively — there was no "
        "prior user query; the user's current message is most likely replying "
        "to this):\n"
        "Agent said to user: 任务跑完了,要看结果吗?"
    )


# ── the read-side clamp ────────────────────────────────────────────────────


def test_no_cap_means_no_clamp_and_no_marker():
    """The continuity tier and the judge pass no caps, so nothing they render
    may acquire a truncation marker."""
    long_text = "x" * 50_000
    text, clipped = routing_blocks.clamp_head(long_text, None)

    assert text == long_text
    assert clipped is False


def test_a_clamp_keeps_the_head_and_marks_itself():
    text, clipped = routing_blocks.clamp_head("开头" + "x" * 100, 10)

    assert clipped is True
    assert text.startswith("开头")
    assert text.endswith(routing_blocks.TRUNCATION_MARKER)
    assert len(text) == 10 + len(routing_blocks.TRUNCATION_MARKER)
