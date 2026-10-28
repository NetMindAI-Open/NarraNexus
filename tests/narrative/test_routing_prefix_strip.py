"""
@file_name: test_routing_prefix_strip.py
@date: 2026-08-20
@description: The channel routing prefix "[From <name>]" must not be BM25
evidence, and must never end up inside a narrative's name.

WHY (prod, 2026-08-14..20, 26,922 routing-audit rows)

96% of prod queries arrive as ``[From <name>] <body>`` — `build_channel_anchor`
puts the sender there on purpose, so the judge and the continuity tier can see
who is talking. BM25 then scores those two tokens like topic evidence:

  tokenize('[From Liam] 👊')  ->  ['from', 'liam']      # the emoji tokenises to nothing

Audit row 768 is that query, scored 5.66 — **100% of it from `from`+`liam`**
(51% / 49%) — which cleared RAW_FLOOR=3.0. Across the whole probe, 30.5% of
prefix-carrying decisions drop to a top1 of ZERO once the prefix is removed:
the routing metadata WAS the entire match.

And it is self-reinforcing. `create_from_query` names a new narrative after the
query and `updater._apply_llm_update` lets the helper LLM rewrite that name from
event text, so prod now carries narratives literally called
``[From U082541Q6AX] stop gre...`` / ``[From o9cq8001z5NQ4n4H2VdvK...`` /
``[From Liam] * 👊 刚甩过去...``. Every later message from that channel matches
its own line's name on the prefix, at a low in-pool df — audit row 1492 reached
margin **357.79** and skipped the judge entirely. The prefix builds the magnet
and then flies into it.

Scope note: only the LEADING prefix is stripped. `message_bus` joins several
messages, each line prefixed, and those interior prefixes are ~1% of a 250-term
bus query whose evidence is broad and healthy (max-term share p50 0.03). Widening
the strip there would lower correct scores for no measured gain — see
`specs/2026-08-20-bm25-gate-redesign-research.md` §R2.1.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from narranexus.platform.channel.channel_context_builder_base import build_channel_anchor
from narranexus.platform.narrative._narrative_impl.retrieval import NarrativeRetrieval
from narranexus.platform.utils.text import strip_routing_prefix


# ---------------- the helper ------------------------------------------------


def test_strips_the_channel_anchor_prefix() -> None:
    assert strip_routing_prefix("[From Liam] 👊") == "👊"
    assert strip_routing_prefix("[From Unknown] 你好啊 你还在么") == "你好啊 你还在么"


def test_strips_the_message_bus_and_im_prefix_shapes() -> None:
    """The three shapes prod actually emits, verbatim from the audit table."""
    assert strip_routing_prefix("[From agent agent_9581c50f66c4] 他们俩都在群里") == "他们俩都在群里"
    assert (
        strip_routing_prefix("[From o9cq80-F87S_NQbA3CEUH55tqrJE@im.wechat] 帮我提取信息")
        == "帮我提取信息"
    )
    assert (
        strip_routing_prefix("[From @sp-ae65b8e16c8d42aa:matrix-test.netmind.chat] 哦")
        == "哦"
    )


def test_is_the_inverse_of_build_channel_anchor() -> None:
    """Anti-drift: the two functions must stay a matched pair.

    `build_channel_anchor` owns the format. If someone changes it, this
    assertion is what tells them the stripper no longer recognises it — the
    alternative is a silent return to scoring sender names.
    """
    for name, body in (
        ("Liam", "👊"),
        ("Chengyu Huang", "盘点一下今天"),
        (None, "anonymous sender falls back to Unknown"),
        ("U082541Q6AX", "summarize this channel"),
    ):
        assert strip_routing_prefix(build_channel_anchor(name, body)) == body


def test_leaves_a_plain_query_untouched() -> None:
    assert strip_routing_prefix("帮我搜索最近的 AI 新闻并整理成简报") == "帮我搜索最近的 AI 新闻并整理成简报"
    assert strip_routing_prefix("hi") == "hi"
    assert strip_routing_prefix("") == ""
    assert strip_routing_prefix(None) == ""


def test_only_the_leading_prefix_goes() -> None:
    """Interior prefixes are message_bus's multi-message join — see module docstring."""
    joined = "[From agent a1] first line\n[From agent a2] second line"
    assert strip_routing_prefix(joined) == "first line\n[From agent a2] second line"


def test_a_bracket_that_is_not_a_routing_prefix_stays() -> None:
    assert strip_routing_prefix("[Fromage] cheese") == "[Fromage] cheese"
    assert strip_routing_prefix("[TODO] fix the gate") == "[TODO] fix the gate"


# ---------------- the magnet, end to end ------------------------------------


def _pool_with_a_liam_magnet():
    """The prod shape: one line whose NAME carries the sender, one that does not."""
    return [
        ("nar_magnet", "NarraMessenger chat with Liam on AI monitoring", False),
        ("nar_other", "部署脚本报错排查 deployment script failure", False),
    ]


def test_a_prefix_only_message_produces_no_bm25_evidence() -> None:
    """`[From Liam] 👊` must score nothing — the body has no tokens at all.

    Prod audit 768/846/860/953: this exact query scored 5.66 / 3.67 / 3.35 /
    2.41 as the pool grew, and at pool<=26 it cleared the floor. The only
    honest score for "a fist-bump emoji" is no score, which routes the turn to
    the judge.
    """
    ranked = NarrativeRetrieval.rank_pool("[From Liam] 👊", _pool_with_a_liam_magnet(), 3)
    assert ranked == []


def test_the_sender_name_no_longer_beats_the_topic() -> None:
    """A real question must outrank the line that merely shares the sender name."""
    ranked = NarrativeRetrieval.rank_pool(
        "[From Liam] 部署脚本报错了怎么排查", _pool_with_a_liam_magnet(), 3
    )
    assert ranked, "a real question must still match something"
    assert ranked[0].narrative_id == "nar_other"


def test_the_body_still_scores_after_the_prefix_is_removed() -> None:
    """Sanity guard against over-stripping: stripping must not blank the query."""
    ranked = NarrativeRetrieval.rank_pool(
        "[From Liam] AI monitoring", _pool_with_a_liam_magnet(), 3
    )
    assert ranked and ranked[0].narrative_id == "nar_magnet"


# ---------------- never build the magnet ------------------------------------


@pytest.mark.asyncio
async def test_a_new_narrative_is_never_named_after_the_routing_prefix() -> None:
    """`create_from_query` must not carry the prefix into name or description.

    Prod carries at least four such magnet lines today (audit 2, 26491, 1492,
    3277). Existing rows are the Owner's 9080 cleanup batch; this test is about
    never minting a fifth.
    """
    retrieval = NarrativeRetrieval.__new__(NarrativeRetrieval)
    captured = {}

    async def _create(**kwargs):
        captured.update(kwargs)
        from tests.narrative.conftest import _narrative  # reuse the real model

        return _narrative("nar_new", name=kwargs["title"])

    retrieval._crud = SimpleNamespace(create=_create, save=AsyncMock())

    await retrieval.create_from_query(
        query="[From o9cq8001z5NQ4n4H2VdvKHsTWc7w@im.wechat] 你好",
        user_id="user_x",
        agent_id="agent_x",
        narrative_type=__import__(
            "narranexus.platform.narrative.models", fromlist=["NarrativeType"]
        ).NarrativeType.CHAT,
    )

    assert "[From " not in captured["title"], captured["title"]
    assert "[From " not in captured["description"], captured["description"]
    assert captured["title"].strip(), "stripping must not leave an empty name"


@pytest.mark.asyncio
async def test_the_updater_strips_the_routing_prefix_from_the_llm_name() -> None:
    """The helper LLM copies event text into the name; audit 1492 is the result.

    `[From Liam] * 👊 刚甩过去，就在你问的同...` became a narrative name, and
    the next `[From Liam] 刚才你` matched it on `刚` at margin 357.79.
    """
    from narranexus.platform.narrative._narrative_impl.updater import NarrativeUpdater

    from tests.narrative.conftest import _narrative

    latest = _narrative("nar_1", name="old name")
    saved: list = []

    updater = NarrativeUpdater.__new__(NarrativeUpdater)
    updater._crud = SimpleNamespace(
        load_by_id=AsyncMock(return_value=latest),
        save=AsyncMock(side_effect=lambda n: saved.append(n)),
    )

    update_output = SimpleNamespace(
        name="[From Liam] * 👊 刚甩过去，就在你问的同时",
        current_summary="summary",
        topic_keywords=["from", "liam"],
        dynamic_summary_entry="",
    )

    await updater._apply_llm_update(latest, update_output, event=SimpleNamespace())

    assert saved, "the update must still be persisted"
    assert "[From " not in saved[0].narrative_info.name, saved[0].narrative_info.name
    assert saved[0].narrative_info.name.strip()
